"""Tests for the Flex gripper capability (``flex.gripper``).

Drives ``OpentronsFlex.setup()`` with an injected ``ChatterboxTransport``
advertising a gripper on the extension mount, and asserts discovery attaches
a :class:`~pylabrobot.opentrons.flex_gripper.FlexGripper`; ``move_labware``
follows the stage -> wire -> commit idiom (PLR-side validation before any
wire command, deck re-parent only on wire success); and
``labware_moved_off_deck`` releases a slot both server-side and PLR-side.
"""

import asyncio
import unittest
from typing import Any, Dict, Optional, Tuple

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_gripper import FlexGripper
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.plate import Plate


def _flex_with_gripper(**transport_kwargs) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  """An ``OpentronsFlex`` whose transport advertises a gripper on the
  extension mount (plus a single-channel pipette so setup() succeeds),
  returning the transport too so a test can inspect recorded commands.

  ``transport_kwargs`` are forwarded to ``ChatterboxTransport``.
  """
  transport = ChatterboxTransport(
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")],
    gripper=True,
    **transport_kwargs,
  )
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  return flex, transport


def _plate(name: str = "plate") -> Plate:
  plate = cor_96_wellplate_360uL_Fb(name=name)
  plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
  return plate


class _FailingMoveTransport(ChatterboxTransport):
  """Chatterbox whose ``moveLabware`` commands fail at the robot: the command
  is accepted (recorded) but its status poll reports ``failed``, so
  ``_execute_command`` raises the way a real robot-server failure would.
  """

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = await super().post(path, json)
    data = (json or {}).get("data", {})
    if path.endswith("/commands") and data.get("commandType") == "moveLabware":
      cmd_data = result["data"]
      cmd_data["status"] = "failed"
      cmd_data["error"] = {"detail": "simulated gripper failure"}
    return result


class TestGripperDiscovery(unittest.TestCase):
  """setup() composes a FlexGripper iff /instruments reports a gripper."""

  def test_gripper_attached_when_advertised(self):
    flex, _transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      self.assertIsInstance(flex.gripper, FlexGripper)
      assert flex.gripper is not None
      self.assertEqual(flex.gripper.gripper_model, "gripperV1.3")
      # Head composition is unaffected by the extra instrument entry.
      self.assertIsNotNone(flex.right)
    finally:
      asyncio.run(flex.stop())

  def test_gripper_none_when_absent(self):
    transport = ChatterboxTransport(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")])
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
    asyncio.run(flex.setup())
    try:
      self.assertIsNone(flex.gripper)
    finally:
      asyncio.run(flex.stop())


class TestMoveLabware(unittest.TestCase):
  """move_labware sends ONE atomic moveLabware command and re-parents the
  deck only on wire success; the labware is JIT-loaded once."""

  def test_happy_path_sends_exact_wire_params_and_reparents_deck(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      gripper = flex.gripper
      assert gripper is not None

      asyncio.run(gripper.move_labware(plate, "C2"))

      move_cmds = [c for c in transport.commands if c["commandType"] == "moveLabware"]
      self.assertEqual(len(move_cmds), 1)
      labware_id = flex._loaded_labware["plate"]
      self.assertEqual(
        move_cmds[0]["params"],
        {
          "labwareId": labware_id,
          "newLocation": {"slotName": "C2"},
          "strategy": "usingGripper",
        },
      )

      self.assertEqual(flex.deck.get_slot(plate), "C2")
      self.assertIsNone(flex.deck.get_resource_at_slot("C1"))
      self.assertIs(flex.deck.get_resource_at_slot("C2"), plate)
    finally:
      asyncio.run(flex.stop())

  def test_second_move_reuses_the_loaded_labware(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      gripper = flex.gripper
      assert gripper is not None

      asyncio.run(gripper.move_labware(plate, "C2"))
      asyncio.run(gripper.move_labware(plate, "D3"))

      load_cmds = [c for c in transport.commands if c["commandType"] == "loadLabware"]
      move_cmds = [c for c in transport.commands if c["commandType"] == "moveLabware"]
      self.assertEqual(len(load_cmds), 1, "labware must be JIT-loaded exactly once")
      self.assertEqual(len(move_cmds), 2)
      self.assertEqual(flex.deck.get_slot(plate), "D3")
    finally:
      asyncio.run(flex.stop())

  def test_move_to_staging_slot(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      gripper = flex.gripper
      assert gripper is not None

      asyncio.run(gripper.move_labware(plate, "B4"))

      move_cmds = [c for c in transport.commands if c["commandType"] == "moveLabware"]
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(move_cmds[0]["params"]["newLocation"], {"slotName": "B4"})
      self.assertEqual(flex.deck.get_slot(plate), "B4")
    finally:
      asyncio.run(flex.stop())


class TestMoveLabwarePreWireRejections(unittest.TestCase):
  """Invalid moves raise OpentronsError BEFORE any wire command is sent."""

  def _assert_no_move_commands(self, transport: ChatterboxTransport) -> None:
    move_cmds = [c for c in transport.commands if c["commandType"] == "moveLabware"]
    self.assertEqual(len(move_cmds), 0, "no moveLabware wire command may be sent")

  def test_labware_not_on_deck_raises(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()  # never assigned to the deck
      gripper = flex.gripper
      assert gripper is not None

      with self.assertRaises(OpentronsError):
        asyncio.run(gripper.move_labware(plate, "C2"))

      self._assert_no_move_commands(transport)
    finally:
      asyncio.run(flex.stop())

  def test_occupied_destination_raises_and_deck_untouched(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      other = _plate(name="other")
      flex.deck.assign_child_at_slot(plate, "C1")
      flex.deck.assign_child_at_slot(other, "C2")
      gripper = flex.gripper
      assert gripper is not None

      with self.assertRaises(OpentronsError):
        asyncio.run(gripper.move_labware(plate, "C2"))

      self._assert_no_move_commands(transport)
      self.assertEqual(flex.deck.get_slot(plate), "C1")
      self.assertEqual(flex.deck.get_slot(other), "C2")
    finally:
      asyncio.run(flex.stop())

  def test_invalid_destination_slot_raises(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      gripper = flex.gripper
      assert gripper is not None

      with self.assertRaises(OpentronsError):
        asyncio.run(gripper.move_labware(plate, "E5"))

      self._assert_no_move_commands(transport)
      self.assertEqual(flex.deck.get_slot(plate), "C1")
    finally:
      asyncio.run(flex.stop())


class TestMoveLabwareWireFailure(unittest.TestCase):
  """A wire-level moveLabware failure re-raises and leaves the deck untouched."""

  def test_failed_move_leaves_deck_untouched(self):
    transport = _FailingMoveTransport(
      pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")], gripper=True
    )
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      gripper = flex.gripper
      assert gripper is not None

      with self.assertRaises(RuntimeError):
        asyncio.run(gripper.move_labware(plate, "C2"))

      move_cmds = [c for c in transport.commands if c["commandType"] == "moveLabware"]
      self.assertEqual(len(move_cmds), 1, "the command reached the wire and failed there")
      self.assertEqual(flex.deck.get_slot(plate), "C1")
      self.assertIsNone(flex.deck.get_resource_at_slot("C2"))
    finally:
      asyncio.run(flex.stop())


class TestLabwareMovedOffDeck(unittest.TestCase):
  """labware_moved_off_deck releases the slot server-side (offDeck logical
  move) for loaded labware, evicts the load cache, and frees the PLR slot."""

  def test_loaded_labware_sends_offdeck_move_and_evicts_cache(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")
      labware_id = asyncio.run(flex._ensure_labware_loaded(plate))

      asyncio.run(flex.labware_moved_off_deck(plate))

      move_cmds = [c for c in transport.commands if c["commandType"] == "moveLabware"]
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(
        move_cmds[0]["params"],
        {
          "labwareId": labware_id,
          "newLocation": "offDeck",
          "strategy": "manualMoveWithoutPause",
        },
      )
      self.assertNotIn("plate", flex._loaded_labware)
      self.assertIsNone(flex.deck.get_slot(plate))
      self.assertIsNone(flex.deck.get_resource_at_slot("C1"))
    finally:
      asyncio.run(flex.stop())

  def test_never_loaded_labware_frees_slot_without_wire_command(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      plate = _plate()
      flex.deck.assign_child_at_slot(plate, "C1")

      asyncio.run(flex.labware_moved_off_deck(plate))

      move_cmds = [c for c in transport.commands if c["commandType"] == "moveLabware"]
      self.assertEqual(len(move_cmds), 0, "never-loaded labware needs no wire command")
      self.assertIsNone(flex.deck.get_slot(plate))
      self.assertIsNone(flex.deck.get_resource_at_slot("C1"))
    finally:
      asyncio.run(flex.stop())


class TestUngrip(unittest.TestCase):
  """ungrip() sends the unsafe/ungripLabware recovery command."""

  def test_ungrip_sends_unsafe_ungrip_labware(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = flex.gripper
      assert gripper is not None

      asyncio.run(gripper.ungrip())

      ungrip_cmds = [c for c in transport.commands if c["commandType"] == "unsafe/ungripLabware"]
      self.assertEqual(len(ungrip_cmds), 1)
      self.assertEqual(ungrip_cmds[0]["params"], {})
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()

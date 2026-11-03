"""Tests for the Flex gripper capability (``flex.gripper``).

Drives ``Flex.setup()`` with an injected ``AsyncMock``
advertising a gripper on the extension mount, and asserts discovery attaches
a :class:`~pylabrobot.opentrons.flex.flex_gripper.FlexGripper`; ``move_labware``
follows the stage -> wire -> commit idiom (PLR-side validation before any
wire command, deck re-parent only on wire success); and
``labware_moved_off_deck`` releases a slot both server-side and PLR-side.
"""

import unittest
from typing import Tuple
from unittest.mock import AsyncMock, patch

from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.flex import Flex
from pylabrobot.opentrons.flex.flex_gripper import FlexGripper
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.opentrons.types import CommandInfo
from pylabrobot.resources import Resource, cor_96_wellplate_360uL_Fb
from pylabrobot.resources.opentrons import flex_96_tiprack_50ul
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.plate import Plate


def _flex_with_gripper(
  test_case: unittest.IsolatedAsyncioTestCase, **api_kwargs
) -> Tuple[Flex, AsyncMock]:
  """Build a Flex with fixed API replies and return its mock for assertions."""
  api = make_api(
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")],
    gripper=True,
    **api_kwargs,
  )
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
  return flex, api


def _plate(name: str = "plate") -> Plate:
  plate = cor_96_wellplate_360uL_Fb(name=name)
  return plate


class TestGripperDiscovery(unittest.IsolatedAsyncioTestCase):
  """setup() composes a FlexGripper iff /instruments reports a gripper."""

  async def test_gripper_attached_when_advertised(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    self.assertIsInstance(flex.gripper, FlexGripper)
    assert flex.gripper is not None
    self.assertEqual(flex.gripper.gripper_model, "gripperV1.3")
    # Head composition is unaffected by the extra instrument entry.
    self.assertIsNotNone(flex.right)

  async def test_gripper_none_when_absent(self):
    api = make_api(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")])
    flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
    self.addAsyncCleanup(flex.disconnect)
    await flex.setup()
    self.assertIsNone(flex.gripper)


class TestMoveLabware(unittest.IsolatedAsyncioTestCase):
  """move_labware sends ONE atomic moveLabware command and re-parents the
  deck only on wire success; the labware is JIT-loaded once."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_happy_path_sends_exact_wire_params_and_reparents_deck(self):
    plate = _plate()
    self.flex.deck.assign_child_at_slot(plate, "C1")
    gripper = self.flex.gripper
    assert gripper is not None

    await gripper.move_labware(plate, "C2")

    move_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(move_cmds), 1)
    labware_id = self.flex._require_labware().get(plate).labware_id
    self.assertEqual(
      move_cmds[0].args[2],
      {
        "labwareId": labware_id,
        "newLocation": {"slotName": "C2"},
        "strategy": "usingGripper",
      },
    )

    self.assertEqual(self.flex.deck.get_slot(plate), "C2")
    self.assertIsNone(self.flex.deck.get_resource_at_slot("C1"))
    self.assertIs(self.flex.deck.get_resource_at_slot("C2"), plate)

  async def test_second_move_reuses_the_loaded_labware(self):
    plate = _plate()
    self.flex.deck.assign_child_at_slot(plate, "C1")
    gripper = self.flex.gripper
    assert gripper is not None

    await gripper.move_labware(plate, "C2")
    await gripper.move_labware(plate, "D3")

    load_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "loadLabware"]
    move_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(load_cmds), 1, "labware must be JIT-loaded exactly once")
    self.assertEqual(len(move_cmds), 2)
    self.assertEqual(self.flex.deck.get_slot(plate), "D3")

  async def test_move_to_staging_slot_uses_addressable_area_form(self):
    # The robot-server's DeckSlotName has no A4-D4: staging slots are
    # addressable areas, so {"slotName": "B4"} would be rejected there.
    plate = _plate()
    self.flex.deck.assign_child_at_slot(plate, "C1")
    gripper = self.flex.gripper
    assert gripper is not None

    await gripper.move_labware(plate, "B4")

    move_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(move_cmds), 1)
    self.assertEqual(move_cmds[0].args[2]["newLocation"], {"addressableAreaName": "B4"})
    self.assertEqual(self.flex.deck.get_slot(plate), "B4")

  async def test_move_back_from_staging_slot_uses_slot_name_form(self):
    plate = _plate()
    self.flex.deck.assign_child_at_slot(plate, "A4")
    gripper = self.flex.gripper
    assert gripper is not None

    await gripper.move_labware(plate, "C2")

    move_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(move_cmds[0].args[2]["newLocation"], {"slotName": "C2"})

  async def test_move_bare_resource_uploads_stub_with_grip_geometry(self):
    # A resource with no pipettable geometry (lid, adapter) rides the movable
    # stub definition; grip_distance_from_top shapes its grip height.
    lid = Resource(name="lid stack", size_x=100.0, size_y=90.0, size_z=20.0)
    self.flex.deck.assign_child_at_slot(lid, "C1")
    gripper = self.flex.gripper
    assert gripper is not None

    await gripper.move_labware(lid, "C2", grip_distance_from_top=5.0)

    self.assertEqual(self.api.define_labware.await_count, 1)
    definition = [c.args[1] for c in self.api.define_labware.await_args_list][0]
    self.assertEqual(definition["ordering"], [["A1"]])
    self.assertEqual(definition["gripHeightFromLabwareBottom"], 15.0)  # 20 - 5
    load_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "loadLabware"]
    self.assertEqual(load_cmds[0].args[2]["namespace"], "pylabrobot")
    self.assertEqual(load_cmds[0].args[2]["loadName"], definition["parameters"]["loadName"])
    move_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(move_cmds), 1)
    self.assertEqual(self.flex.deck.get_slot(lid), "C2")


class TestGripDistanceDiscarded(unittest.IsolatedAsyncioTestCase):
  """grip_distance_from_top only shapes a definition pylabrobot uploads, so
  the paths that cannot honor it say so instead of dropping it silently."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_catalogue_labware_logs_the_ignored_grip_distance(self):
    rack = flex_96_tiprack_50ul(name="rack")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    gripper = self.flex.gripper
    assert gripper is not None

    with self.assertLogs("pylabrobot.opentrons.flex.flex", level="WARNING") as logs:
      await gripper.move_labware(rack, "C2", grip_distance_from_top=5.0)

    # Nothing is uploaded, so there is nowhere to put the requested height:
    # the robot grips at the catalogue definition's own.
    self.assertEqual(self.api.define_labware.await_count, 0)
    load_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "loadLabware"]
    self.assertEqual(load_cmds[0].args[2]["namespace"], "opentrons")
    self.assertTrue(any("grip_distance_from_top=5.0" in line for line in logs.output))
    self.assertTrue(
      any("opentrons_flex_96_tiprack_50ul" in line for line in logs.output),
      logs.output,
    )

  async def test_second_move_logs_the_grip_distance_the_loaded_labware_ignores(self):
    lid = Resource(name="lid stack", size_x=100.0, size_y=90.0, size_z=20.0)
    self.flex.deck.assign_child_at_slot(lid, "C1")
    gripper = self.flex.gripper
    assert gripper is not None
    await gripper.move_labware(lid, "C2", grip_distance_from_top=5.0)

    with self.assertLogs("pylabrobot.opentrons.flex.flex", level="WARNING") as logs:
      await gripper.move_labware(lid, "C3", grip_distance_from_top=9.0)

    self.assertEqual(self.api.define_labware.await_count, 1)
    self.assertEqual(
      [c.args[1] for c in self.api.define_labware.await_args_list][0][
        "gripHeightFromLabwareBottom"
      ],
      15.0,
    )
    self.assertTrue(any("grip_distance_from_top=9.0" in line for line in logs.output))


class TestMoveLabwarePreWireRejections(unittest.IsolatedAsyncioTestCase):
  """Invalid moves raise OpentronsError BEFORE any wire command is sent."""

  def _assert_no_move_commands(self, api: AsyncMock) -> None:
    move_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(move_cmds), 0, "no moveLabware wire command may be sent")

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_labware_not_on_deck_raises(self):
    plate = _plate()  # never assigned to the deck
    gripper = self.flex.gripper
    assert gripper is not None

    with self.assertRaises(OpentronsError):
      await gripper.move_labware(plate, "C2")

    self._assert_no_move_commands(self.api)

  async def test_occupied_destination_raises_and_deck_untouched(self):
    plate = _plate()
    other = _plate(name="other")
    self.flex.deck.assign_child_at_slot(plate, "C1")
    self.flex.deck.assign_child_at_slot(other, "C2")
    gripper = self.flex.gripper
    assert gripper is not None

    with self.assertRaises(OpentronsError):
      await gripper.move_labware(plate, "C2")

    self._assert_no_move_commands(self.api)
    self.assertEqual(self.flex.deck.get_slot(plate), "C1")
    self.assertEqual(self.flex.deck.get_slot(other), "C2")

  async def test_invalid_destination_slot_raises(self):
    plate = _plate()
    self.flex.deck.assign_child_at_slot(plate, "C1")
    gripper = self.flex.gripper
    assert gripper is not None

    with self.assertRaises(OpentronsError):
      await gripper.move_labware(plate, "E5")

    self._assert_no_move_commands(self.api)
    self.assertEqual(self.flex.deck.get_slot(plate), "C1")


class TestMoveLabwareWireFailure(unittest.IsolatedAsyncioTestCase):
  """A wire-level moveLabware failure re-raises and leaves the deck untouched."""

  async def test_failed_move_leaves_deck_untouched(self):
    api = make_api(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")], gripper=True)
    flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
    self.addAsyncCleanup(flex.disconnect)
    await flex.setup()
    plate = _plate()
    flex.deck.assign_child_at_slot(plate, "C1")
    gripper = flex.gripper
    assert gripper is not None

    with (
      patch.object(
        api,
        "get_command",
        AsyncMock(
          side_effect=[
            api.get_command.return_value,
            CommandInfo("failed", {}, {"errorType": "moveFailed", "detail": "move failed"}),
          ]
        ),
      ),
      self.assertRaises(RuntimeError),
    ):
      await gripper.move_labware(plate, "C2")

    move_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(move_cmds), 1, "the command reached the wire and failed there")
    self.assertEqual(flex.deck.get_slot(plate), "C1")
    self.assertIsNone(flex.deck.get_resource_at_slot("C2"))


class TestLabwareMovedOffDeck(unittest.IsolatedAsyncioTestCase):
  """labware_moved_off_deck releases the slot server-side (offDeck logical
  move) for loaded labware, evicts the load cache, and frees the PLR slot."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_loaded_labware_sends_offdeck_move_and_evicts_cache(self):
    plate = _plate()
    self.flex.deck.assign_child_at_slot(plate, "C1")
    labware_id = await self.flex._ensure_labware_loaded(plate)

    await self.flex.labware_moved_off_deck(plate)

    move_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(move_cmds), 1)
    self.assertEqual(
      move_cmds[0].args[2],
      {
        "labwareId": labware_id,
        "newLocation": "offDeck",
        "strategy": "manualMoveWithoutPause",
      },
    )
    self.assertFalse(self.flex._require_labware().is_loaded(plate))
    self.assertIsNone(self.flex.deck.get_slot(plate))
    self.assertIsNone(self.flex.deck.get_resource_at_slot("C1"))

  async def test_never_loaded_labware_frees_slot_without_wire_command(self):
    plate = _plate()
    self.flex.deck.assign_child_at_slot(plate, "C1")

    await self.flex.labware_moved_off_deck(plate)

    move_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveLabware"]
    self.assertEqual(len(move_cmds), 0, "never-loaded labware needs no wire command")
    self.assertIsNone(self.flex.deck.get_slot(plate))
    self.assertIsNone(self.flex.deck.get_resource_at_slot("C1"))


class TestUngrip(unittest.IsolatedAsyncioTestCase):
  """ungrip() sends the unsafe/ungripLabware recovery command."""

  async def test_ungrip_sends_unsafe_ungrip_labware(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    gripper = flex.gripper
    assert gripper is not None

    await gripper.ungrip()

    ungrip_cmds = [
      c for c in api.submit_command.await_args_list if c.args[1] == "unsafe/ungripLabware"
    ]
    self.assertEqual(len(ungrip_cmds), 1)
    self.assertEqual(ungrip_cmds[0].args[2], {})


if __name__ == "__main__":
  unittest.main()

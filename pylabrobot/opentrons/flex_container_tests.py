"""Tests for container (trough/reservoir) ops on the Flex heads, and for the
robot-level commands that belong to the device rather than to a head.

A bare PLR ``Container`` is a single-cavity resource with ONE volume tracker;
robot-side single-cavity labware definitions expose exactly one well, named
"A1", and carry the ``centerMultichannelOnWells`` quirk, so the ENGINE
centers the nozzle array in the cavity. These tests pin the wire shape
(wellName "A1", no manual centering offsets -- only the caller's
offset/liquid_height ride the wire), the one-tracker staging semantics (each
channel holding a tip moves ``volume``; the summed delta commits/rolls back
as one op), and the pre-wire rejections (no tip, array or offset-shifted
array overhanging the cavity).

The robot-level tests pin the wire shape of the device's own commands: the
snake_case params the robot/* family takes (unlike the rest of the API), the
axis-name and version-gate refusals that must send nothing, and the exact
params of the status, comment, wait and reload commands.
"""

import asyncio
import unittest
from typing import Any, Dict, List, Optional, Tuple, Type

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead1, FlexHead8, FlexHead96
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import OFFLINE_API_VERSION, ChatterboxTransport
from pylabrobot.resources import (
  Container,
  cor_96_wellplate_360uL_Fb,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation


class _FailingAspirateTransport(ChatterboxTransport):
  """Chatterbox whose ``aspirate`` POST raises -- models a wire-level failure
  AFTER trackers are staged, driving the rollback paths."""

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if path.endswith("/commands") and (json or {}).get("data", {}).get("commandType") == "aspirate":
      raise RuntimeError("simulated aspirate wire failure")
    return await super().post(path, json)


class _FailingDispenseTransport(ChatterboxTransport):
  """Like ``_FailingAspirateTransport`` but for ``dispense`` commands."""

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if path.endswith("/commands") and (json or {}).get("data", {}).get("commandType") == "dispense":
      raise RuntimeError("simulated dispense wire failure")
    return await super().post(path, json)


def _make_trough(
  name: str = "trough",
  size_x: float = 127.76,
  size_y: float = 85.48,
  max_volume: float = 195000.0,
) -> Container:
  """A single-cavity reservoir built directly with the PLR ``Container`` class,
  mapped to a real Opentrons single-cavity load name.

  The default size is that reservoir's OUTER footprint, the convention a PLR
  container carries (its cavity is narrower, and PLR does not model it).
  """
  trough = Container(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=31.4,
    material_z_thickness=1.0,
    max_volume=max_volume,
  )
  trough.ot_load_name = "nest_1_reservoir_195ml"  # type: ignore[attr-defined]
  return trough


def _flex_head1(
  transport_cls: Type[ChatterboxTransport] = ChatterboxTransport,
) -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead1]:
  """An ``OpentronsFlex`` with a single-channel head on the right mount, plus
  the transport (for command inspection) and the head itself.
  """
  transport = transport_cls(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")])
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  asyncio.run(flex.setup())
  head = flex.right
  assert isinstance(head, FlexHead1)
  return flex, transport, head


def _flex_head8(
  transport_cls: Type[ChatterboxTransport] = ChatterboxTransport,
) -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead8]:
  """An ``OpentronsFlex`` with an 8-channel head on the left mount, plus the
  transport (for command inspection) and the head itself.
  """
  transport = transport_cls(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  asyncio.run(flex.setup())
  head = flex.left
  assert isinstance(head, FlexHead8)
  return flex, transport, head


def _flex_head96(
  transport_cls: Type[ChatterboxTransport] = ChatterboxTransport,
) -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead96]:
  """An ``OpentronsFlex`` with a 96-channel head, plus the transport (for
  command inspection) and the head itself.
  """
  transport = transport_cls(pipettes=[("p1000_96", 96, 1.0, 1000.0, "left")])
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  asyncio.run(flex.setup())
  head = flex.head96
  assert isinstance(head, FlexHead96)
  return flex, transport, head


class TestFlexHead1ContainerOps(unittest.TestCase):
  """FlexHead1 aspirate/dispense accept a bare Container: the container is its
  own robot-side labware addressed at its sole well "A1", and volume is
  tracked against the container's single tracker.
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_aspirate_names_container_labware_at_well_a1(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(1000.0)

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      asyncio.run(head.aspirate(trough, volume=50))

      load_cmds = [
        c
        for c in transport.commands
        if c["commandType"] == "loadLabware" and c["params"]["loadName"] == "nest_1_reservoir_195ml"
      ]
      self.assertEqual(len(load_cmds), 1, "the container itself must be loaded as labware")

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(aspirate_cmds[0]["params"]["wellName"], "A1")
      self.assertEqual(aspirate_cmds[0]["params"]["labwareId"], transport.labware_ids["trough"])
      # A single nozzle goes to the cavity center: no x/y centering offset,
      # just the default bottom clearance.
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"],
        {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
      )

      self.assertAlmostEqual(trough.tracker.volume, 950.0)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_adds_volume_to_container_tracker(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      asyncio.run(head.dispense(trough, volume=30))

      dispense_cmds = [c for c in transport.commands if c["commandType"] == "dispense"]
      self.assertEqual(len(dispense_cmds), 1)
      self.assertEqual(dispense_cmds[0]["params"]["wellName"], "A1")
      self.assertAlmostEqual(trough.tracker.volume, 30.0)
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_container_without_tip_rejects_before_any_wire_command(self):
    flex, transport, head = _flex_head1()
    try:
      trough = _make_trough()
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(1000.0)

      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate(trough, volume=50))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 1000.0)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_container_without_tip_rejects_before_any_wire_command(self):
    flex, transport, head = _flex_head1()
    try:
      trough = _make_trough()
      flex.deck.assign_child_at_slot(trough, "C2")

      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.dispense(trough, volume=30))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 0.0)
    finally:
      asyncio.run(flex.stop())

  def test_wire_failure_rolls_back_container_tracker(self):
    flex, _transport, head = _flex_head1(transport_cls=_FailingAspirateTransport)
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(1000.0)

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      with self.assertRaises(RuntimeError):
        asyncio.run(head.aspirate(trough, volume=50))

      self.assertAlmostEqual(trough.tracker.volume, 1000.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 1000.0)
    finally:
      asyncio.run(flex.stop())


class TestFlexHead8ContainerOps(unittest.TestCase):
  """FlexHead8 aspirate_container/dispense_container fan all 8 nozzles into
  one cavity: ONE command at well "A1" with no manual centering (the
  definition's centerMultichannelOnWells quirk makes the engine center the
  63 mm nozzle row), and the container's single tracker moves
  volume * (channels holding tips) as one committed/rolled-back op.
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_aspirate_container_sends_one_uncentered_command_at_a1(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_container(trough, volume=50))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      params = aspirate_cmds[0]["params"]
      self.assertEqual(params["wellName"], "A1")
      self.assertEqual(params["volume"], 50)
      # No manual x/y centering: the engine centers the nozzle row on the
      # cavity itself (centerMultichannelOnWells). Only the default bottom
      # clearance rides the wire.
      self.assertEqual(
        params["wellLocation"],
        {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
      )

      cmd_types = [c["commandType"] for c in transport.commands]
      self.assertNotIn(
        "prepareToAspirate",
        cmd_types,
        "naming the well leaves priming to the robot, which does it at the well top",
      )

      # All 8 channels hold a tip, so the single tracker loses 8 * 50.
      self.assertAlmostEqual(trough.tracker.volume, 9600.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 9600.0)
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_container_tracker_scales_with_mounted_tips(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      # Leave only 3 tips in column 0 (rows A, D, H) before the pickup.
      column_0_spots = rack.get_all_items()[0:8]
      for i in (1, 2, 4, 5, 6):
        column_0_spots[i].tracker.remove_tip(commit=True)

      asyncio.run(head.pick_up_tips(rack, column=0))
      self.assertEqual(sum(1 for t in head.get_mounted_tips() if t is not None), 3)

      asyncio.run(head.aspirate_container(trough, volume=50))

      # Exactly the 3 tip-holding channels draw 50 each: -150, not -400.
      self.assertAlmostEqual(trough.tracker.volume, 9850.0)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_container_adds_volume_per_mounted_tip(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.dispense_container(trough, volume=40))

      dispense_cmds = [c for c in transport.commands if c["commandType"] == "dispense"]
      self.assertEqual(len(dispense_cmds), 1)
      params = dispense_cmds[0]["params"]
      self.assertEqual(params["wellName"], "A1")
      self.assertEqual(
        params["wellLocation"],
        {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
      )
      self.assertAlmostEqual(trough.tracker.volume, 320.0)
    finally:
      asyncio.run(flex.stop())

  def test_offset_and_liquid_height_merge_into_well_location(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(
        head.aspirate_container(
          trough, volume=10, offset=Coordinate(x=2, y=-1, z=0.5), liquid_height=3
        )
      )

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      # Only the caller's offset rides the wire (centering is engine-side);
      # liquid_height adds to z, replacing the default clearance.
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"],
        {"origin": "bottom", "offset": {"x": 2, "y": -1, "z": 3.5}},
      )
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_container_without_tip_rejects_before_any_wire_command(self):
    flex, transport, head = _flex_head8()
    try:
      trough = _make_trough()
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate_container(trough, volume=50))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 10000.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 10000.0)
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_container_rejects_cavity_narrower_than_nozzle_row(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      narrow = _make_trough(name="narrow", size_y=40.0, max_volume=50000.0)
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(narrow, "C2")
      narrow.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))

      # 40 mm front-to-back cannot contain the 63 mm nozzle row.
      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate_container(narrow, volume=50))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(narrow.tracker.volume, 10000.0)
      self.assertAlmostEqual(narrow.tracker.get_used_volume(), 10000.0)
    finally:
      asyncio.run(flex.stop())

  def test_wire_failure_rolls_back_container_tracker(self):
    flex, _transport, head = _flex_head8(transport_cls=_FailingAspirateTransport)
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      with self.assertRaises(RuntimeError):
        asyncio.run(head.aspirate_container(trough, volume=50))

      # The staged 8 * 50 uL is rolled back in full: committed AND pending
      # volume are untouched.
      self.assertAlmostEqual(trough.tracker.volume, 10000.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 10000.0)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_container_without_tip_rejects_before_any_wire_command(self):
    flex, transport, head = _flex_head8()
    try:
      trough = _make_trough()
      flex.deck.assign_child_at_slot(trough, "C2")

      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.dispense_container(trough, volume=40))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 0.0)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_container_wire_failure_rolls_back_container_tracker(self):
    flex, _transport, head = _flex_head8(transport_cls=_FailingDispenseTransport)
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")

      asyncio.run(head.pick_up_tips(rack, column=0))
      with self.assertRaises(RuntimeError):
        asyncio.run(head.dispense_container(trough, volume=40))

      # The staged 8 * 40 uL is rolled back in full.
      self.assertAlmostEqual(trough.tracker.volume, 0.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 0.0)
    finally:
      asyncio.run(flex.stop())

  def test_offset_that_keeps_row_inside_cavity_passes(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()  # 85.48 mm front-to-back; 63 mm row + 2*11.24 = 85.48 just fits
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_container(trough, volume=10, offset=Coordinate(y=11.24)))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      # A lateral-only offset keeps the default bottom clearance: its z is 0
      # because the caller said nothing about z, not to ask for the floor.
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"]["offset"],
        {"x": 0, "y": 11.24, "z": 1.0},
      )
    finally:
      asyncio.run(flex.stop())

  def test_zero_offset_keeps_the_default_bottom_clearance(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_container(trough, volume=10, offset=Coordinate.zero()))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"]["offset"],
        {"x": 0, "y": 0, "z": 1.0},
        "a no-op offset must not move the tip to the cavity floor",
      )
    finally:
      asyncio.run(flex.stop())

  def test_too_small_container_message_names_no_offset_when_none_was_passed(self):
    flex, _transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      narrow = _make_trough(name="narrow", size_y=40.0, max_volume=50000.0)
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(narrow, "C2")
      narrow.tracker.set_volume(10000.0)
      asyncio.run(head.pick_up_tips(rack, column=0))

      with self.assertRaises(OpentronsError) as no_offset:
        asyncio.run(head.aspirate_container(narrow, volume=50))
      self.assertNotIn("offset", str(no_offset.exception))

      with self.assertRaises(OpentronsError) as with_offset:
        asyncio.run(head.aspirate_container(narrow, volume=50, offset=Coordinate(y=3)))
      self.assertIn("offset", str(with_offset.exception))
    finally:
      asyncio.run(flex.stop())

  def test_rotated_cavity_is_guarded_on_the_axis_the_robot_sees(self):
    # 40 x 70 mm rotated a quarter turn is a 70 x 40 mm cavity to the robot,
    # which is the footprint the uploaded definition carries.
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      rotated = _make_trough(name="rotated", size_x=40.0, size_y=70.0, max_volume=50000.0)
      rotated.rotation = Rotation(z=90)
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(rotated, "C2")
      rotated.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate_container(rotated, volume=50))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(rotated.tracker.volume, 10000.0)
    finally:
      asyncio.run(flex.stop())

  def test_rotated_cavity_deep_enough_for_the_row_is_accepted(self):
    # The mirror case: 110 x 60 mm rotated presents 110 mm front-to-back, so
    # the 63 mm row fits and reading the pre-rotation 60 mm would refuse it.
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      rotated = _make_trough(name="rotated", size_x=110.0, size_y=60.0, max_volume=50000.0)
      rotated.rotation = Rotation(z=90)
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(rotated, "C2")
      rotated.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_container(rotated, volume=50))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
    finally:
      asyncio.run(flex.stop())

  def test_rotated_parent_rotates_the_cavity_too(self):
    # The guard reads the COMPOSED rotation, so a plain container inside a
    # rotated carrier is guarded on the same axis the robot will see.
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      carrier = Resource(name="carrier", size_x=70.0, size_y=40.0, size_z=25.0)
      carrier.rotation = Rotation(z=90)
      inner = _make_trough(name="inner", size_x=40.0, size_y=70.0, max_volume=50000.0)
      carrier.assign_child_resource(inner, location=Coordinate.zero())
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(carrier, "C2")
      inner.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError) as raised:
        asyncio.run(head.aspirate_container(inner, volume=50))

      # Name the guard: reading the container's own y (70 mm) would let this
      # through, and it would then fail later for an unrelated reason.
      self.assertEqual(raised.exception.title, "Container too small")
      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_offset_that_shifts_row_past_cavity_wall_rejects_pre_wire(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()  # 85.48 front-to-back; 63 mm row + 2*11.5 = 86 overhangs
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate_container(trough, volume=10, offset=Coordinate(y=-11.5)))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 10000.0)
    finally:
      asyncio.run(flex.stop())


class TestFlexHead96ContainerOps(unittest.TestCase):
  """FlexHead96 aspirate/dispense accept a bare Container: ONE command at
  well "A1" with no manual centering (the definition's
  centerMultichannelOnWells quirk makes the engine center the 12x8 grid),
  and the container's single tracker moves volume * (channels holding tips).
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_aspirate_container_no_manual_centering_and_tracks_96_channels(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(100000.0)

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.aspirate(trough, volume=50))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      params = aspirate_cmds[0]["params"]
      self.assertEqual(params["wellName"], "A1")
      # No manual x/y centering: the engine centers the 12x8 grid on the
      # cavity itself (centerMultichannelOnWells).
      self.assertEqual(
        params["wellLocation"],
        {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
      )

      self.assertAlmostEqual(trough.tracker.volume, 100000.0 - 96 * 50.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 100000.0 - 96 * 50.0)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_container_no_manual_centering_and_adds_96x(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.dispense(trough, volume=20))

      dispense_cmds = [c for c in transport.commands if c["commandType"] == "dispense"]
      self.assertEqual(len(dispense_cmds), 1)
      params = dispense_cmds[0]["params"]
      self.assertEqual(params["wellName"], "A1")
      self.assertEqual(
        params["wellLocation"],
        {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
      )
      self.assertAlmostEqual(trough.tracker.volume, 96 * 20.0)
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_container_without_tip_rejects_before_any_wire_command(self):
    flex, transport, head = _flex_head96()
    try:
      trough = _make_trough()
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(100000.0)

      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate(trough, volume=50))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 100000.0)
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_container_rejects_cavity_smaller_than_grid(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      narrow = _make_trough(name="narrow", size_x=90.0, max_volume=50000.0)
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(narrow, "C2")
      narrow.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack))

      # 90 mm left-to-right cannot contain the grid's 99 mm x span.
      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate(narrow, volume=50))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(narrow.tracker.volume, 10000.0)
    finally:
      asyncio.run(flex.stop())

  def test_wire_failure_rolls_back_container_tracker(self):
    flex, _transport, head = _flex_head96(transport_cls=_FailingAspirateTransport)
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(100000.0)

      asyncio.run(head.pick_up_tips(rack))
      with self.assertRaises(RuntimeError):
        asyncio.run(head.aspirate(trough, volume=50))

      # The staged 96 * 50 uL is rolled back in full: committed AND pending
      # volume are untouched.
      self.assertAlmostEqual(trough.tracker.volume, 100000.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 100000.0)
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_container_rejects_cavity_shallower_than_grid_y_span(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      shallow = _make_trough(name="shallow", size_y=50.0, max_volume=50000.0)
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(shallow, "C2")
      shallow.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack))

      # 50 mm front-to-back cannot contain the grid's 63 mm y span.
      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate(shallow, volume=50))

      self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(shallow.tracker.volume, 10000.0)
    finally:
      asyncio.run(flex.stop())

  def test_offset_that_keeps_grid_inside_cavity_passes(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()  # 127.76 x 85.48; grid 99 x 63 plus 2*14.38 / 2*11.24 just fits
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(100000.0)

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.aspirate(trough, volume=10, offset=Coordinate(x=14.38, y=11.24)))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"]["offset"],
        {"x": 14.38, "y": 11.24, "z": 1.0},
      )
    finally:
      asyncio.run(flex.stop())

  def test_rotated_cavity_is_guarded_on_the_axis_the_robot_sees(self):
    # 107 x 71 mm rotated a quarter turn is 71 mm left-to-right to the robot,
    # too narrow for the grid's 99 mm x span, though unrotated it fits.
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      rotated = _make_trough(name="rotated")
      rotated.rotation = Rotation(z=90)
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(rotated, "C2")
      rotated.tracker.set_volume(100000.0)

      asyncio.run(head.pick_up_tips(rack))
      for op in (
        lambda: head.aspirate(rotated, volume=50),
        lambda: head.dispense(rotated, volume=50),
      ):
        commands_before = len(transport.commands)
        with self.assertRaises(OpentronsError):
          asyncio.run(op())
        self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(rotated.tracker.volume, 100000.0)
    finally:
      asyncio.run(flex.stop())

  def test_offset_that_shifts_grid_past_cavity_wall_rejects_pre_wire(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      trough = _make_trough()  # 127.76 x 85.48: 99 + 2*14.5 and 63 + 2*11.5 both overhang
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(100000.0)

      asyncio.run(head.pick_up_tips(rack))
      for offset in (Coordinate(x=14.5), Coordinate(y=11.5)):
        commands_before = len(transport.commands)
        with self.assertRaises(OpentronsError):
          asyncio.run(head.aspirate(trough, volume=10, offset=offset))
        self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 100000.0)
    finally:
      asyncio.run(flex.stop())


class TestLiquidOpsRequireAMountedTip(unittest.TestCase):
  """Every liquid op requires a mounted tip, wells and plates as much as
  containers: without one the command describes a tip that is not there and
  the pipette drives its bare NOZZLE roughly a tip length lower. The engine
  rejects a tipless op, but ``dispense`` moves to the well FIRST and checks
  after, so the collision happens before the rejection."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  @staticmethod
  def _plate(flex: OpentronsFlex):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    flex.deck.assign_child_at_slot(plate, "C2")
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)
    return plate

  def _assert_refused_pre_wire(self, flex, transport, op) -> None:
    commands_before = len(transport.commands)
    with self.assertRaises(OpentronsError) as caught:
      asyncio.run(op())
    self.assertEqual(caught.exception.title, "NoTipError")
    self.assertEqual(len(transport.commands), commands_before)

  def test_head1_well_ops_refuse_without_a_tip(self):
    flex, transport, head = _flex_head1()
    try:
      well = self._plate(flex).get_item("B3")
      self._assert_refused_pre_wire(flex, transport, lambda: head.aspirate(well, volume=10))
      self._assert_refused_pre_wire(flex, transport, lambda: head.dispense(well, volume=10))
      self.assertAlmostEqual(well.tracker.volume, 100.0)
    finally:
      asyncio.run(flex.stop())

  def test_head8_column_ops_refuse_without_tips(self):
    flex, transport, head = _flex_head8()
    try:
      plate = self._plate(flex)
      self._assert_refused_pre_wire(
        flex, transport, lambda: head.aspirate(plate, column=0, volume=10)
      )
      self._assert_refused_pre_wire(
        flex, transport, lambda: head.dispense(plate, column=0, volume=10)
      )
      self.assertAlmostEqual(plate.get_item("A1").tracker.volume, 100.0)
    finally:
      asyncio.run(flex.stop())

  def test_head96_plate_ops_refuse_without_tips(self):
    flex, transport, head = _flex_head96()
    try:
      plate = self._plate(flex)
      self._assert_refused_pre_wire(flex, transport, lambda: head.aspirate(plate, volume=10))
      self._assert_refused_pre_wire(flex, transport, lambda: head.dispense(plate, volume=10))
      self.assertAlmostEqual(plate.get_item("A1").tracker.volume, 100.0)
    finally:
      asyncio.run(flex.stop())


class _AxisPositionTransport(ChatterboxTransport):
  """Chatterbox whose robot/moveAxes* commands report the axis positions they
  reached, as the real robot-server does. The dict it hands back is the one the
  completion poll reads, so setting the result here is what the caller sees."""

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = await super().post(path, json)
    command_type = (json or {}).get("data", {}).get("commandType")
    if command_type in ("robot/moveAxesTo", "robot/moveAxesRelative"):
      response["data"]["result"] = {"position": {"x": 11.0, "y": 22.0, "leftZ": 33.0}}
    return response


def _flex_device(
  api_version: str = OFFLINE_API_VERSION,
  transport_cls: Type[ChatterboxTransport] = ChatterboxTransport,
) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  """A set-up ``OpentronsFlex`` plus its transport, for the robot-level
  commands that belong to the device rather than to a head. ``api_version`` is
  what ``/health`` reports, which is what the robot/* version gate reads.
  """
  transport = transport_cls(
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")], api_version=api_version
  )
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  asyncio.run(flex.setup())
  return flex, transport


def _cmds(transport: ChatterboxTransport, command_type: str) -> List[Dict[str, Any]]:
  return [c for c in transport.commands if c["commandType"] == command_type]


class TestFlexAxisMotion(unittest.TestCase):
  """move_axes_to/move_axes_relative send the robot/* family's snake_case
  params and return the axis positions the command reports."""

  def test_move_axes_to_sends_snake_case_axis_map_only(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.move_axes_to({"x": 100.0, "leftZ": 250.0}))

      (cmd,) = _cmds(transport, "robot/moveAxesTo")
      self.assertEqual(cmd["params"], {"axis_map": {"x": 100.0, "leftZ": 250.0}})
    finally:
      asyncio.run(flex.stop())

  def test_move_axes_to_sends_critical_point_and_speed_snake_case(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.move_axes_to({"x": 1.0}, critical_point={"rightZ": 5.0}, speed=40.0))

      (cmd,) = _cmds(transport, "robot/moveAxesTo")
      self.assertEqual(
        cmd["params"],
        {"axis_map": {"x": 1.0}, "critical_point": {"rightZ": 5.0}, "speed": 40.0},
      )
    finally:
      asyncio.run(flex.stop())

  def test_move_axes_relative_sends_snake_case_axis_map_and_speed(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.move_axes_relative({"y": -5.0}, speed=20.0))

      (cmd,) = _cmds(transport, "robot/moveAxesRelative")
      self.assertEqual(cmd["params"], {"axis_map": {"y": -5.0}, "speed": 20.0})
    finally:
      asyncio.run(flex.stop())

  def test_speed_omitted_when_not_given(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.move_axes_relative({"y": -5.0}))

      (cmd,) = _cmds(transport, "robot/moveAxesRelative")
      self.assertNotIn("speed", cmd["params"])
    finally:
      asyncio.run(flex.stop())

  def test_both_moves_return_the_reported_position(self):
    flex, _transport = _flex_device(transport_cls=_AxisPositionTransport)
    try:
      reached = {"x": 11.0, "y": 22.0, "leftZ": 33.0}
      self.assertEqual(asyncio.run(flex.move_axes_to({"x": 11.0})), reached)
      self.assertEqual(asyncio.run(flex.move_axes_relative({"x": 1.0})), reached)
    finally:
      asyncio.run(flex.stop())

  def test_unknown_axis_refused_before_any_wire_command(self):
    flex, transport = _flex_device()
    try:
      for op in (
        lambda: flex.move_axes_to({"x": 1.0, "zed": 2.0}),
        lambda: flex.move_axes_relative({"zed": 2.0}),
      ):
        commands_before = len(transport.commands)
        with self.assertRaises(ValueError) as caught:
          asyncio.run(op())
        # The refusal names what was wrong AND what would have been right.
        self.assertIn("zed", str(caught.exception))
        self.assertIn("extensionJaw", str(caught.exception))
        self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_unknown_critical_point_axis_refused_before_any_wire_command(self):
    flex, transport = _flex_device()
    try:
      commands_before = len(transport.commands)
      with self.assertRaises(ValueError):
        asyncio.run(flex.move_axes_to({"x": 1.0}, critical_point={"zed": 2.0}))

      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_retract_axis_refuses_an_unknown_axis_before_the_wire(self):
    flex, transport = _flex_device()
    try:
      commands_before = len(transport.commands)
      with self.assertRaises(ValueError):
        asyncio.run(flex.retract_axis("zed"))

      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())


class TestFlexAxisMotionVersionGate(unittest.TestCase):
  """The robot/* moveAxes commands need robot software 8.2.0+; retractAxis
  predates that family and is not gated."""

  def test_old_release_refuses_both_moves_and_sends_nothing(self):
    flex, transport = _flex_device(api_version="8.1.0")
    try:
      for op in (
        lambda: flex.move_axes_to({"x": 1.0}),
        lambda: flex.move_axes_relative({"x": 1.0}),
      ):
        with self.assertRaises(OpentronsError) as caught:
          asyncio.run(op())
        self.assertIn("8.2.0", str(caught.exception))

      robot_cmds = [c for c in transport.commands if c["commandType"].startswith("robot/")]
      self.assertEqual(robot_cmds, [])
    finally:
      asyncio.run(flex.stop())

  def test_retract_axis_is_not_gated(self):
    flex, transport = _flex_device(api_version="8.1.0")
    try:
      asyncio.run(flex.retract_axis("leftZ"))
      self.assertEqual(len(_cmds(transport, "retractAxis")), 1)
    finally:
      asyncio.run(flex.stop())


class TestFlexRobotCommands(unittest.TestCase):
  """The device's one-shot commands: exact params, no extras."""

  def test_retract_axis(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.retract_axis("extensionJaw"))

      (cmd,) = _cmds(transport, "retractAxis")
      self.assertEqual(cmd["params"], {"axis": "extensionJaw"})
    finally:
      asyncio.run(flex.stop())

  def test_set_status_bar(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.set_status_bar("disco"))

      (cmd,) = _cmds(transport, "setStatusBar")
      self.assertEqual(cmd["params"], {"animation": "disco"})
    finally:
      asyncio.run(flex.stop())

  def test_set_rail_lights_carries_the_boolean_both_ways(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.set_rail_lights(True))
      asyncio.run(flex.set_rail_lights(False))

      cmds = _cmds(transport, "setRailLights")
      self.assertEqual([c["params"] for c in cmds], [{"on": True}, {"on": False}])
    finally:
      asyncio.run(flex.stop())

  def test_add_comment(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.add_comment("starting plate 3"))

      (cmd,) = _cmds(transport, "comment")
      self.assertEqual(cmd["params"], {"message": "starting plate 3"})
    finally:
      asyncio.run(flex.stop())

  def test_wait_for_duration(self):
    flex, transport = _flex_device()
    try:
      asyncio.run(flex.wait_for_duration(2.5))

      (cmd,) = _cmds(transport, "waitForDuration")
      self.assertEqual(cmd["params"], {"seconds": 2.5})
    finally:
      asyncio.run(flex.stop())

  def test_reload_labware_names_the_id_the_labware_was_loaded_under(self):
    flex, transport = _flex_device()
    try:
      trough = _make_trough()
      flex.deck.assign_child_at_slot(trough, "C2")

      asyncio.run(flex.reload_labware(trough))

      (_load_cmd,) = _cmds(transport, "loadLabware")
      (reload_cmd,) = _cmds(transport, "reloadLabware")
      self.assertEqual(reload_cmd["params"], {"labwareId": transport.labware_ids["trough"]})
    finally:
      asyncio.run(flex.stop())

  def test_reload_labware_of_loaded_labware_does_not_load_it_again(self):
    flex, transport = _flex_device()
    try:
      trough = _make_trough()
      flex.deck.assign_child_at_slot(trough, "C2")

      asyncio.run(flex.reload_labware(trough))
      asyncio.run(flex.reload_labware(trough))

      self.assertEqual(len(_cmds(transport, "loadLabware")), 1)
      self.assertEqual(len(_cmds(transport, "reloadLabware")), 2)
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()

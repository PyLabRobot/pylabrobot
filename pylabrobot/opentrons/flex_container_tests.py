"""Tests for container (trough/reservoir) ops on the Flex heads.

A bare PLR ``Container`` is a single-cavity resource with ONE volume tracker;
robot-side single-cavity labware definitions expose exactly one well, named
"A1", and carry the ``centerMultichannelOnWells`` quirk, so the ENGINE
centers the nozzle array in the cavity. These tests pin the wire shape
(wellName "A1", no manual centering offsets -- only the caller's
offset/liquid_height ride the wire), the one-tracker staging semantics (each
channel holding a tip moves ``volume``; the summed delta commits/rolls back
as one op), and the pre-wire rejections (no tip, array or offset-shifted
array overhanging the cavity).
"""

import asyncio
import unittest
from typing import Any, Dict, Optional, Tuple, Type

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead1, FlexHead8, FlexHead96
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources import (
  Container,
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
  size_x: float = 107.0,
  size_y: float = 71.0,
  max_volume: float = 195000.0,
) -> Container:
  """A single-cavity reservoir built directly with the PLR ``Container`` class,
  mapped to a real Opentrons single-cavity load name.
  """
  trough = Container(name=name, size_x=size_x, size_y=size_y, size_z=25.0, max_volume=max_volume)
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
      self.assertEqual(aspirate_cmds[0]["params"]["labwareId"], load_cmds[0]["params"]["labwareId"])
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
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      aspirate_indices = [i for i, t in enumerate(cmd_types) if t == "aspirate"]
      self.assertEqual(len(prepare_indices), 1, "prepareToAspirate must fire before the aspirate")
      self.assertEqual(prepare_indices[0], aspirate_indices[0] - 1)

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
      trough = _make_trough()  # 71 mm front-to-back; 63 mm row + 2*4 = 71 fits
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_container(trough, volume=10, offset=Coordinate(y=4)))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      # A lateral-only offset keeps the default bottom clearance: its z is 0
      # because the caller said nothing about z, not to ask for the floor.
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"]["offset"],
        {"x": 0, "y": 4, "z": 1.0},
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
      trough = _make_trough()  # 71 mm front-to-back; 63 mm row + 2*5 = 73 overhangs
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(10000.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      commands_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.aspirate_container(trough, volume=10, offset=Coordinate(y=-5)))

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
      trough = _make_trough()  # 107 x 71 mm; grid 99 x 63 + 2*4 on each axis fits
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(100000.0)

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.aspirate(trough, volume=10, offset=Coordinate(x=4, y=4)))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"]["offset"],
        {"x": 4, "y": 4, "z": 1.0},
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
      trough = _make_trough()  # 107 x 71 mm: x 99 + 2*4.5 = 108 and y 63 + 2*4.5 = 72 overhang
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(trough, "C2")
      trough.tracker.set_volume(100000.0)

      asyncio.run(head.pick_up_tips(rack))
      for offset in (Coordinate(x=4.5), Coordinate(y=4.5)):
        commands_before = len(transport.commands)
        with self.assertRaises(OpentronsError):
          asyncio.run(head.aspirate(trough, volume=10, offset=offset))
        self.assertEqual(len(transport.commands), commands_before)
      self.assertAlmostEqual(trough.tracker.volume, 100000.0)
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()

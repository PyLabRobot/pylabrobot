"""Tests for container (trough/reservoir) ops on the Flex heads.

A bare PLR ``Container`` is a single-cavity resource with ONE volume tracker;
robot-side single-cavity labware definitions expose exactly one well, named
"A1". These tests pin the wire shape (wellName "A1" plus the head-centering
``wellLocation`` math), the one-tracker staging semantics (each channel
holding a tip moves ``volume``; the summed delta commits/rolls back as one
op), the pre-wire rejections, and that the plate/column paths are unchanged.
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
  cor_96_wellplate_360uL_Fb,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


class _FailingAspirateTransport(ChatterboxTransport):
  """Chatterbox whose ``aspirate`` POST raises -- models a wire-level failure
  AFTER trackers are staged, driving the rollback paths."""

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if path.endswith("/commands") and (json or {}).get("data", {}).get("commandType") == "aspirate":
      raise RuntimeError("simulated aspirate wire failure")
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


class TestFlexHead8ContainerOps(unittest.TestCase):
  """FlexHead8 aspirate_container/dispense_container fan all 8 nozzles into
  one cavity: ONE command at well "A1" with a wellLocation centering the
  63 mm nozzle row, and the container's single tracker moves
  volume * (channels holding tips) as one committed/rolled-back op.
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_aspirate_container_sends_one_centered_command_at_a1(self):
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
      # The anchor (channel A) nozzle sits half the 63 mm row span back (+y)
      # of the cavity center, so the row is centered front-to-back.
      self.assertEqual(
        params["wellLocation"],
        {"origin": "bottom", "offset": {"x": 0.0, "y": 31.5, "z": 1.0}},
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
        {"origin": "bottom", "offset": {"x": 0.0, "y": 31.5, "z": 1.0}},
      )
      self.assertAlmostEqual(trough.tracker.volume, 320.0)
    finally:
      asyncio.run(flex.stop())

  def test_container_well_location_merges_offset_and_liquid_height(self):
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
      # The caller's offset rides on top of the centering; liquid_height adds
      # to z, replacing the default clearance.
      self.assertEqual(
        aspirate_cmds[0]["params"]["wellLocation"],
        {"origin": "bottom", "offset": {"x": 2.0, "y": 30.5, "z": 3.5}},
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

  def test_column_aspirate_on_plate_unchanged(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      wells = plate.get_all_items()
      for well in wells:
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate(plate, column=2, volume=50))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(aspirate_cmds[0]["params"]["wellName"], "A3")

      column_2 = set(wells[16:24])
      for well in wells:
        expected = 50.0 if well in column_2 else 100.0
        self.assertAlmostEqual(well.tracker.volume, expected, msg=well.name)
    finally:
      asyncio.run(flex.stop())


class TestFlexHead96ContainerOps(unittest.TestCase):
  """FlexHead96 aspirate/dispense accept a bare Container: ONE command at
  well "A1" whose wellLocation puts the back-left (A1) anchor nozzle at
  (-49.5, +31.5) from the cavity center so the 12x8 grid is centered, and
  the container's single tracker moves volume * (channels holding tips).
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_aspirate_container_centers_grid_and_tracks_96_channels(self):
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
      # Back-left anchor nozzle at (-99/2, +63/2) from the cavity center
      # centers the whole 12x8 grid.
      self.assertEqual(
        params["wellLocation"],
        {"origin": "bottom", "offset": {"x": -49.5, "y": 31.5, "z": 1.0}},
      )

      self.assertAlmostEqual(trough.tracker.volume, 100000.0 - 96 * 50.0)
      self.assertAlmostEqual(trough.tracker.get_used_volume(), 100000.0 - 96 * 50.0)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_container_centers_grid_and_adds_96x(self):
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
        {"origin": "bottom", "offset": {"x": -49.5, "y": 31.5, "z": 1.0}},
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

  def test_plate_aspirate_unchanged(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      for well in plate.get_all_items():
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.aspirate(plate, volume=50))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(aspirate_cmds[0]["params"]["wellName"], "A1")
      for well in plate.get_all_items():
        self.assertAlmostEqual(well.tracker.volume, 50.0, msg=well.name)
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()

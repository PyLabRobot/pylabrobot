"""Tests for container (trough/reservoir) ops on the Flex heads, and for the
robot-level commands that belong to the device rather than to a head.

A bare PLR container has one volume tracker. Pipetting centers the nozzle
array using PLR coordinates and sends an in-place plunger command. Tests cover
coordinates, offsets, per-tip volume accounting and rollback on wire failure.

The robot-level tests pin the wire shape of the device's own commands: the
snake_case params the robot/* family takes (unlike the rest of the API), the
axis-name and version-gate refusals that must send nothing, and the exact
params of the status, comment, wait and reload commands.
"""

import unittest
from typing import List, Tuple
from unittest.mock import AsyncMock, _Call, patch

from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.flex import Flex
from pylabrobot.opentrons.flex.flex_head import FlexHead1, FlexHead8, FlexHead96
from pylabrobot.opentrons.flex.flex_wire import OFFLINE_API_VERSION
from pylabrobot.opentrons.flex.tests.liquid_test_utils import pipetting_location
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
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
  return trough


async def _flex_head1(
  test_case: unittest.IsolatedAsyncioTestCase,
) -> Tuple[Flex, AsyncMock, FlexHead1]:
  """An ``Flex`` with a single-channel head on the right mount, plus
  the api (for command inspection) and the head itself.
  """
  api = make_api(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")])
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
  await flex.setup()
  head = flex.right
  assert isinstance(head, FlexHead1)
  return flex, api, head


async def _flex_head8(
  test_case: unittest.IsolatedAsyncioTestCase,
) -> Tuple[Flex, AsyncMock, FlexHead8]:
  """An ``Flex`` with an 8-channel head on the left mount, plus the
  api (for command inspection) and the head itself.
  """
  api = make_api(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
  await flex.setup()
  head = flex.left
  assert isinstance(head, FlexHead8)
  return flex, api, head


async def _flex_head96(
  test_case: unittest.IsolatedAsyncioTestCase,
) -> Tuple[Flex, AsyncMock, FlexHead96]:
  """An ``Flex`` with a 96-channel head, plus the api (for
  command inspection) and the head itself.
  """
  api = make_api(pipettes=[("p1000_96", 96, 1.0, 1000.0, "left")])
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
  await flex.setup()
  head = flex.head96
  assert isinstance(head, FlexHead96)
  return flex, api, head


class TestFlexHead1ContainerOps(unittest.IsolatedAsyncioTestCase):
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

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head1(self)

  async def test_aspirate_positions_container_without_loading_labware(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(1000.0)

    await self.head.pick_up_tips(rack.get_item("A1"))
    await self.head.aspirate(trough, volume=50)

    load_cmds = [
      c
      for c in self.api.submit_command.await_args_list
      if c.args[1] == "loadLabware" and c.args[2]["loadName"] == "nest_1_reservoir_195ml"
    ]
    self.assertEqual(len(load_cmds), 0, "PLR positioning must not load the container")

    aspirate_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)
    self.assertNotIn("wellName", aspirate_cmds[0].args[2])
    self.assertNotIn("labwareId", aspirate_cmds[0].args[2])
    # A single nozzle goes to the cavity center: no x/y centering offset,
    # just the default bottom clearance.
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels),
      {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
    )

    self.assertAlmostEqual(trough.tracker.volume, 950.0)

  async def test_dispense_adds_volume_to_container_tracker(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")

    await self.head.pick_up_tips(rack.get_item("A1"))
    for tip in self.head.get_mounted_tips():
      if tip is not None:
        tip.tracker.set_volume(30)
    await self.head.dispense(trough, volume=30)

    dispense_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "dispenseInPlace"
    ]
    self.assertEqual(len(dispense_cmds), 1)
    self.assertNotIn("wellName", dispense_cmds[0].args[2])
    self.assertAlmostEqual(trough.tracker.volume, 30.0)

  async def test_aspirate_container_without_tip_rejects_before_any_wire_command(self):
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(1000.0)

    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate(trough, volume=50)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(trough.tracker.volume, 1000.0)

  async def test_dispense_container_without_tip_rejects_before_any_wire_command(self):
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(trough, "C2")

    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.dispense(trough, volume=30)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(trough.tracker.volume, 0.0)

  async def test_wire_failure_rolls_back_container_tracker(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(1000.0)

    await self.head.pick_up_tips(rack.get_item("A1"))
    with (
      patch.object(
        self.api,
        "submit_command",
        AsyncMock(
          side_effect=[self.api.submit_command.return_value] * 4
          + [RuntimeError("pipetting failed")]
        ),
      ) as submit,
      self.assertRaisesRegex(RuntimeError, "pipetting failed"),
    ):
      await self.head.aspirate(trough, volume=50)

    assert submit.await_args is not None
    self.assertEqual(submit.await_args.args[1], "aspirateInPlace")
    self.assertAlmostEqual(trough.tracker.volume, 1000.0)
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 1000.0)


class TestFlexHead8ContainerOps(unittest.IsolatedAsyncioTestCase):
  """PLR centers the nozzle row; each mounted tip changes the shared tracker."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)

  async def test_aspirate_container_centers_nozzle_row(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    await self.head.aspirate_container(trough, volume=50)

    aspirate_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)
    params = aspirate_cmds[0].args[2]
    self.assertNotIn("wellName", params)
    self.assertEqual(params["volume"], 50)
    # The primary nozzle is positioned so the row centers on the cavity.
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels),
      {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
    )

    cmd_types = [c.args[1] for c in self.api.submit_command.await_args_list]
    self.assertIn(
      "prepareToAspirate",
      cmd_types,
      "the driver primes above the target before descent",
    )

    # All 8 channels hold a tip, so the single tracker loses 8 * 50.
    self.assertAlmostEqual(trough.tracker.volume, 9600.0)
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 9600.0)

  async def test_aspirate_container_tracker_scales_with_mounted_tips(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    # Leave only 3 tips in column 0 (rows A, D, H) before the pickup.
    column_0_spots = rack.get_all_items()[0:8]
    for i in (1, 2, 4, 5, 6):
      column_0_spots[i].tracker.remove_tip(commit=True)

    await self.head.pick_up_tips(rack, column=0)
    self.assertEqual(sum(1 for t in self.head.get_mounted_tips() if t is not None), 3)

    await self.head.aspirate_container(trough, volume=50)

    # Exactly the 3 tip-holding channels draw 50 each: -150, not -400.
    self.assertAlmostEqual(trough.tracker.volume, 9850.0)

  async def test_dispense_container_adds_volume_per_mounted_tip(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")

    await self.head.pick_up_tips(rack, column=0)
    for tip in self.head.get_mounted_tips():
      if tip is not None:
        tip.tracker.set_volume(40)
    await self.head.dispense_container(trough, volume=40)

    dispense_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "dispenseInPlace"
    ]
    self.assertEqual(len(dispense_cmds), 1)
    params = dispense_cmds[0].args[2]
    self.assertNotIn("wellName", params)
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels),
      {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
    )
    self.assertAlmostEqual(trough.tracker.volume, 320.0)

  async def test_offset_and_liquid_height_merge_into_well_location(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    await self.head.aspirate_container(
      trough, volume=10, offset=Coordinate(x=2, y=-1, z=0.5), liquid_height=3
    )

    [c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"]
    # Caller offsets shift the centered row; liquid_height replaces clearance.
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels),
      {"origin": "bottom", "offset": {"x": 2, "y": -1, "z": 3.5}},
    )

  async def test_aspirate_container_without_tip_rejects_before_any_wire_command(self):
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate_container(trough, volume=50)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(trough.tracker.volume, 10000.0)
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 10000.0)

  async def test_aspirate_container_rejects_cavity_narrower_than_nozzle_row(self):
    rack = flex_96_tiprack_50ul(name="rack")
    narrow = _make_trough(name="narrow", size_y=40.0, max_volume=50000.0)
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(narrow, "C2")
    narrow.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)

    # 40 mm front-to-back cannot contain the 63 mm nozzle row.
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate_container(narrow, volume=50)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(narrow.tracker.volume, 10000.0)
    self.assertAlmostEqual(narrow.tracker.get_used_volume(), 10000.0)

  async def test_wire_failure_rolls_back_container_tracker(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    with (
      patch.object(
        self.api,
        "submit_command",
        AsyncMock(
          side_effect=[self.api.submit_command.return_value] * 4
          + [RuntimeError("pipetting failed")]
        ),
      ) as submit,
      self.assertRaisesRegex(RuntimeError, "pipetting failed"),
    ):
      await self.head.aspirate_container(trough, volume=50)

    assert submit.await_args is not None
    self.assertEqual(submit.await_args.args[1], "aspirateInPlace")
    # The staged 8 * 50 uL is rolled back in full: committed AND pending
    # volume are untouched.
    self.assertAlmostEqual(trough.tracker.volume, 10000.0)
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 10000.0)

  async def test_dispense_container_without_tip_rejects_before_any_wire_command(self):
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(trough, "C2")

    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.dispense_container(trough, volume=40)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(trough.tracker.volume, 0.0)

  async def test_dispense_container_wire_failure_rolls_back_container_tracker(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")

    await self.head.pick_up_tips(rack, column=0)
    with (
      patch.object(
        self.api,
        "submit_command",
        AsyncMock(
          side_effect=[self.api.submit_command.return_value] * 3
          + [RuntimeError("pipetting failed")]
        ),
      ) as submit,
      self.assertRaisesRegex(RuntimeError, "pipetting failed"),
    ):
      for tip in self.head.get_mounted_tips():
        if tip is not None:
          tip.tracker.set_volume(40)
      await self.head.dispense_container(trough, volume=40)

    assert submit.await_args is not None
    self.assertEqual(submit.await_args.args[1], "dispenseInPlace")
    # The staged 8 * 40 uL is rolled back in full.
    self.assertAlmostEqual(trough.tracker.volume, 0.0)
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 0.0)

  async def test_offset_that_keeps_row_inside_cavity_passes(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()  # 85.48 mm front-to-back; 63 mm row + 2*11.24 = 85.48 just fits
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    await self.head.aspirate_container(trough, volume=10, offset=Coordinate(y=11.24))

    aspirate_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)
    # A lateral-only offset keeps the default bottom clearance: its z is 0
    # because the caller said nothing about z, not to ask for the floor.
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels)["offset"],
      {"x": 0, "y": 11.24, "z": 1.0},
    )

  async def test_zero_offset_keeps_the_default_bottom_clearance(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    await self.head.aspirate_container(trough, volume=10, offset=Coordinate.zero())

    [c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"]
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels)["offset"],
      {"x": 0, "y": 0, "z": 1.0},
      "a no-op offset must not move the tip to the cavity floor",
    )

  async def test_too_small_container_message_names_no_offset_when_none_was_passed(self):
    rack = flex_96_tiprack_50ul(name="rack")
    narrow = _make_trough(name="narrow", size_y=40.0, max_volume=50000.0)
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(narrow, "C2")
    narrow.tracker.set_volume(10000.0)
    await self.head.pick_up_tips(rack, column=0)

    with self.assertRaises(OpentronsError) as no_offset:
      await self.head.aspirate_container(narrow, volume=50)
    self.assertNotIn("offset", str(no_offset.exception))

    with self.assertRaises(OpentronsError) as with_offset:
      await self.head.aspirate_container(narrow, volume=50, offset=Coordinate(y=3))
    self.assertIn("offset", str(with_offset.exception))

  async def test_rotated_cavity_is_guarded_on_the_axis_the_robot_sees(self):
    # 40 x 70 mm rotated a quarter turn is a 70 x 40 mm cavity to the robot,
    # which is the footprint the uploaded definition carries.
    rack = flex_96_tiprack_50ul(name="rack")
    rotated = _make_trough(name="rotated", size_x=40.0, size_y=70.0, max_volume=50000.0)
    rotated.rotation = Rotation(z=90)
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(rotated, "C2")
    rotated.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate_container(rotated, volume=50)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(rotated.tracker.volume, 10000.0)

  async def test_rotated_cavity_deep_enough_for_the_row_is_accepted(self):
    # The mirror case: 110 x 60 mm rotated presents 110 mm front-to-back, so
    # the 63 mm row fits and reading the pre-rotation 60 mm would refuse it.
    rack = flex_96_tiprack_50ul(name="rack")
    rotated = _make_trough(name="rotated", size_x=110.0, size_y=60.0, max_volume=50000.0)
    rotated.rotation = Rotation(z=90)
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(rotated, "C2")
    rotated.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    await self.head.aspirate_container(rotated, volume=50)

    aspirate_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)

  async def test_rotated_parent_rotates_the_cavity_too(self):
    # The guard reads the COMPOSED rotation, so a plain container inside a
    # rotated carrier is guarded on the same axis the robot will see.
    rack = flex_96_tiprack_50ul(name="rack")
    carrier = Resource(name="carrier", size_x=70.0, size_y=40.0, size_z=25.0)
    carrier.rotation = Rotation(z=90)
    inner = _make_trough(name="inner", size_x=40.0, size_y=70.0, max_volume=50000.0)
    carrier.assign_child_resource(inner, location=Coordinate.zero())
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(carrier, "C2")
    inner.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError) as raised:
      await self.head.aspirate_container(inner, volume=50)

    # Name the guard: reading the container's own y (70 mm) would let this
    # through, and it would then fail later for an unrelated reason.
    self.assertEqual(raised.exception.title, "Container too small")
    self.assertEqual(self.api.submit_command.await_count, commands_before)

  async def test_offset_that_shifts_row_past_cavity_wall_rejects_pre_wire(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()  # 85.48 front-to-back; 63 mm row + 2*11.5 = 86 overhangs
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack, column=0)
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate_container(trough, volume=10, offset=Coordinate(y=-11.5))

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(trough.tracker.volume, 10000.0)


class TestFlexHead96ContainerOps(unittest.IsolatedAsyncioTestCase):
  """PLR centers the 96-nozzle grid and tracks total container volume."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head96(self)

  async def test_aspirate_container_centers_grid_and_tracks_96_channels(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(100000.0)

    await self.head.pick_up_tips(rack)
    await self.head.aspirate(trough, volume=50)

    aspirate_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)
    params = aspirate_cmds[0].args[2]
    self.assertNotIn("wellName", params)
    # The primary nozzle is positioned so the grid centers on the cavity.
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels),
      {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
    )

    self.assertAlmostEqual(trough.tracker.volume, 100000.0 - 96 * 50.0)
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 100000.0 - 96 * 50.0)

  async def test_dispense_container_centers_grid_and_adds_96x(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")

    await self.head.pick_up_tips(rack)
    for tip in self.head.get_mounted_tips():
      if tip is not None:
        tip.tracker.set_volume(20)
    await self.head.dispense(trough, volume=20)

    dispense_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "dispenseInPlace"
    ]
    self.assertEqual(len(dispense_cmds), 1)
    params = dispense_cmds[0].args[2]
    self.assertNotIn("wellName", params)
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels),
      {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
    )
    self.assertAlmostEqual(trough.tracker.volume, 96 * 20.0)

  async def test_aspirate_container_without_tip_rejects_before_any_wire_command(self):
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(100000.0)

    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate(trough, volume=50)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(trough.tracker.volume, 100000.0)

  async def test_aspirate_container_rejects_cavity_smaller_than_grid(self):
    rack = flex_96_tiprack_50ul(name="rack")
    narrow = _make_trough(name="narrow", size_x=90.0, max_volume=50000.0)
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(narrow, "C2")
    narrow.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack)

    # 90 mm left-to-right cannot contain the grid's 99 mm x span.
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate(narrow, volume=50)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(narrow.tracker.volume, 10000.0)

  async def test_wire_failure_rolls_back_container_tracker(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(100000.0)

    await self.head.pick_up_tips(rack)
    with (
      patch.object(
        self.api,
        "submit_command",
        AsyncMock(
          side_effect=[self.api.submit_command.return_value] * 4
          + [RuntimeError("pipetting failed")]
        ),
      ) as submit,
      self.assertRaisesRegex(RuntimeError, "pipetting failed"),
    ):
      await self.head.aspirate(trough, volume=50)

    assert submit.await_args is not None
    self.assertEqual(submit.await_args.args[1], "aspirateInPlace")
    # The staged 96 * 50 uL is rolled back in full: committed AND pending
    # volume are untouched.
    self.assertAlmostEqual(trough.tracker.volume, 100000.0)
    self.assertAlmostEqual(trough.tracker.get_used_volume(), 100000.0)

  async def test_aspirate_container_rejects_cavity_shallower_than_grid_y_span(self):
    rack = flex_96_tiprack_50ul(name="rack")
    shallow = _make_trough(name="shallow", size_y=50.0, max_volume=50000.0)
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(shallow, "C2")
    shallow.tracker.set_volume(10000.0)

    await self.head.pick_up_tips(rack)

    # 50 mm front-to-back cannot contain the grid's 63 mm y span.
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.aspirate(shallow, volume=50)

    self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(shallow.tracker.volume, 10000.0)

  async def test_offset_that_keeps_grid_inside_cavity_passes(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()  # 127.76 x 85.48; grid 99 x 63 plus 2*14.38 / 2*11.24 just fits
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(100000.0)

    await self.head.pick_up_tips(rack)
    await self.head.aspirate(trough, volume=10, offset=Coordinate(x=14.38, y=11.24))

    aspirate_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)
    self.assertEqual(
      pipetting_location(self.api, trough, self.head.channels)["offset"],
      {"x": 14.38, "y": 11.24, "z": 1.0},
    )

  async def test_rotated_cavity_is_guarded_on_the_axis_the_robot_sees(self):
    # 107 x 71 mm rotated a quarter turn is 71 mm left-to-right to the robot,
    # too narrow for the grid's 99 mm x span, though unrotated it fits.
    rack = flex_96_tiprack_50ul(name="rack")
    rotated = _make_trough(name="rotated")
    rotated.rotation = Rotation(z=90)
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(rotated, "C2")
    rotated.tracker.set_volume(100000.0)

    await self.head.pick_up_tips(rack)
    for op in (
      lambda: self.head.aspirate(rotated, volume=50),
      lambda: self.head.dispense(rotated, volume=50),
    ):
      commands_before = self.api.submit_command.await_count
      with self.assertRaises(OpentronsError):
        await op()
      self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(rotated.tracker.volume, 100000.0)

  async def test_offset_that_shifts_grid_past_cavity_wall_rejects_pre_wire(self):
    rack = flex_96_tiprack_50ul(name="rack")
    trough = _make_trough()  # 127.76 x 85.48: 99 + 2*14.5 and 63 + 2*11.5 both overhang
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(100000.0)

    await self.head.pick_up_tips(rack)
    for offset in (Coordinate(x=14.5), Coordinate(y=11.5)):
      commands_before = self.api.submit_command.await_count
      with self.assertRaises(OpentronsError):
        await self.head.aspirate(trough, volume=10, offset=offset)
      self.assertEqual(self.api.submit_command.await_count, commands_before)
    self.assertAlmostEqual(trough.tracker.volume, 100000.0)


class TestLiquidOpsRequireAMountedTip(unittest.IsolatedAsyncioTestCase):
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
  def _plate(flex: Flex):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(plate, "C2")
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)
    return plate

  async def _assert_refused_pre_wire(self, flex, api, op) -> None:
    commands_before = api.submit_command.await_count
    with self.assertRaises(OpentronsError) as caught:
      await op()
    self.assertEqual(caught.exception.title, "NoTipError")
    self.assertEqual(api.submit_command.await_count, commands_before)

  async def test_head1_well_ops_refuse_without_a_tip(self):
    flex, api, head = await _flex_head1(self)
    well = self._plate(flex).get_item("B3")
    await self._assert_refused_pre_wire(flex, api, lambda: head.aspirate(well, volume=10))
    await self._assert_refused_pre_wire(flex, api, lambda: head.dispense(well, volume=10))
    self.assertAlmostEqual(well.tracker.volume, 100.0)

  async def test_head8_column_ops_refuse_without_tips(self):
    flex, api, head = await _flex_head8(self)
    plate = self._plate(flex)
    await self._assert_refused_pre_wire(
      flex, api, lambda: head.aspirate(plate, column=0, volume=10)
    )
    await self._assert_refused_pre_wire(
      flex, api, lambda: head.dispense(plate, column=0, volume=10)
    )
    self.assertAlmostEqual(plate.get_item("A1").tracker.volume, 100.0)

  async def test_head96_plate_ops_refuse_without_tips(self):
    flex, api, head = await _flex_head96(self)
    plate = self._plate(flex)
    await self._assert_refused_pre_wire(flex, api, lambda: head.aspirate(plate, volume=10))
    await self._assert_refused_pre_wire(flex, api, lambda: head.dispense(plate, volume=10))
    self.assertAlmostEqual(plate.get_item("A1").tracker.volume, 100.0)


async def _flex_device(
  test_case: unittest.IsolatedAsyncioTestCase,
  api_version: str = OFFLINE_API_VERSION,
) -> Tuple[Flex, AsyncMock]:
  """A set-up ``Flex`` plus its api, for the robot-level
  commands that belong to the device rather than to a head. ``api_version`` is
  what ``/health`` reports, which is what the robot/* version gate reads.
  """
  api = make_api(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")], api_version=api_version)
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
  await flex.setup()
  return flex, api


def _cmds(api: AsyncMock, command_type: str) -> List[_Call]:
  return [c for c in api.submit_command.await_args_list if c.args[1] == command_type]


class TestFlexAxisMotion(unittest.IsolatedAsyncioTestCase):
  """move_axes_to/move_axes_relative send the robot/* family's snake_case
  params and return the axis positions the command reports."""

  async def asyncSetUp(self):
    self.flex, self.api = await _flex_device(self)

  async def test_move_axes_to_sends_snake_case_axis_map_only(self):
    await self.flex.move_axes_to({"x": 100.0, "leftZ": 250.0})

    (cmd,) = _cmds(self.api, "robot/moveAxesTo")
    self.assertEqual(cmd.args[2], {"axis_map": {"x": 100.0, "leftZ": 250.0}})

  async def test_move_axes_to_sends_critical_point_and_speed_snake_case(self):
    await self.flex.move_axes_to({"x": 1.0}, critical_point={"rightZ": 5.0}, speed=40.0)

    (cmd,) = _cmds(self.api, "robot/moveAxesTo")
    self.assertEqual(
      cmd.args[2],
      {"axis_map": {"x": 1.0}, "critical_point": {"rightZ": 5.0}, "speed": 40.0},
    )

  async def test_move_axes_relative_sends_snake_case_axis_map_and_speed(self):
    await self.flex.move_axes_relative({"y": -5.0}, speed=20.0)

    (cmd,) = _cmds(self.api, "robot/moveAxesRelative")
    self.assertEqual(cmd.args[2], {"axis_map": {"y": -5.0}, "speed": 20.0})

  async def test_speed_omitted_when_not_given(self):
    await self.flex.move_axes_relative({"y": -5.0})

    (cmd,) = _cmds(self.api, "robot/moveAxesRelative")
    self.assertNotIn("speed", cmd.args[2])

  async def test_both_moves_return_the_reported_position(self):
    self.api.get_command.return_value.result["position"] = {"x": 11.0, "y": 22.0, "leftZ": 33.0}
    reached = {"x": 11.0, "y": 22.0, "leftZ": 33.0}
    self.assertEqual(await self.flex.move_axes_to({"x": 11.0}), reached)
    self.assertEqual(await self.flex.move_axes_relative({"x": 1.0}), reached)

  async def test_unknown_axis_refused_before_any_wire_command(self):
    for op in (
      lambda: self.flex.move_axes_to({"x": 1.0, "zed": 2.0}),
      lambda: self.flex.move_axes_relative({"zed": 2.0}),
    ):
      commands_before = self.api.submit_command.await_count
      with self.assertRaises(ValueError) as caught:
        await op()
      # The refusal names what was wrong AND what would have been right.
      self.assertIn("zed", str(caught.exception))
      self.assertIn("extensionJaw", str(caught.exception))
      self.assertEqual(self.api.submit_command.await_count, commands_before)

  async def test_unknown_critical_point_axis_refused_before_any_wire_command(self):
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(ValueError):
      await self.flex.move_axes_to({"x": 1.0}, critical_point={"zed": 2.0})

    self.assertEqual(self.api.submit_command.await_count, commands_before)

  async def test_retract_axis_refuses_an_unknown_axis_before_the_wire(self):
    commands_before = self.api.submit_command.await_count
    with self.assertRaises(ValueError):
      await self.flex.retract_axis("zed")

    self.assertEqual(self.api.submit_command.await_count, commands_before)


class TestFlexAxisMotionVersionGate(unittest.IsolatedAsyncioTestCase):
  """The robot/* moveAxes commands need robot software 8.2.0+; retractAxis
  predates that family and is not gated."""

  async def asyncSetUp(self):
    self.flex, self.api = await _flex_device(self, api_version="8.1.0")

  async def test_old_release_refuses_both_moves_and_sends_nothing(self):
    for op in (
      lambda: self.flex.move_axes_to({"x": 1.0}),
      lambda: self.flex.move_axes_relative({"x": 1.0}),
    ):
      with self.assertRaises(OpentronsError) as caught:
        await op()
      self.assertIn("8.2.0", str(caught.exception))

    robot_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1].startswith("robot/")
    ]
    self.assertEqual(robot_cmds, [])

  async def test_retract_axis_is_not_gated(self):
    await self.flex.retract_axis("leftZ")
    self.assertEqual(len(_cmds(self.api, "retractAxis")), 1)


class TestFlexRobotCommands(unittest.IsolatedAsyncioTestCase):
  """The device's one-shot commands: exact params, no extras."""

  async def asyncSetUp(self):
    self.flex, self.api = await _flex_device(self)

  async def test_retract_axis(self):
    await self.flex.retract_axis("extensionJaw")

    (cmd,) = _cmds(self.api, "retractAxis")
    self.assertEqual(cmd.args[2], {"axis": "extensionJaw"})

  async def test_set_status_bar(self):
    await self.flex.set_status_bar("disco")

    (cmd,) = _cmds(self.api, "setStatusBar")
    self.assertEqual(cmd.args[2], {"animation": "disco"})

  async def test_set_rail_lights_carries_the_boolean_both_ways(self):
    await self.flex.set_rail_lights(True)
    await self.flex.set_rail_lights(False)

    cmds = _cmds(self.api, "setRailLights")
    self.assertEqual([c.args[2] for c in cmds], [{"on": True}, {"on": False}])

  async def test_add_comment(self):
    await self.flex.add_comment("starting plate 3")

    (cmd,) = _cmds(self.api, "comment")
    self.assertEqual(cmd.args[2], {"message": "starting plate 3"})

  async def test_wait_for_duration(self):
    await self.flex.wait_for_duration(2.5)

    (cmd,) = _cmds(self.api, "waitForDuration")
    self.assertEqual(cmd.args[2], {"seconds": 2.5})

  async def test_reload_labware_names_the_id_the_labware_was_loaded_under(self):
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(trough, "C2")

    await self.flex.reload_labware(trough)

    (_load_cmd,) = _cmds(self.api, "loadLabware")
    (reload_cmd,) = _cmds(self.api, "reloadLabware")
    self.assertEqual(reload_cmd.args[2], {"labwareId": "labware"})

  async def test_reload_labware_of_loaded_labware_does_not_load_it_again(self):
    trough = _make_trough()
    self.flex.deck.assign_child_at_slot(trough, "C2")

    await self.flex.reload_labware(trough)
    await self.flex.reload_labware(trough)

    self.assertEqual(len(_cmds(self.api, "loadLabware")), 1)
    self.assertEqual(len(_cmds(self.api, "reloadLabware")), 2)


if __name__ == "__main__":
  unittest.main()

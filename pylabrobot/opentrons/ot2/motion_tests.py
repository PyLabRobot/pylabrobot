"""OT-2 motion preflight with mocked run operations."""

import unittest
from unittest.mock import ANY, AsyncMock, create_autospec

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import OT2, OT2_8ChannelPipette, OT2SingleChannelPipette
from pylabrobot.opentrons.labware import LabwareRegistry
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.resources import (
  Coordinate,
  Resource,
  Rotation,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import (
  OTDeck,
  opentrons_96_filtertiprack_20ul,
  opentrons_96_tiprack_300ul,
)


class OT2MotionTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    """Set up both pipette types and a tip rack without connecting to hardware."""
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.addCleanup(set_tip_tracking, False)
    self.addCleanup(set_volume_tracking, False)
    self.protocol_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    self.protocol_run.active = True
    self.protocol_run.get_position.return_value = Coordinate(30.25, 40.5, 20)
    self.deck = OTDeck()
    self.robot = OT2("ot2.local", deck=self.deck, io=AsyncMock(spec_set=HTTP))
    self.robot._run = self.protocol_run
    self.robot._labware = LabwareRegistry(self.protocol_run)
    self.multi = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
    self.single = OT2SingleChannelPipette(self.robot, "right", "p20_single_gen2", "right-id")
    self.rack = opentrons_96_filtertiprack_20ul("tips")
    self.deck.assign_child_at_slot(self.rack, 1)
    self.column = self.rack["A1:H1"]
    self.primary = self.column[0].get_location_wrt(self.deck, "c", "c", "b")
    tip = self.column[0].get_tip()
    self.nozzle_z = self.primary.z + tip.get_size_z() - tip.fitting_depth

  def _place_obstacle(self, location: Coordinate, height: float, rotation: float = 0) -> Resource:
    """Place an obstacle extending from another slot into the pickup region."""
    obstacle = Resource("obstacle", 20, 20, height, rotation=Rotation(z=rotation))
    self.deck.assign_child_at_slot(obstacle, 3)
    obstacle.location = location - self.deck.slot_locations[2]
    return obstacle

  async def test_unreachable_tip_commands_send_nothing_on_either_pipette(self) -> None:
    """Reach failures occur before labware loading, command submission, or tracker staging."""
    for pipette, pickup, drop, target in (
      (self.multi, self.multi.pick_up_tips, self.multi.drop_tips, self.column),
      (self.single, self.single.pick_up_tip, self.single.drop_tip, self.column[0]),
    ):
      with self.subTest(pipette=pipette.name):
        for offset in (Coordinate(x=1000), Coordinate(y=-1000), Coordinate(z=-1000)):
          with self.subTest(offset=offset):
            self.protocol_run.reset_mock()
            with self.assertRaises(ValueError):
              await pickup(target, offset=offset)  # type: ignore[arg-type]
            self.assertEqual(self.protocol_run.mock_calls, [])
            self.assertFalse(pipette.has_tip)
            self.assertTrue(all(spot.has_tip() for spot in self.column))
        await pickup(target)  # type: ignore[arg-type]
        self.protocol_run.reset_mock()
        with self.assertRaises(ValueError):
          await drop(target, offset=Coordinate(x=1000))  # type: ignore[arg-type]
        self.assertEqual(self.protocol_run.mock_calls, [])
        self.assertTrue(pipette.has_tip)
        await drop(target)  # type: ignore[arg-type]

  async def test_pickup_respects_mount_reach_and_xy_offset(self) -> None:
    """The left mount can reach negative X positions the right mount cannot."""
    robot_x = self.primary.x - self.deck.slot_locations[0].x
    offset = Coordinate(x=-20 - robot_x)
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "right mount"):
      await self.single.pick_up_tip(self.column[0], offset=offset)
    self.assertEqual(self.protocol_run.mock_calls, [])
    await self.multi.pick_up_tips(self.column, offset=offset)
    self.assertTrue(self.multi.has_tip)

  async def test_eight_channel_y_reach_uses_head_center(self) -> None:
    """The reference nozzle is 31.5 mm behind the center used for reach checks."""
    self.protocol_run.get_position.return_value = Coordinate(100, 100, 120)
    for y in (31.5, 379.0):
      with self.subTest(y=y):
        self.protocol_run.reset_mock()
        await self.multi.move_to(Coordinate(100, y, 120))
        self.protocol_run.move_to.assert_awaited_once_with(
          "left-id",
          Coordinate(100, y, 120),
          speed=None,
          minimum_z_height=None,
          force_direct=False,
        )
    for y in (20, 31.49, 379.01):
      with self.subTest(y=y):
        self.protocol_run.reset_mock()
        with self.assertRaises(ValueError):
          await self.multi.move_to(Coordinate(100, y, 120))
        self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_single_channel_y_reach_has_no_head_center_offset(self) -> None:
    """Single-channel targets remain bounded directly by Y=0 through Y=347.5."""
    self.protocol_run.get_position.return_value = Coordinate(100, 100, 120)
    for y in (0, 20, 347.5):
      await self.single.move_to(Coordinate(100, y, 120))
    for y in (-0.01, 347.51, 379):
      with self.subTest(y=y):
        self.protocol_run.reset_mock()
        with self.assertRaises(ValueError):
          await self.single.move_to(Coordinate(100, y, 120))
        self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_tip_commands_use_head_center_before_tracker_changes(self) -> None:
    """Pickup and drop apply the same reference offset as explicit moves."""
    robot_y = self.primary.y - self.deck.slot_locations[0].y
    offset = Coordinate(y=20 - robot_y)
    self.protocol_run.reset_mock()
    with self.assertRaises(ValueError):
      await self.multi.pick_up_tips(self.column, offset=offset)
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    await self.multi.pick_up_tips(self.column)
    tips = self.multi.tips
    self.protocol_run.reset_mock()
    with self.assertRaises(ValueError):
      await self.multi.drop_tips(self.column, offset=offset)
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertIs(self.multi.tips, tips)
    self.assertTrue(all(not spot.has_tip() for spot in self.column))

  async def test_tall_overlapping_resource_rejects_before_pickup(self) -> None:
    """A tall neighboring resource blocks the entire eight-nozzle footprint."""
    self._place_obstacle(self.primary + Coordinate(x=-10, y=-50, z=-self.primary.z), 100)
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "obstacle"):
      await self.multi.pick_up_tips(self.column)
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    self.assertFalse(self.multi.has_tip)

  async def test_short_resource_below_nozzles_is_cleared(self) -> None:
    """XY overlap alone does not reject labware below the pickup clearance threshold."""
    self._place_obstacle(
      self.primary + Coordinate(x=-10, y=-50, z=-self.primary.z), self.nozzle_z - 10 - 0.1
    )
    await self.multi.pick_up_tips(self.column)
    self.assertTrue(self.multi.has_tip)

  async def test_300ul_pickup_clears_short_plate_below_nozzle_engagement(self) -> None:
    """Long tips clear an overlapping short plate despite their bottoms being near the deck."""
    pipette = OT2_8ChannelPipette(self.robot, "left", "p300_multi_gen2", "p300-id")
    rack = opentrons_96_tiprack_300ul("tips300")
    self.deck.assign_child_at_slot(rack, 10)
    plate = celltreat_96_wellplate_350uL_Fb("short_plate")
    self.deck.assign_child_at_slot(plate, 7)
    # Extend the plate beneath the full column's front nozzles.
    assert plate.location is not None
    plate.location += Coordinate(y=36)

    await pipette.pick_up_tips(rack["A1:H1"])

    self.assertEqual(len(pipette.tips), 8)
    self.assertTrue(all(not spot.has_tip() for spot in rack["A1:H1"]))
    self.protocol_run.pick_up_tip.assert_awaited_once_with("p300-id", ANY, "A1", ANY)

  async def test_z_offset_is_included_in_pickup_clearance(self) -> None:
    """A downward pickup offset can bring the nozzle envelope into a short obstacle."""
    self._place_obstacle(
      self.primary + Coordinate(x=-10, y=-50, z=-self.primary.z), self.nozzle_z - 10 - 0.1
    )
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "obstacle"):
      await self.multi.pick_up_tips(self.column, offset=Coordinate(z=-1))
    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_xy_offset_is_included_in_pickup_clearance(self) -> None:
    """An obstacle outside the nominal footprint can overlap an offset pickup."""
    self._place_obstacle(self.primary + Coordinate(x=6, y=-50, z=-self.primary.z), 100)
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "obstacle"):
      await self.multi.pick_up_tips(self.column, offset=Coordinate(x=2))
    self.assertEqual(self.protocol_run.mock_calls, [])
    await self.multi.pick_up_tips(self.column)

  async def test_rotated_obstacle_uses_its_transformed_corners(self) -> None:
    """A rotated obstacle can extend to the left of its resource origin."""
    self._place_obstacle(
      self.primary + Coordinate(x=6, y=-10, z=-self.primary.z), 100, rotation=180
    )
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "obstacle"):
      await self.multi.pick_up_tips(self.column)
    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_child_overhanging_its_parent_is_checked(self) -> None:
    """A carrier's small bounds cannot hide an attached component in the pickup footprint."""
    parent = self._place_obstacle(self.primary + Coordinate(x=100), 1)
    child = Resource("overhang", 20, 20, 100)
    parent.assign_child_resource(child, location=Coordinate(x=-110, y=-50))
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "overhang"):
      await self.multi.pick_up_tips(self.column)
    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_clearance_is_independent_of_world_translation(self) -> None:
    """Head and resource bounds use the same deck-relative frame."""
    world = Resource("world", 1000, 1000, 1000)
    world.assign_child_resource(self.deck, location=Coordinate(400, 500, 600))
    self._place_obstacle(self.primary + Coordinate(x=-10, y=-50, z=-self.primary.z), 100)
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "obstacle"):
      await self.multi.pick_up_tips(self.column)
    self.assertEqual(self.protocol_run.mock_calls, [])

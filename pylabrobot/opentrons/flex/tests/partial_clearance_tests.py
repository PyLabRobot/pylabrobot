"""Partial-column pipetting must reject modeled adjacent obstacles before motion."""

import unittest

from pylabrobot.opentrons import FlexHead8
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import (
  Coordinate,
  Resource,
  TipRack,
  cor_96_wellplate_360uL_Fb,
  no_volume_tracking,
)
from pylabrobot.resources.opentrons import flex_96_filtertiprack_50ul


class PartialClearanceTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    """Pick up four front tips with mocked API replies in a clear D1 slot."""
    self.api = make_api(pipettes=[("p1000_multi_flex", 8, 5, 1000, "left")])
    self.flex = make_flex("offline", api=self.api)
    self.rack = flex_96_filtertiprack_50ul("tips")
    self.plate = cor_96_wellplate_360uL_Fb("plate")
    self.flex.deck.assign_child_at_slot(self.rack, "D1")
    self.flex.deck.assign_child_at_slot(self.plate, "C2")
    await self.flex.setup()
    head = self.flex.left_pipette
    assert isinstance(head, FlexHead8)
    self.head: FlexHead8 = head
    await head.pick_up_tips(self.rack.column(5)[:4], use_channels=[4, 5, 6, 7])
    self.api.submit_command.reset_mock()

  async def asyncTearDown(self):
    """Release the mocked session."""
    await self.flex.disconnect()

  async def test_modeled_tip_rack_blocks_aspirate_and_dispense_before_motion(self):
    """Physical removal cannot bypass the obstacle still present in PLR."""
    obstacle = TipRack("1000ul_tips_B2", 127.76, 85.48, 99, ordered_items={})
    self.flex.deck.assign_child_at_slot(obstacle, "B2")
    for operation in (self.head.aspirate, self.head.dispense):
      with self.subTest(operation=operation.__name__), no_volume_tracking():
        with self.assertRaisesRegex(ValueError, "Collision risk.*C2.*B2"):
          await operation(self.plate.column(0)[:4], 1, liquid_height=1)
        self.assertEqual(self.api.submit_command.await_args_list, [])
        self.assertEqual(self.head._nozzle_layout, "PARTIAL")
        self.assertEqual(sum(t is not None for t in self.head.get_mounted_tips()), 4)

  async def test_clear_slot_preserves_partial_layout_and_positions_primary_well(self):
    """The H1 primary nozzle must be over D1 when front tips cover A1-D1."""
    with no_volume_tracking():
      await self.head.aspirate(self.plate.column(0)[:4], 1, liquid_height=1)
      await self.head.dispense(self.plate.column(1)[:4], 1, liquid_height=1)
    types = [c.args[1] for c in self.api.submit_command.await_args_list]
    self.assertNotIn("configureNozzleLayout", types)
    moves = [c for c in self.api.submit_command.await_args_list if c.args[1] == "moveToCoordinates"]
    self.assertEqual(moves[0].args[2]["coordinates"], {"x": 178.3, "y": 154.2, "z": 109.0})
    self.assertEqual(moves[1].args[2]["coordinates"], {"x": 187.3, "y": 154.2, "z": 109.0})
    self.assertEqual(types.count("aspirateInPlace"), 1)
    self.assertEqual(types.count("dispenseInPlace"), 1)

  async def test_incompatible_well_spacing_is_rejected_before_motion(self):
    """Bookkeeping must never describe wells that the nozzles cannot reach."""
    wells = self.plate.column(0)[:4]
    wells[1] = self.plate.get_item("B2")
    with self.assertRaisesRegex(ValueError, "9 mm spacing"), no_volume_tracking():
      await self.head.aspirate(wells, 1)
    self.assertEqual(self.api.submit_command.await_args_list, [])

  async def test_clearance_uses_operation_height_and_tip_length(self):
    """The same obstacle clears long tips or a higher target, but not short low tips."""
    obstacle = Resource("raised_plate_B2", 127.76, 85.48, 60)
    self.flex.deck.assign_child_at_slot(obstacle, "B2")
    with no_volume_tracking():
      with self.assertRaisesRegex(ValueError, "Collision risk"):
        await self.head.aspirate(self.plate.column(0)[:4], 1, liquid_height=1)
      self.assertEqual(self.api.submit_command.await_args_list, [])
      await self.head.aspirate(self.plate.column(0)[:4], 1, liquid_height=35)
      self.api.submit_command.reset_mock()
      for tip in self.head.get_mounted_tips():
        if tip is not None:
          tip._size_z = 95.6
          tip._local_size_z = 95.6
      await self.head.dispense(self.plate.column(0)[:4], 1, liquid_height=1)
      self.assertIn("dispenseInPlace", [c.args[1] for c in self.api.submit_command.await_args_list])

  async def test_rack_can_clear_at_high_operation_z(self):
    """Tip racks use geometry too; their type alone cannot block a high operation."""
    obstacle = TipRack("obstacle_rack_B2", 127.76, 85.48, 99, ordered_items={})
    self.flex.deck.assign_child_at_slot(obstacle, "B2")
    with no_volume_tracking():
      await self.head.aspirate(
        self.plate.column(0)[:4], 1, liquid_height=1, offset=Coordinate(z=80)
      )
    self.assertIn("aspirateInPlace", [c.args[1] for c in self.api.submit_command.await_args_list])

  async def test_obstacle_absolute_height_includes_elevation(self):
    """A short plate on a tall support is not treated as a short obstacle."""
    obstacle = Resource("plate_on_support_B2", 127.76, 85.48, 14)
    self.flex.deck.assign_child_at_slot(obstacle, "B2")
    assert obstacle.location is not None
    obstacle.location += Coordinate(z=50)
    with self.assertRaisesRegex(ValueError, "Collision risk"), no_volume_tracking():
      await self.head.aspirate(self.plate.column(0)[:4], 1, liquid_height=1)
    self.assertEqual(self.api.submit_command.await_args_list, [])

  async def test_low_plate_clears_short_tips(self):
    """A normal low plate behind C2 must not prevent the partial operation."""
    self.flex.deck.assign_child_at_slot(Resource("low_plate_B2", 127.76, 85.48, 14.2), "B2")
    with no_volume_tracking():
      await self.head.aspirate(self.plate.column(0)[:4], 1, liquid_height=1)
    self.assertIn("aspirateInPlace", [c.args[1] for c in self.api.submit_command.await_args_list])

  async def test_single_nozzle_uses_the_same_height_check(self):
    """Single and partial columns both reach the geometry guard before motion."""
    self.head._channel_tips[:7] = [None] * 7
    self.head._nozzle_layout = "SINGLE"
    self.head._single_anchor = "H1"
    self.flex.deck.assign_child_at_slot(Resource("raised_plate_B2", 127.76, 85.48, 60), "B2")
    with no_volume_tracking():
      with self.assertRaisesRegex(ValueError, "Collision risk"):
        await self.head.aspirate(self.plate.get_item("A1"), 1, liquid_height=1)
      self.assertEqual(self.api.submit_command.await_args_list, [])
      await self.head.aspirate(self.plate.get_item("A1"), 1, liquid_height=35)

  async def test_touching_nozzle_plane_is_blocked(self):
    """Equality is contact, not clearance."""
    self.flex.deck.assign_child_at_slot(Resource("contact_B2", 127.76, 85.48, 52), "B2")
    with self.assertRaisesRegex(ValueError, "Collision risk"), no_volume_tracking():
      await self.head.dispense(self.plate.column(0)[:4], 1, liquid_height=1.07)
    self.assertEqual(self.api.submit_command.await_args_list, [])

  def test_twenty_mm_margin_boundary(self):
    """Reject less than 20 mm, accept exactly 20 mm and greater clearance."""
    self.flex.deck.assign_child_at_slot(Resource("obstacle_B2", 127.76, 85.48, 60), "B2")
    for gap in (19.9, 20.0, 20.1):
      with self.subTest(gap=gap):
        if gap < 20:
          with self.assertRaisesRegex(ValueError, "at least 20mm is required"):
            self.flex.deck.check_partial_nozzle_clearance("C2", "H1", 4, operation_z=60 + gap)
        else:
          self.flex.deck.check_partial_nozzle_clearance("C2", "H1", 4, operation_z=60 + gap)

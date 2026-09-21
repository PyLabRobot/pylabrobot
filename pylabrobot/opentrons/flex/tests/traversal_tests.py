"""Vertical clearance after completed Flex tip and liquid operations."""

import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.opentrons import FlexHead1, FlexHead8, FlexHead96
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import (
  Resource,
  cor_96_wellplate_360uL_Fb,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.opentrons import (
  flex_96_filtertiprack_50ul,
)


class FlexTraversalTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.io = make_api(
      pipettes=[("p1000_multi_flex", 8, 5, 1000, "left")],
      saved_position={"x": 14, "y": 74, "z": 20},
    )
    self.flex = make_flex("offline", api=self.io)
    self.rack = flex_96_filtertiprack_50ul("tips")
    self.plate = cor_96_wellplate_360uL_Fb("plate")
    self.flex.deck.assign_child_at_slot(self.rack, "D1")
    self.flex.deck.assign_child_at_slot(self.plate, "B1")
    set_tip_tracking(True)
    set_volume_tracking(True)
    await self.flex.setup()
    head = self.flex.left_pipette
    assert isinstance(head, FlexHead8)
    self.head = head

  async def asyncTearDown(self):
    await self.flex.disconnect()
    set_tip_tracking(False)
    set_volume_tracking(False)

  def assert_vertical_retraction(self):
    """The final command must address Z alone, to the computed clearance."""
    self.assertEqual(self.io.submit_command.await_args_list[-2].args[1], "savePosition")
    self.io.submit_command.assert_awaited_with(
      "run",
      "moveRelative",
      {
        "pipetteId": self.head.pipette_id,
        "axis": "z",
        "distance": self.flex.traversal_height - 20,
      },
    )

  async def test_pickup_retracts_after_tip_verification(self):
    await self.head.pick_up_tips(self.rack.column(0))
    self.assert_vertical_retraction()
    self.assertEqual(self.io.submit_command.await_args_list[-3].args[1], "getTipPresence")
    self.assertEqual(sum(t is not None for t in self.head.get_mounted_tips()), 8)

  async def test_aspirate_dispense_and_rack_return_retract(self):
    await self.head.pick_up_tips(self.rack.column(0))
    for well in self.plate.column(0):
      well.tracker.set_volume(40)
    await self.head.aspirate(self.plate.column(0), volume=20)
    self.assert_vertical_retraction()
    await self.head.dispense(self.plate.column(0), volume=20)
    self.assert_vertical_retraction()
    await self.head.drop_tips(self.rack, column=0)
    self.assert_vertical_retraction()

  async def test_does_not_lower_a_head_already_above_clearance(self):
    self.io.get_command.return_value.result["position"] = {"x": 14, "y": 74, "z": 250}
    await self.head.pick_up_tips(self.rack.column(0))
    self.assertEqual(self.io.submit_command.await_args_list[-1].args[1], "savePosition")
    self.assertFalse(
      any(c.args[1] == "moveRelative" for c in self.io.submit_command.await_args_list)
    )

  async def test_tall_labware_raises_clearance(self):
    self.flex.deck.assign_child_at_slot(Resource("obstacle", 100, 70, 150), "C2")
    self.assertGreater(self.flex.traversal_height, 150)
    await self.head.pick_up_tips(self.rack.column(0))
    self.assert_vertical_retraction()

  async def test_failed_retraction_preserves_successful_pickup_state(self):
    with patch.object(
      self.head, "_retract_to_traversal_height", AsyncMock(side_effect=RuntimeError("lift failed"))
    ):
      with self.assertRaisesRegex(RuntimeError, "lift failed"):
        await self.head.pick_up_tips(self.rack.column(0))
    self.assertTrue(all(not s.has_tip() for s in self.rack.column(0)))
    self.assertTrue(all(t is not None for t in self.head.get_mounted_tips()))

  async def test_failed_retraction_preserves_successful_aspiration_state(self):
    await self.head.pick_up_tips(self.rack.column(0))
    for well in self.plate.column(0):
      well.tracker.set_volume(40)
    with patch.object(
      self.head, "_retract_to_traversal_height", AsyncMock(side_effect=RuntimeError("lift failed"))
    ):
      with self.assertRaisesRegex(RuntimeError, "lift failed"):
        await self.head.aspirate(self.plate.column(0), volume=20)
    self.assertTrue(all(w.tracker.get_used_volume() == 20 for w in self.plate.column(0)))
    self.assertTrue(
      all(t is not None and t.tracker.get_used_volume() == 20 for t in self.head.get_mounted_tips())
    )

  async def test_other_head_sizes_retract_after_pickup(self):
    for channels, name in ((1, "p1000_single_flex"), (96, "p1000_96")):
      with self.subTest(channels=channels):
        io = make_api(pipettes=[(name, channels, 1, 1000, "left")])
        flex = make_flex("offline", api=io)
        rack = flex_96_filtertiprack_50ul("tips")
        flex.deck.assign_child_at_slot(rack, "D1")
        try:
          await flex.setup()
          head = flex.head96 if channels == 96 else flex.left_pipette
          if isinstance(head, FlexHead1):
            await head.pick_up_tips(rack.get_item("A1"))
          else:
            assert isinstance(head, FlexHead96)
            await head.pick_up_tips(rack)
          self.assertEqual(io.submit_command.await_args_list[-1].args[1], "moveRelative")
          self.assertEqual(io.submit_command.await_args_list[-1].args[2]["axis"], "z")
        finally:
          await flex.disconnect()

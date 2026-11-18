"""OT-2 eight-channel tips on the front nozzles, with mocked run operations."""

import unittest
from unittest.mock import ANY, AsyncMock, create_autospec

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import OT2, OT2_8ChannelPipette
from pylabrobot.opentrons.labware import LabwareRegistry
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.resources import Coordinate, Resource, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul

ALL = {"style": "ALL"}


class OT2FrontNozzleTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    """An eight-channel pipette, a full rack and a plate, without hardware."""
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
    self.pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
    self.robot.left_pipette = self.pipette
    self.rack = opentrons_96_filtertiprack_20ul("tips")
    self.deck.assign_child_at_slot(self.rack, 4)
    self.plate = celltreat_96_wellplate_350uL_Fb("plate")
    self.deck.assign_child_at_slot(self.plate, 2)
    for well in self.plate["A1:H1"]:
      well.tracker.set_volume(15)

  def _layouts(self):
    """The nozzle layouts sent, in order."""
    return [c.args[1] for c in self.protocol_run.mock_calls if c[0] == "configure_nozzle_layout"]

  async def test_front_tips_set_the_partial_column_and_move_by_the_front_nozzle(self) -> None:
    spots = self.rack["A1:C1"]
    tips = tuple(spot.get_tip() for spot in spots)

    await self.pipette.pick_up_tips(spots, use_channels=[5, 6, 7])

    self.assertEqual(self.pipette.tip_channels, (5, 6, 7))
    self.assertEqual(self.pipette.tips, tips)
    names = [c[0] for c in self.protocol_run.mock_calls]
    self.assertLess(names.index("configure_nozzle_layout"), names.index("pick_up_tip"))
    self.assertEqual(
      self._layouts(),
      [
        {
          "style": "QUADRANT",
          "primaryNozzle": "H1",
          "frontRightNozzle": "H1",
          "backLeftNozzle": "F1",
        }
      ],
    )
    self.protocol_run.pick_up_tip.assert_awaited_once_with(
      "left-id", ANY, "C1", Coordinate(z=tips[0].get_size_z())
    )

    self.protocol_run.reset_mock()
    wells = self.plate["F1:H1"]
    await self.pipette.aspirate(wells, volume=5)
    front_well = self.robot._deck_to_robot_frame(
      wells[-1].get_location_wrt(self.deck, "c", "c", "cavity_bottom")
    )
    self.protocol_run.move_to.assert_any_await(
      "left-id",
      front_well,
      speed=None,
      minimum_z_height=self.robot.traversal_height,
      force_direct=False,
    )
    self.assertEqual([w.tracker.volume for w in self.plate["E1:H1"]], [15, 10, 10, 10])

    self.protocol_run.reset_mock()
    await self.pipette.dispense(wells, volume=5)
    await self.pipette.return_tips()

    self.protocol_run.drop_tip.assert_awaited_once_with("left-id", ANY, "C1", Coordinate(z=10))
    self.assertEqual(self._layouts(), [ALL])
    names = [c[0] for c in self.protocol_run.mock_calls]
    self.assertLess(names.index("drop_tip"), names.index("configure_nozzle_layout"))
    self.assertEqual(self.pipette.tip_channels, ())
    self.assertTrue(all(spot.has_tip() for spot in spots))

  async def test_one_front_tip_is_the_single_layout_and_discards_back_to_all(self) -> None:
    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    self.assertEqual(self._layouts(), [{"style": "SINGLE", "primaryNozzle": "H1"}])

    # Over the trash the back nozzle stands beyond the reach the check allows; retracting
    # straight up from there is not checked again.
    self.protocol_run.get_position.return_value = Coordinate(330, 410, 80)
    await self.pipette.discard_tips()

    self.assertEqual(self._layouts()[-1], ALL)
    self.protocol_run.move_to.assert_awaited_with(
      "left-id",
      Coordinate(330, 410, self.robot.traversal_height),
      speed=None,
      minimum_z_height=None,
      force_direct=True,
    )
    self.assertFalse(self.pipette.has_tip)

  async def test_the_front_nozzle_reaches_where_the_back_one_cannot(self) -> None:
    target = Coordinate(100, -20, 60)
    await self.pipette.pick_up_tips(self.rack["H1:H1"])
    with self.assertRaisesRegex(ValueError, "reachable"):
      await self.pipette.move_to(target)
    await self.pipette.return_tips()

    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    await self.pipette.move_to(target)

  async def test_tips_behind_the_front_selection_are_refused(self) -> None:
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "undeclared tips at tips_A1, tips_B1, tips_C1"):
      await self.pipette.pick_up_tips(self.rack["D1:E1"], use_channels=[6, 7])
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertFalse(self.pipette.has_tip)

  async def test_channels_with_a_gap_or_in_the_middle_are_refused(self) -> None:
    for channels in ([0, 2], [3, 4], [6]):
      with self.subTest(channels=channels), self.assertRaisesRegex(ValueError, "use_channels"):
        await self.pipette.pick_up_tips(self.rack["A1:B1"], use_channels=channels)
    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_a_failed_front_pickup_puts_the_layout_back(self) -> None:
    self.protocol_run.pick_up_tip.side_effect = RuntimeError("pickup failed")
    with self.assertRaisesRegex(RuntimeError, "pickup failed"):
      await self.pipette.pick_up_tips(self.rack["A1:B1"], use_channels=[6, 7])
    self.assertEqual(self._layouts()[-1], ALL)
    self.assertTrue(all(spot.has_tip() for spot in self.rack["A1:B1"]))

  async def test_nozzles_without_a_tip_stay_clear_of_the_deck(self) -> None:
    # A tall box in slot 5, behind the plate: the front tip's empty neighbours hang over it.
    tall = Resource("tall_box", 120, 80, 200)
    self.deck.assign_child_at_slot(tall, 5)
    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    self.protocol_run.reset_mock()

    with self.assertRaisesRegex(ValueError, "without a tip, would come down to .* over tall_box"):
      await self.pipette.aspirate(self.plate["A1:A1"], volume=5)

    self.protocol_run.move_to.assert_not_awaited()
    self.protocol_run.aspirate_in_place.assert_not_awaited()
    self.assertEqual(self.plate.get_well("A1").tracker.volume, 15)

  async def test_the_clearance_can_be_set(self) -> None:
    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    low = Resource("low_box", 120, 80, 1)
    self.deck.assign_child_at_slot(low, 5)
    await self.pipette.aspirate(self.plate["A1:A1"], volume=1)
    self.pipette.empty_nozzle_clearance = 1000
    with self.assertRaisesRegex(ValueError, "kept 1000 mm above"):
      await self.pipette.aspirate(self.plate["A1:A1"], volume=1)


if __name__ == "__main__":
  unittest.main()

"""Partial OT-2 pickup geometry and tracking through the offline HTTP transport."""

import unittest

from pylabrobot.opentrons import OT2, OpentronsError, OT2_8ChannelPipette
from pylabrobot.opentrons.ot2.ot2_tests import FakeHTTP
from pylabrobot.resources import Coordinate, Resource, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul


class OT2PartialPickupTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    """Create two multi pipettes, a rack, and liquid sources without hardware."""
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.io = FakeHTTP(left_pipette_name="p20_multi_gen2", right_pipette_name="p20_multi_gen2")
    self.deck = OTDeck()
    self.robot = OT2("ot2.local", deck=self.deck, io=self.io, command_poll_interval=0)
    await self.robot.setup(skip_home=True)
    assert isinstance(self.robot.left_pipette, OT2_8ChannelPipette)
    self.pipette = self.robot.left_pipette
    self.rack = opentrons_96_filtertiprack_20ul("tips")
    self.rack.model = None
    self.deck.assign_child_at_slot(self.rack, 1)
    self.column = self.rack["A1:H1"]
    self.plate = celltreat_96_wellplate_350uL_Fb("plate")
    self.deck.assign_child_at_slot(self.plate, 2)
    self.sources = self.plate["A1:H1"]
    self.destinations = self.plate["A2:H2"]
    for well in self.sources:
      well.tracker.set_volume(15)

  async def asyncTearDown(self) -> None:
    """Close the fake run and restore the global tracking switches."""
    await self.robot.stop()
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_partial_pickup_liquid_transfer_and_return(self) -> None:
    """E-H tips map to nozzles 0-3 and support a complete protocol on either mount."""
    for pipette in self.robot.pipettes:
      assert isinstance(pipette, OT2_8ChannelPipette)
      with self.subTest(mount=pipette.mount):
        selected = self.rack["E1:H1"]
        original_tips = tuple(spot.get_tip() for spot in selected)
        before = len(self.io.commands)
        await pipette.pick_up_tips(selected)
        self.assertEqual(pipette.tip_channels, (0, 1, 2, 3))
        self.assertEqual(pipette.tips, original_tips)
        self.assertTrue(all(spot.has_tip() for spot in self.column[:4]))
        await pipette.mix(self.sources[:4], volume=5, repetitions=2)
        await pipette.aspirate(self.sources[:4], volume=5)
        await pipette.dispense(self.destinations[:4], volume=5)
        await pipette.return_tips()
        self.assertEqual(pipette.tip_channels, ())
        for spot, tip in zip(selected, original_tips):
          self.assertIs(spot.get_tip(), tip)
        commands = self.io.commands[before:]
        for kind in ("pickUpTip", "dropTip"):
          selected_commands = [c for c in commands if c["commandType"] == kind]
          self.assertEqual(len(selected_commands), 1)
          self.assertEqual(selected_commands[0]["params"]["wellName"], "E1")
          self.assertEqual(selected_commands[0]["params"]["pipetteId"], pipette.pipette_id)
    self.assertEqual([w.tracker.volume for w in self.sources], [5] * 4 + [15] * 4)
    self.assertEqual([w.tracker.volume for w in self.destinations], [10] * 4 + [0] * 4)

  async def test_pickup_grabbing_undeclared_tips_below_is_rejected(self) -> None:
    """A-F from a full column would also grab G/H, and must fail before commands or staging."""
    before = len(self.io.calls)
    with self.assertRaisesRegex(ValueError, "undeclared tips"):
      await self.pipette.pick_up_tips(self.column[:6])
    self.assertEqual(len(self.io.calls), before)
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    self.assertEqual(self.pipette.tip_channels, ())

  async def test_allow_undeclared_absorbs_grabbed_tips(self) -> None:
    """Accepting A-F plus occupied G/H tracks and returns all eight tips with a warning."""
    with self.assertWarnsRegex(UserWarning, "undeclared tips"):
      await self.pipette.pick_up_tips(self.column[:6], allow_undeclared_tip_pickup=True)
    self.assertEqual(self.pipette.tip_channels, tuple(range(8)))
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    await self.pipette.return_tips()
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_default_and_per_call_override(self) -> None:
    """The OT2 default can be overridden for one pickup without changing the default."""
    self.robot.allow_undeclared_tip_pickup = True
    before = len(self.io.calls)
    with self.assertRaisesRegex(ValueError, "undeclared tips"):
      await self.pipette.pick_up_tips(self.column[:6], allow_undeclared_tip_pickup=False)
    self.assertEqual(len(self.io.calls), before)
    with self.assertWarns(UserWarning):
      await self.pipette.pick_up_tips(self.column[:6])
    self.assertTrue(self.robot.allow_undeclared_tip_pickup)
    await self.pipette.discard_tips()
    self.assertEqual(self.pipette.tip_channels, ())

  async def test_sparse_extra_tips_keep_their_nozzle_indices(self) -> None:
    """An empty G spot must not shift an accepted H tip from nozzle 7 to nozzle 6."""
    self.column[6].tracker.remove_tip(commit=True)
    channels = (0, 1, 2, 3, 4, 5, 7)
    with self.assertWarns(UserWarning):
      await self.pipette.pick_up_tips(self.column[:6], allow_undeclared_tip_pickup=True)
    self.assertEqual(self.pipette.tip_channels, channels)
    before = len(self.io.calls)
    with self.assertRaises(ValueError):
      await self.pipette.aspirate(self.sources[:7], volume=5)
    self.assertEqual(len(self.io.calls), before)
    await self.pipette.aspirate([self.sources[i] for i in channels], volume=5)
    await self.pipette.dispense([self.destinations[i] for i in channels], volume=5)
    await self.pipette.return_tips()
    self.assertEqual([w.tracker.volume for w in self.sources], [10] * 6 + [15, 10])
    self.assertEqual([w.tracker.volume for w in self.destinations], [5] * 6 + [0, 5])
    self.assertEqual([spot.has_tip() for spot in self.column], [True] * 6 + [False, True])

  async def test_empty_spots_below_selection_allow_partial_pickup(self) -> None:
    """Unused nozzles over empty spots do not count as extra tips."""
    for spot in self.column[6:]:
      spot.tracker.remove_tip(commit=True)
    await self.pipette.pick_up_tips(self.column[:6])
    self.assertEqual(self.pipette.tip_channels, tuple(range(6)))
    await self.pipette.return_tips()
    self.assertEqual([spot.has_tip() for spot in self.column], [True] * 6 + [False] * 2)

  async def test_single_tip_uses_nozzle_zero(self) -> None:
    """A single selected tip stays on nozzle 0 and transfers only one well's volume."""
    for spot in self.column[1:]:
      spot.tracker.remove_tip(commit=True)
    await self.pipette.pick_up_tips(self.column[:1])
    self.assertEqual(self.pipette.tip_channels, (0,))
    await self.pipette.aspirate(self.sources[:1], volume=5)
    await self.pipette.dispense(self.destinations[:1], volume=5)
    await self.pipette.return_tips()
    self.assertEqual([w.tracker.volume for w in self.sources], [10] + [15] * 7)
    self.assertEqual([w.tracker.volume for w in self.destinations], [5] + [0] * 7)

  async def test_invalid_partial_geometry_sends_nothing(self) -> None:
    """Reject gaps, duplicates, reversed rows, mixed columns/racks, and non-pitch spacing."""
    other = opentrons_96_filtertiprack_20ul("other")
    self.deck.assign_child_at_slot(other, 3)
    before = len(self.io.calls)
    for targets in (
      [],
      [self.column[0]] * 9,
      [self.column[0], self.column[2]],
      [self.column[0], self.column[0]],
      self.column[3::-1],
      [self.column[0], self.rack.get_item("B2")],
      [self.column[0], other.get_item("B1")],
    ):
      with self.subTest(targets=targets), self.assertRaises(ValueError):
        await self.pipette.pick_up_tips(targets, allow_undeclared_tip_pickup=True)
    assert self.column[1].location is not None
    self.column[1].location += Coordinate(y=0.1)
    with self.assertRaises(ValueError):
      await self.pipette.pick_up_tips(self.column[:2], allow_undeclared_tip_pickup=True)
    self.assertEqual(len(self.io.calls), before)
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_pickup_failure_rolls_back_declared_and_extra_tips(self) -> None:
    """A rejected command must not transfer ownership of either requested or extra tips."""
    self.io.fail_command_type = "pickUpTip"
    with self.assertWarns(UserWarning), self.assertRaises(OpentronsError):
      await self.pipette.pick_up_tips(self.column[:6], allow_undeclared_tip_pickup=True)
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    self.assertEqual(self.pipette.tips, ())
    self.assertEqual(self.pipette.tip_channels, ())
    self.io.fail_command_type = None
    with self.assertWarns(UserWarning):
      await self.pipette.pick_up_tips(self.column[:6], allow_undeclared_tip_pickup=True)
    self.assertEqual(len(self.pipette.tips), 8)

  async def test_partial_liquid_failure_rolls_back_all_mounted_tips(self) -> None:
    """An unsuccessful stroke preserves source and tip volumes for a partial layout."""
    await self.pipette.pick_up_tips(self.rack["E1:H1"])
    self.io.fail_command_type = "aspirateInPlace"
    with self.assertRaises(OpentronsError):
      await self.pipette.aspirate(self.sources[:4], volume=5)
    self.assertEqual([w.tracker.volume for w in self.sources], [15] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [0] * 4)
    self.assertEqual(self.pipette.tip_channels, (0, 1, 2, 3))

  async def test_drop_targets_and_failed_return_preserve_mounted_layout(self) -> None:
    """Drop must address every mounted tip, and a failed return keeps their origins."""
    await self.pipette.pick_up_tips(self.rack["E1:H1"])
    before = len(self.io.calls)
    with self.assertRaises(ValueError):
      await self.pipette.drop_tips(self.rack["E1:G1"])
    self.assertEqual(len(self.io.calls), before)
    self.io.fail_command_type = "dropTip"
    with self.assertRaises(OpentronsError):
      await self.pipette.return_tips()
    self.assertEqual(self.pipette.tip_channels, (0, 1, 2, 3))
    self.assertTrue(all(not spot.has_tip() for spot in self.column[4:]))
    self.io.fail_command_type = None
    await self.pipette.return_tips()
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_collision_checks_include_bare_nozzles_beyond_partial_selection(self) -> None:
    """Unused front nozzles can collide with an obstacle beyond the rack's front edge."""
    primary = self.column[4].get_location_wrt(self.deck, "c", "c", "b")
    obstacle = Resource("obstacle", 10, 10, 100)
    self.deck.assign_child_at_slot(obstacle, 3)
    obstacle.location = Coordinate(primary.x - 5, primary.y - 63, 0) - self.deck.slot_locations[2]
    before = len(self.io.calls)
    with self.assertRaisesRegex(ValueError, "obstacle"):
      await self.pipette.pick_up_tips(self.rack["E1:H1"])
    self.assertEqual(len(self.io.calls), before)
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_retraction_failure_keeps_completed_partial_pickup(self) -> None:
    """A failed retraction retains the nozzle layout from a successful pickup."""
    self.io.fail_command_type = "savePosition"
    with self.assertRaises(OpentronsError):
      await self.pipette.pick_up_tips(self.rack["E1:H1"])
    self.assertEqual(self.pipette.tip_channels, (0, 1, 2, 3))
    self.assertEqual(len(self.pipette.tips), 4)
    self.assertTrue(all(not spot.has_tip() for spot in self.column[4:]))

  async def test_disabled_tracking_still_records_extra_nozzle_ownership(self) -> None:
    """Tracking switches do not hide extra tips from the mounted layout."""
    set_tip_tracking(False)
    set_volume_tracking(False)
    with self.assertWarns(UserWarning):
      await self.pipette.pick_up_tips(self.column[:6], allow_undeclared_tip_pickup=True)
    self.assertEqual(self.pipette.tip_channels, tuple(range(8)))
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    await self.pipette.aspirate(self.sources, volume=5)
    await self.pipette.discard_tips()
    self.assertEqual(self.pipette.tip_channels, ())


if __name__ == "__main__":
  unittest.main()

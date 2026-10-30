import asyncio
import unittest
from unittest.mock import ANY, AsyncMock, call, create_autospec

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import OT2, OpentronsError, OT2_8ChannelPipette, OT2SingleChannelPipette
from pylabrobot.opentrons.labware import LabwareRegistry
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.resources import Coordinate, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.errors import (
  HasTipError,
  NoTipError,
  TooLittleLiquidError,
  TooLittleVolumeError,
)
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul


class OT2SingleChannelTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    """Use real resources and trackers with mocked run operations."""
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
    self.pipette = OT2SingleChannelPipette(self.robot, "left", "p20_single_gen2", "pipette-id")
    self.robot.left_pipette = self.pipette
    self.tips = opentrons_96_filtertiprack_20ul("tips")
    self.deck.assign_child_at_slot(self.tips, 1)
    self.plate = celltreat_96_wellplate_350uL_Fb("plate")
    self.deck.assign_child_at_slot(self.plate, 2)

  async def test_failed_aspiration_rolls_back_volume_trackers(self) -> None:
    pipette = self.pipette
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.protocol_run.aspirate_in_place.side_effect = OpentronsError("aspiration failed")

    with self.assertRaisesRegex(OpentronsError, "aspiration failed"):
      await pipette.aspirate(source, volume=10)

    self.assertAlmostEqual(source.tracker.get_used_volume(), 15)
    assert pipette.tip is not None
    self.assertAlmostEqual(pipette.tip.tracker.get_used_volume(), 0)

  async def test_rejected_aspiration_preserves_both_volume_trackers(self) -> None:
    pipette = self.pipette
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    pipette.tip.tracker.set_volume(15)
    source = self.plate.get_well("A1")
    source.tracker.set_volume(30)
    self.protocol_run.reset_mock()

    with self.assertRaises(TooLittleVolumeError):
      await pipette.aspirate(source, volume=10)

    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertEqual(source.tracker.get_used_volume(), 30)
    self.assertEqual(source.tracker.volume, 30)
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 15)
    self.assertEqual(pipette.tip.tracker.volume, 15)

  async def test_rejected_dispense_preserves_both_volume_trackers(self) -> None:
    pipette = self.pipette
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    pipette.tip.tracker.set_volume(10)
    destination = self.plate.get_well("A1")
    destination.tracker.set_volume(destination.tracker.max_volume - 5)
    initial_volume = destination.tracker.get_used_volume()
    self.protocol_run.reset_mock()

    with self.assertRaises(TooLittleVolumeError):
      await pipette.dispense(destination, volume=10)

    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertEqual(destination.tracker.get_used_volume(), initial_volume)
    self.assertEqual(destination.tracker.volume, initial_volume)
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 10)
    self.assertEqual(pipette.tip.tracker.volume, 10)

  async def test_moved_or_unassigned_loaded_rack_is_rejected_before_a_command(self) -> None:
    pipette = self.pipette
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    await pipette.return_tip()
    self.deck.unassign_child_resource(self.tips)
    self.protocol_run.reset_mock()

    with self.assertRaisesRegex(ValueError, "assigned directly"):
      await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.deck.assign_child_at_slot(self.tips, slot=5)
    with self.assertRaisesRegex(ValueError, "loaded in slot 1"):
      await pipette.pick_up_tip(self.tips.get_item("A1"))

    self.protocol_run.pick_up_tip.assert_not_awaited()
    self.protocol_run.drop_tip.assert_not_awaited()
    self.protocol_run.load_labware.assert_not_awaited()
    self.protocol_run.get_position.assert_not_awaited()
    self.assertTrue(self.tips.get_item("A1").has_tip())
    self.assertFalse(pipette.has_tip)

  async def test_drop_into_moved_rack_preserves_tip_state(self) -> None:
    pipette = self.pipette
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.deck.unassign_child_resource(self.tips)
    self.deck.assign_child_at_slot(self.tips, slot=5)
    self.protocol_run.reset_mock()

    with self.assertRaisesRegex(ValueError, "loaded in slot 1"):
      await pipette.return_tip()

    self.protocol_run.pick_up_tip.assert_not_awaited()
    self.protocol_run.drop_tip.assert_not_awaited()
    self.protocol_run.load_labware.assert_not_awaited()
    self.protocol_run.get_position.assert_not_awaited()
    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertTrue(pipette.has_tip)

  async def test_mix_rejects_insufficient_liquid_or_tip_capacity_before_moving(self) -> None:
    pipette = self.pipette
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    source = self.plate.get_well("A1")
    self.protocol_run.reset_mock()
    for source_volume, tip_volume, error in (
      (0, 0, TooLittleLiquidError),
      (30, 15, TooLittleVolumeError),
    ):
      with self.subTest(source_volume=source_volume, tip_volume=tip_volume):
        source.tracker.set_volume(source_volume)
        pipette.tip.tracker.set_volume(tip_volume)
        with self.assertRaises(error):
          await pipette.mix(source, volume=10, repetitions=3)
        self.assertEqual(self.protocol_run.mock_calls, [])
        self.assertEqual(source.tracker.get_used_volume(), source_volume)
        self.assertEqual(pipette.tip.tracker.get_used_volume(), tip_volume)

  async def test_mix_tracks_each_transfer_when_dispense_fails(self) -> None:
    pipette = self.pipette
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    self.protocol_run.dispense_in_place.side_effect = [None, OpentronsError("dispense failed")]

    with self.assertRaisesRegex(OpentronsError, "dispense failed"):
      await pipette.mix(source, volume=10, repetitions=3)

    self.assertEqual(source.tracker.get_used_volume(), 5)
    self.assertEqual(source.tracker.volume, 5)
    assert pipette.tip is not None
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 10)
    self.assertEqual(pipette.tip.tracker.volume, 10)

  async def test_unreachable_move_is_rejected_before_a_run_command(self) -> None:
    pipette = self.pipette
    self.protocol_run.reset_mock()

    with self.assertRaisesRegex(ValueError, "reachable"):
      await pipette.move_to(Coordinate(500, 0, 10))

    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_negative_z_move_is_rejected_before_a_run_command(self) -> None:
    pipette = self.pipette
    self.protocol_run.reset_mock()

    with self.assertRaisesRegex(ValueError, "non-negative"):
      await pipette.move_to(Coordinate(10, 10, -1))

    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_pickup_uses_tip_height_and_retracts_after_committing_ownership(self) -> None:
    origin = self.tips.get_item("A1")
    tip = origin.get_tip()

    async def read_position(pipette_id: str) -> Coordinate:
      """Check ownership at the start of retraction."""
      self.assertIs(self.pipette.tip, tip)
      self.assertFalse(origin.has_tip())
      return Coordinate(30.25, 40.5, 20)

    self.protocol_run.get_position.side_effect = read_position

    await self.pipette.pick_up_tip(origin)

    self.protocol_run.pick_up_tip.assert_awaited_once_with(
      "pipette-id", ANY, "A1", Coordinate(z=tip.get_size_z())
    )
    self.protocol_run.get_position.assert_awaited_once_with("pipette-id")
    self.protocol_run.move_to.assert_awaited_once_with(
      "pipette-id",
      Coordinate(30.25, 40.5, 120),
      speed=None,
      minimum_z_height=None,
      force_direct=True,
    )

  async def test_return_restores_the_same_tip_and_reuses_loaded_labware(self) -> None:
    origin = self.tips.get_item("A1")
    tip = origin.get_tip()
    await self.pipette.pick_up_tip(origin)
    self.protocol_run.drop_tip.reset_mock()

    await self.pipette.return_tip()

    self.assertIs(origin.get_tip(), tip)
    self.assertFalse(self.pipette.has_tip)
    self.protocol_run.drop_tip.assert_awaited_once_with("pipette-id", ANY, "A1", Coordinate(z=10))
    self.protocol_run.load_labware.assert_awaited_once()

  async def test_pickup_retraction_failure_keeps_completed_pickup(self) -> None:
    origin = self.tips.get_item("A1")
    for retraction in (self.protocol_run.get_position, self.protocol_run.move_to):
      with self.subTest(retraction=retraction):
        tip = origin.get_tip()
        failure = OpentronsError("retraction failed")
        retraction.side_effect = failure

        with self.assertRaises(OpentronsError) as raised:
          await self.pipette.pick_up_tip(origin)

        self.assertIs(raised.exception, failure)
        self.assertIs(self.pipette.tip, tip)
        self.assertFalse(origin.has_tip())
        retraction.side_effect = None
        await self.pipette.return_tip()
        self.assertIs(origin.get_tip(), tip)

  async def test_move_retracts_from_reported_position_without_lowering(self) -> None:
    for z in (10, 120, 150):
      with self.subTest(z=z):
        self.protocol_run.reset_mock()
        self.protocol_run.get_position.return_value = Coordinate(101, 202, z)

        await self.pipette.move_to(Coordinate(100, 200, z))

        expected = [
          call.move_to(
            "pipette-id",
            Coordinate(100, 200, z),
            speed=None,
            minimum_z_height=None,
            force_direct=False,
          ),
          call.get_position("pipette-id"),
        ]
        if z < 120:
          expected.append(
            call.move_to(
              "pipette-id",
              Coordinate(101, 202, 120),
              speed=None,
              minimum_z_height=None,
              force_direct=True,
            )
          )
        self.assertEqual(self.protocol_run.mock_calls, expected)

  async def test_drop_retracts_from_reported_position_at_configured_height(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    self.tips.set_tip_state({"A12": False})
    self.robot.traversal_height = 140
    self.protocol_run.reset_mock()

    await self.pipette.drop_tip(self.tips.get_item("A12"))

    self.protocol_run.drop_tip.assert_awaited_once_with("pipette-id", ANY, "A12", Coordinate(z=10))
    self.protocol_run.get_position.assert_awaited_once_with("pipette-id")
    self.protocol_run.move_to.assert_awaited_once_with(
      "pipette-id",
      Coordinate(30.25, 40.5, 140),
      speed=None,
      minimum_z_height=None,
      force_direct=True,
    )
    self.assertTrue(self.tips.get_item("A12").has_tip())
    self.assertFalse(self.pipette.has_tip)

  async def test_drop_does_not_lower_a_nozzle_above_traversal_height(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    self.protocol_run.get_position.return_value = Coordinate(30.25, 40.5, 130)
    self.protocol_run.reset_mock()

    await self.pipette.return_tip()

    self.protocol_run.drop_tip.assert_awaited_once()
    self.protocol_run.get_position.assert_awaited_once()
    self.protocol_run.move_to.assert_not_awaited()
    self.assertFalse(self.pipette.has_tip)

  async def test_failed_drop_keeps_tip_and_does_not_retract(self) -> None:
    origin = self.tips.get_item("A1")
    await self.pipette.pick_up_tip(origin)
    tip = self.pipette.tip
    self.protocol_run.reset_mock()
    self.protocol_run.drop_tip.side_effect = OpentronsError("drop failed")

    with self.assertRaisesRegex(OpentronsError, "drop failed"):
      await self.pipette.return_tip()

    self.assertIs(self.pipette.tip, tip)
    self.assertFalse(origin.has_tip())
    self.protocol_run.get_position.assert_not_awaited()
    self.protocol_run.move_to.assert_not_awaited()

  async def test_discard_uses_fixed_trash_offset_and_retracts(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    self.protocol_run.reset_mock()

    await self.pipette.discard_tip()

    self.assertEqual(
      self.protocol_run.mock_calls,
      [
        call.discard_tip_in_fixed_trash("pipette-id", Coordinate(z=10)),
        call.get_position("pipette-id"),
        call.move_to(
          "pipette-id",
          Coordinate(30.25, 40.5, 120),
          speed=None,
          minimum_z_height=None,
          force_direct=True,
        ),
      ],
    )
    self.assertFalse(self.pipette.has_tip)

  async def test_drop_retraction_failure_keeps_completed_drop(self) -> None:
    for operation, returned in ((self.pipette.return_tip, True), (self.pipette.discard_tip, False)):
      for retraction in (self.protocol_run.get_position, self.protocol_run.move_to):
        with self.subTest(operation=operation.__name__, retraction=retraction):
          origin = next(spot for spot in self.tips.get_all_items() if spot.has_tip())
          await self.pipette.pick_up_tip(origin)
          retraction.side_effect = OpentronsError("retraction failed")

          with self.assertRaisesRegex(OpentronsError, "retraction failed"):
            await operation()

          self.assertIsNone(self.pipette.tip)
          self.assertFalse(self.pipette.has_tip)
          self.assertEqual(origin.has_tip(), returned)
          with self.assertRaisesRegex(RuntimeError, "origin is unknown"):
            await self.pipette.return_tip()
          retraction.side_effect = None

  async def test_aspiration_uses_default_flow_rate_and_commits_both_trackers(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)

    await self.pipette.aspirate(source, volume=10)

    self.protocol_run.aspirate_in_place.assert_awaited_once_with("pipette-id", 10, 3.78)
    self.assertEqual(source.tracker.volume, 5)
    assert self.pipette.tip is not None
    self.assertEqual(self.pipette.tip.tracker.volume, 10)

  async def test_dispense_uses_default_flow_rate_and_commits_both_trackers(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    assert self.pipette.tip is not None
    self.pipette.tip.tracker.set_volume(10)
    destination = self.plate.get_well("B1")

    await self.pipette.dispense(destination, volume=10)

    self.protocol_run.dispense_in_place.assert_awaited_once_with("pipette-id", 10, 7.56)
    self.assertEqual(destination.tracker.volume, 10)
    self.assertEqual(self.pipette.tip.tracker.volume, 0)

  async def test_liquid_operations_retract_from_reported_position(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    assert self.pipette.tip is not None
    well = self.plate.get_well("A1")
    for operation, initial_tip in ((self.pipette.aspirate, 0), (self.pipette.dispense, 10)):
      with self.subTest(operation=operation.__name__):
        well.tracker.set_volume(30)
        self.pipette.tip.tracker.set_volume(initial_tip)
        self.protocol_run.reset_mock()

        await operation(well, volume=10)

        self.assertEqual(
          self.protocol_run.mock_calls[-2:],
          [
            call.get_position("pipette-id"),
            call.move_to(
              "pipette-id",
              Coordinate(30.25, 40.5, 120),
              speed=None,
              minimum_z_height=None,
              force_direct=True,
            ),
          ],
        )

  async def test_transfer_retraction_failure_keeps_completed_transfer(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    assert self.pipette.tip is not None
    well = self.plate.get_well("A1")
    for operation, initial_tip, expected_tip, expected_well in (
      (self.pipette.aspirate, 0, 10, 20),
      (self.pipette.dispense, 10, 0, 40),
    ):
      with self.subTest(operation=operation.__name__):
        well.tracker.set_volume(30)
        self.pipette.tip.tracker.set_volume(initial_tip)
        self.protocol_run.move_to.side_effect = [None, OpentronsError("retraction failed")]

        with self.assertRaisesRegex(OpentronsError, "retraction failed"):
          await operation(well, volume=10)

        self.assertEqual(well.tracker.volume, expected_well)
        self.assertEqual(well.tracker.get_used_volume(), expected_well)
        self.assertEqual(self.pipette.tip.tracker.volume, expected_tip)
        self.assertEqual(self.pipette.tip.tracker.get_used_volume(), expected_tip)

  async def test_mix_alternates_strokes_and_retracts_once(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    assert self.pipette.tip is not None
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    self.pipette.tip.tracker.set_volume(5)
    self.protocol_run.reset_mock()

    await self.pipette.mix(source, volume=10, repetitions=3)

    self.assertEqual(source.tracker.volume, 15)
    self.assertEqual(self.pipette.tip.tracker.volume, 5)
    self.assertEqual(
      self.protocol_run.mock_calls,
      [
        call.move_to("pipette-id", ANY, speed=None, minimum_z_height=120, force_direct=False),
      ]
      + [
        call.aspirate_in_place("pipette-id", 10, 3.78),
        call.dispense_in_place("pipette-id", 10, 7.56),
      ]
      * 3
      + [
        call.get_position("pipette-id"),
        call.move_to(
          "pipette-id",
          Coordinate(30.25, 40.5, 120),
          speed=None,
          minimum_z_height=None,
          force_direct=True,
        ),
      ],
    )

  async def test_concurrent_pickup_waits_then_rejects_a_second_tip(self) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def pick_up_tip(*args: object) -> None:
      """Hold the first pickup while the second operation attempts to start."""
      entered.set()
      await release.wait()

    self.protocol_run.pick_up_tip.side_effect = pick_up_tip
    first = asyncio.create_task(self.pipette.pick_up_tip(self.tips.get_item("A1")))
    self.addAsyncCleanup(asyncio.wait_for, first, timeout=1)
    self.addCleanup(release.set)
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(self.pipette.pick_up_tip(self.tips.get_item("A2")))
    try:
      await asyncio.sleep(0)
      self.assertFalse(second.done())
      self.protocol_run.pick_up_tip.assert_awaited_once()
    finally:
      release.set()
      results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)

    self.assertIsNone(results[0])
    self.assertIsInstance(results[1], RuntimeError)
    self.assertIn("already has a tip", str(results[1]))
    self.protocol_run.pick_up_tip.assert_awaited_once()
    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertTrue(self.tips.get_item("A2").has_tip())

  async def test_concurrent_dispense_waits_for_aspiration_to_commit(self) -> None:
    await self.pipette.pick_up_tip(self.tips.get_item("A1"))
    source, destination = self.plate.get_well("A1"), self.plate.get_well("B1")
    source.tracker.set_volume(15)
    entered, release = asyncio.Event(), asyncio.Event()

    async def aspirate(*args: object) -> None:
      """Hold aspiration before its tracker transaction commits."""
      entered.set()
      await release.wait()

    self.protocol_run.aspirate_in_place.side_effect = aspirate
    first = asyncio.create_task(self.pipette.aspirate(source, volume=10))
    self.addAsyncCleanup(asyncio.wait_for, first, timeout=1)
    self.addCleanup(release.set)
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(self.pipette.dispense(destination, volume=10))
    try:
      await asyncio.sleep(0)
      self.assertFalse(second.done())
      self.protocol_run.dispense_in_place.assert_not_awaited()
    finally:
      release.set()
      await asyncio.wait_for(asyncio.gather(first, second), 1)

    self.assertEqual(source.tracker.volume, 5)
    self.assertEqual(destination.tracker.volume, 10)
    assert self.pipette.tip is not None
    self.assertEqual(self.pipette.tip.tracker.volume, 0)

  async def test_concurrent_discard_waits_then_rejects_an_already_returned_tip(self) -> None:
    origin = self.tips.get_item("A1")
    await self.pipette.pick_up_tip(origin)
    entered, release = asyncio.Event(), asyncio.Event()

    async def drop_tip(*args: object) -> None:
      """Hold the return before releasing ownership of the tip."""
      entered.set()
      await release.wait()

    self.protocol_run.drop_tip.side_effect = drop_tip
    first = asyncio.create_task(self.pipette.return_tip())
    self.addAsyncCleanup(asyncio.wait_for, first, timeout=1)
    self.addCleanup(release.set)
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(self.pipette.discard_tip())
    try:
      await asyncio.sleep(0)
      self.assertFalse(second.done())
      self.protocol_run.discard_tip_in_fixed_trash.assert_not_awaited()
    finally:
      release.set()
      results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)

    self.assertIsNone(results[0])
    self.assertIsInstance(results[1], RuntimeError)
    self.assertIn("does not have a tip", str(results[1]))
    self.protocol_run.drop_tip.assert_awaited_once()
    self.protocol_run.discard_tip_in_fixed_trash.assert_not_awaited()
    self.assertTrue(origin.has_tip())
    self.assertFalse(self.pipette.has_tip)

  async def test_stop_waits_for_movement_and_its_retraction(self) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def move(*args: object, **kwargs: object) -> None:
      """Keep movement in flight while stop waits for the operation lock."""
      entered.set()
      await release.wait()

    self.protocol_run.move_to.side_effect = move
    movement = asyncio.create_task(self.pipette.move_to(Coordinate(100, 100, 100)))
    self.addAsyncCleanup(asyncio.wait_for, movement, timeout=1)
    self.addCleanup(release.set)
    await asyncio.wait_for(entered.wait(), timeout=1)
    stop = asyncio.create_task(self.robot.stop())
    try:
      await asyncio.sleep(0)
      self.assertFalse(stop.done())
      self.protocol_run.stop.assert_not_awaited()
    finally:
      release.set()
      await asyncio.wait_for(asyncio.gather(movement, stop), 1)

    self.assertEqual(
      self.protocol_run.mock_calls,
      [
        call.move_to(
          "pipette-id",
          Coordinate(100, 100, 100),
          speed=None,
          minimum_z_height=None,
          force_direct=False,
        ),
        call.get_position("pipette-id"),
        call.move_to(
          "pipette-id",
          Coordinate(30.25, 40.5, 120),
          speed=None,
          minimum_z_height=None,
          force_direct=True,
        ),
        call.stop(),
      ],
    )
    self.assertIsNone(self.robot._run)


class OT2MultiChannelTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    """Bind real pipettes and resources to mocked run operations."""
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
    self.pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-pipette-id")
    self.robot.left_pipette = self.pipette
    self.robot.right_pipette = OT2SingleChannelPipette(
      self.robot, "right", "p20_single_gen2", "right-pipette-id"
    )
    self.tips = opentrons_96_filtertiprack_20ul("tips")
    self.deck.assign_child_at_slot(self.tips, 1)
    self.plate = celltreat_96_wellplate_350uL_Fb("plate")
    self.deck.assign_child_at_slot(self.plate, 2)
    self.column = self.tips["A1:H1"]
    self.sources = self.plate["A1:H1"]
    self.destinations = self.plate["A2:H2"]
    for well in self.sources:
      well.tracker.set_volume(15)

  async def test_pickup_rejects_missing_or_mixed_models_before_commands(self) -> None:
    """A tip type is its model, and every nozzle must have a defined matching model."""
    last_tip = self.column[-1].get_tip()
    for model in (None, "different_tip_model"):
      with self.subTest(model=model):
        last_tip.model = model
        self.protocol_run.reset_mock()
        with self.assertRaises(ValueError):
          await self.pipette.pick_up_tips(self.column)
        self.assertEqual(self.protocol_run.mock_calls, [])
        self.assertTrue(all(spot.has_tip() for spot in self.column))
        self.assertFalse(self.pipette.has_tip)

  def test_constructors_reject_models_with_the_wrong_channel_count(self) -> None:
    """A concrete class cannot bind a model whose nozzle count contradicts its API."""
    with self.assertRaisesRegex(ValueError, "8-channel"):
      OT2SingleChannelPipette(self.robot, "left", "p20_multi_gen2", "left-pipette-id")
    with self.assertRaisesRegex(ValueError, "1-channel"):
      OT2_8ChannelPipette(self.robot, "right", "p20_single_gen2", "right-pipette-id")

  async def test_single_mount_and_multi_mount_keep_independent_tips(self) -> None:
    """A multi on one mount does not change the other mount's single-channel API."""
    single = self.robot.right_pipette
    assert isinstance(single, OT2SingleChannelPipette)
    self.assertEqual(single.num_channels, 1)
    await asyncio.gather(
      self.pipette.pick_up_tips(self.column), single.pick_up_tip(self.tips.get_item("A3"))
    )
    await self.pipette.discard_tips()
    self.assertTrue(single.has_tip)
    self.assertIsNotNone(single.tip)
    await single.return_tip()
    self.assertTrue(self.tips.get_item("A3").has_tip())
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    self.assertEqual(
      self.protocol_run.pick_up_tip.await_args_list,
      [
        call("left-pipette-id", ANY, "A1", ANY),
        call("right-pipette-id", ANY, "A3", ANY),
      ],
    )

  async def test_incomplete_or_misaligned_pickups_send_nothing(self) -> None:
    """Reject undeclared extra tips, repeated, mixed-column, and reversed targets before I/O."""
    self.protocol_run.reset_mock()
    for spots in (
      [],
      self.column[:7],
      [self.column[0]] * 8,
      list(reversed(self.column)),
      self.column[:7] + [self.tips.get_item("H2")],
    ):
      with self.subTest(spots=spots), self.assertRaises(ValueError):
        await self.pipette.pick_up_tips(spots)
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_missing_last_tip_and_failed_pickup_preserve_the_rack(self) -> None:
    """A missing eighth tip or failed command must not consume the first seven tips."""
    last_tip = self.column[-1].get_tip()
    self.column[-1].tracker.remove_tip(commit=True)
    self.protocol_run.reset_mock()
    with self.assertRaises(NoTipError):
      await self.pipette.pick_up_tips(self.column)
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertTrue(all(spot.has_tip() for spot in self.column[:-1]))
    self.column[-1].tracker.add_tip(last_tip)
    self.protocol_run.pick_up_tip.side_effect = OpentronsError("pick_up_tip failed")
    with self.assertRaises(OpentronsError):
      await self.pipette.pick_up_tips(self.column)
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    self.assertFalse(self.pipette.has_tip)

  async def test_drop_preflight_and_command_failure_roll_back_every_spot(self) -> None:
    """A late occupied destination or command failure preserves all mounted tips."""
    await self.pipette.pick_up_tips(self.column)
    mounted = self.pipette.tips
    destinations = self.tips["A2:H2"]
    for spot in destinations[:-1]:
      spot.tracker.remove_tip(commit=True)
    self.protocol_run.reset_mock()
    with self.assertRaises(HasTipError):
      await self.pipette.drop_tips(destinations)
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.assertTrue(all(not spot.has_tip() for spot in destinations[:-1]))
    self.protocol_run.drop_tip.side_effect = OpentronsError("drop_tip failed")
    with self.assertRaises(OpentronsError):
      await self.pipette.return_tips()
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    self.assertIs(self.pipette.tips, mounted)
    self.protocol_run.drop_tip.side_effect = None
    destinations[-1].tracker.remove_tip(commit=True)
    await self.pipette.drop_tips(destinations)
    for spot, tip in zip(destinations, mounted):
      self.assertIs(spot.get_tip(), tip)

  async def test_last_well_or_tip_capacity_failure_rolls_back_the_whole_transfer(self) -> None:
    """Every nozzle is validated before motion, with earlier staged updates undone."""
    await self.pipette.pick_up_tips(self.column)
    self.sources[-1].tracker.set_volume(0)
    self.protocol_run.reset_mock()
    with self.assertRaises(TooLittleLiquidError):
      await self.pipette.aspirate(self.sources, volume=10)
    self.assertEqual([w.tracker.get_used_volume() for w in self.sources], [15] * 7 + [0])
    self.assertEqual([tip.tracker.get_used_volume() for tip in self.pipette.tips], [0] * 8)
    self.sources[-1].tracker.set_volume(15)
    self.pipette.tips[-1].tracker.set_volume(20)
    with self.assertRaises(TooLittleVolumeError):
      await self.pipette.aspirate(self.sources, volume=10)
    self.assertEqual([w.tracker.get_used_volume() for w in self.sources], [15] * 8)
    self.assertEqual([tip.tracker.get_used_volume() for tip in self.pipette.tips], [0] * 7 + [20])
    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_liquid_command_failures_preserve_all_volumes(self) -> None:
    """A rejected aspiration or dispense rolls back the entire column."""
    await self.pipette.pick_up_tips(self.column)
    for operation, kind, tip_volume in (
      (self.pipette.aspirate, self.protocol_run.aspirate_in_place, 0),
      (self.pipette.dispense, self.protocol_run.dispense_in_place, 10),
    ):
      with self.subTest(kind=kind):
        for tip in self.pipette.tips:
          tip.tracker.set_volume(tip_volume)
        kind.side_effect = OpentronsError("liquid operation failed")
        with self.assertRaises(OpentronsError):
          await operation(self.sources, volume=10)
        self.assertEqual([w.tracker.get_used_volume() for w in self.sources], [15] * 8)
        self.assertEqual(
          [tip.tracker.get_used_volume() for tip in self.pipette.tips], [tip_volume] * 8
        )

  async def test_liquid_targets_must_match_the_rigid_head(self) -> None:
    """A partial column or row cannot silently command an eight-nozzle stroke."""
    await self.pipette.pick_up_tips(self.column)
    self.protocol_run.reset_mock()
    for targets in (self.sources[:7], self.plate["A1:A8"]):
      with self.subTest(targets=targets), self.assertRaises(ValueError):
        await self.pipette.aspirate(targets, volume=10)
    self.assertEqual(self.protocol_run.mock_calls, [])

  async def test_mix_commits_each_completed_stroke(self) -> None:
    """A later failed mix stroke retains the liquid moved by earlier completed strokes."""
    await self.pipette.pick_up_tips(self.column)
    await self.pipette.mix(self.sources, volume=5, repetitions=2)
    self.assertEqual([w.tracker.volume for w in self.sources], [15] * 8)
    self.protocol_run.dispense_in_place.side_effect = OpentronsError("dispense_in_place failed")
    with self.assertRaises(OpentronsError):
      await self.pipette.mix(self.sources, volume=5, repetitions=1)
    self.assertEqual([w.tracker.volume for w in self.sources], [10] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [5] * 8)

  async def test_retraction_failure_keeps_completed_tip_and_liquid_state(self) -> None:
    """A retraction failure must not undo the successful operation preceding it."""
    self.protocol_run.get_position.side_effect = OpentronsError("get_position failed")
    with self.assertRaises(OpentronsError):
      await self.pipette.pick_up_tips(self.column)
    self.assertEqual(len(self.pipette.tips), 8)
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    with self.assertRaises(OpentronsError):
      await self.pipette.aspirate(self.sources, volume=10)
    self.assertEqual([w.tracker.volume for w in self.sources], [5] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [10] * 8)
    with self.assertRaises(OpentronsError):
      await self.pipette.return_tips(allow_nonzero_volume=True)
    self.assertFalse(self.pipette.has_tip)
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_discard_checks_every_tip_and_retains_tips_on_failure(self) -> None:
    """Liquid in the last tip blocks disposal; a failed drop keeps tip ownership."""
    await self.pipette.pick_up_tips(self.column)
    self.pipette.tips[-1].tracker.set_volume(1)
    self.protocol_run.reset_mock()
    with self.assertRaisesRegex(ValueError, "contains liquid"):
      await self.pipette.discard_tips()
    self.assertEqual(self.protocol_run.mock_calls, [])
    self.protocol_run.discard_tip_in_fixed_trash.side_effect = OpentronsError(
      "discard_tip_in_fixed_trash failed"
    )
    with self.assertRaises(OpentronsError):
      await self.pipette.discard_tips(allow_nonzero_volume=True)
    self.assertEqual(len(self.pipette.tips), 8)
    self.protocol_run.discard_tip_in_fixed_trash.side_effect = None
    await self.pipette.discard_tips(allow_nonzero_volume=True)
    self.assertFalse(self.pipette.has_tip)

  async def test_disabled_tracking_still_owns_and_operates_all_tips(self) -> None:
    """Global tracking switches do not change the physical command grouping."""
    set_tip_tracking(False)
    set_volume_tracking(False)
    await self.pipette.pick_up_tips(self.column)
    await self.pipette.aspirate(self.sources, volume=10)
    await self.pipette.dispense(self.destinations, volume=10)
    self.assertEqual(len(self.pipette.tips), 8)
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    self.assertEqual([w.tracker.volume for w in self.sources], [15] * 8)
    self.assertEqual([w.tracker.volume for w in self.destinations], [0] * 8)
    await self.pipette.discard_tips()

  async def test_column_pickup_uses_one_stroke_and_preserves_tip_identity(self) -> None:
    original_tips = tuple(spot.get_tip() for spot in self.column)

    await self.pipette.pick_up_tips(self.column)

    self.assertEqual(self.pipette.tips, original_tips)
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    self.protocol_run.pick_up_tip.assert_awaited_once_with(
      "left-pipette-id", ANY, "A1", Coordinate(z=original_tips[0].get_size_z())
    )

  async def test_column_aspiration_transfers_volume_into_every_tip_with_one_stroke(self) -> None:
    await self.pipette.pick_up_tips(self.column)

    await self.pipette.aspirate(self.sources, volume=10)

    self.assertEqual([well.tracker.volume for well in self.sources], [5] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [10] * 8)
    self.protocol_run.aspirate_in_place.assert_awaited_once_with("left-pipette-id", 10, 7.6)

  async def test_column_dispense_uses_requested_flow_rate_for_every_tip(self) -> None:
    await self.pipette.pick_up_tips(self.column)
    for tip in self.pipette.tips:
      tip.tracker.set_volume(10)

    await self.pipette.dispense(self.destinations, volume=10, flow_rate=6)

    self.assertEqual([well.tracker.volume for well in self.destinations], [10] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [0] * 8)
    self.protocol_run.dispense_in_place.assert_awaited_once_with("left-pipette-id", 10, 6)

  async def test_column_return_restores_each_tip_to_its_original_spot(self) -> None:
    original_tips = tuple(spot.get_tip() for spot in self.column)
    await self.pipette.pick_up_tips(self.column)

    await self.pipette.return_tips()

    for spot, tip in zip(self.column, original_tips):
      self.assertIs(spot.get_tip(), tip)
    self.assertEqual(self.pipette.tips, ())
    self.assertFalse(self.pipette.has_tip)
    self.protocol_run.drop_tip.assert_awaited_once_with(
      "left-pipette-id", ANY, "A1", Coordinate(z=10)
    )


if __name__ == "__main__":
  unittest.main()

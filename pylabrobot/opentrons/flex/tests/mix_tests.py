"""In-place Flex mixing preserves positions and tracks each completed stroke."""

import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.opentrons import FlexHead8
from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import (
  Container,
  Coordinate,
  Resource,
  biorad_96_wellplate_200uL_Vb,
  set_volume_tracking,
)
from pylabrobot.resources.errors import TooLittleLiquidError
from pylabrobot.resources.opentrons import flex_96_tiprack_50ul


class FlexMixTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    """Set up a P50 and the workshop plate using mocked hardware."""
    self.api = make_api(pipettes=[("p50_multi_flex", 8, 1, 50, "right")])
    self.flex = make_flex(api=self.api)
    self.rack = flex_96_tiprack_50ul("tips")
    self.plate = biorad_96_wellplate_200uL_Vb("plate")
    self.flex.deck.assign_child_at_slot(self.rack, "B1")
    self.flex.deck.assign_child_at_slot(self.plate, "D2")
    facility = Resource("facility", 2400, 1000, 0)
    facility.assign_child_resource(self.flex.deck, location=Coordinate(-900, 20, 30))
    await self.flex.setup()
    assert isinstance(self.flex.right, FlexHead8)
    self.head = self.flex.right
    await self.head.pick_up_tips(self.rack.column(0))
    self.plate.set_well_volumes([100] * 96)
    set_volume_tracking(True)
    self.api.submit_command.reset_mock()

  async def asyncTearDown(self):
    """Reset global tracking and close the mocked connection."""
    set_volume_tracking(False)
    await self.flex.disconnect()

  async def test_positions_once_and_mixes_in_place(self):
    """Five cycles have one descent, one prime, and one final retraction."""
    await self.head.mix(
      self.plate.column(0),
      50,
      repetitions=5,
      liquid_height=4,
      aspirate_flow_rate=20,
      dispense_flow_rate=30,
    )
    commands = self.api.submit_command.await_args_list
    self.assertEqual(
      [c.args[1] for c in commands],
      ["moveToCoordinates", "prepareToAspirate", "moveToCoordinates"]
      + ["aspirateInPlace", "dispenseInPlace"] * 5
      + ["savePosition", "moveRelative"],
    )
    self.assertEqual(commands[0].args[2]["coordinates"], {"x": 178.38, "y": 74.24, "z": 109})
    self.assertEqual(commands[2].args[2]["coordinates"], {"x": 178.38, "y": 74.24, "z": 6.05})
    self.assertEqual(commands[2].args[2]["minimumZHeight"], 6.05)
    dispenses = [c.args[2] for c in commands if c.args[1] == "dispenseInPlace"]
    self.assertEqual([c["pushOut"] for c in dispenses[:-1]], [0] * 4)
    self.assertNotIn("pushOut", dispenses[-1])
    self.assertTrue(all(c["flowRate"] == 30 for c in dispenses))
    self.assertTrue(
      all(c.args[2]["flowRate"] == 20 for c in commands if c.args[1] == "aspirateInPlace")
    )
    self.assertEqual([w.tracker.volume for w in self.plate.get_all_items()], [100] * 96)
    self.assertTrue(all(t.tracker.volume == 0 for t in self.head.get_mounted_tips() if t))

  async def test_plate_column_and_final_push_out(self):
    """The column bridge and explicit final push-out address the selected column."""
    await self.head.mix(self.plate, 20, column=1, final_push_out=0)
    commands = self.api.submit_command.await_args_list
    self.assertEqual(commands[0].args[2]["coordinates"]["x"], 187.38)
    dispense = next(c for c in commands if c.args[1] == "dispenseInPlace")
    self.assertEqual(dispense.args[2]["pushOut"], 0)

  async def test_failed_dispense_preserves_successful_aspiration(self):
    """A failed stroke rolls back itself, not the preceding successful stroke."""
    with patch.object(
      self.head, "dispense_in_place", AsyncMock(side_effect=RuntimeError("failed"))
    ):
      with self.assertRaisesRegex(RuntimeError, "failed"):
        await self.head.mix(self.plate.column(0), 50, repetitions=5)
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [50] * 8)
    self.assertTrue(all(t.tracker.volume == 50 for t in self.head.get_mounted_tips() if t))
    self.assertEqual(self.api.submit_command.await_args_list[-1].args[1], "aspirateInPlace")

  async def test_insufficient_liquid_prevents_motion(self):
    """Reject an infeasible first stroke before positioning the robot."""
    self.plate.column(0)[0].tracker.set_volume(10)
    with self.assertRaises(TooLittleLiquidError):
      await self.head.mix(self.plate.column(0), 50)
    self.api.submit_command.assert_not_awaited()
    self.assertEqual(self.plate.column(0)[0].tracker.volume, 10)

  async def test_invalid_parameters_prevent_motion(self):
    """Invalid repetitions, capacity, rates, and push-out are pre-wire errors."""
    for kwargs in (
      {"repetitions": 0},
      {"repetitions": 1.5},
      {"volume": 51},
      {"volume": float("nan")},
      {"aspirate_flow_rate": 0},
      {"final_push_out": -1},
    ):
      with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
        await self.head.mix(self.plate.column(0), **({"volume": 50} | kwargs))
      self.api.submit_command.assert_not_awaited()

  async def test_nonempty_tips_prevent_priming(self):
    """Priming must not expel previously aspirated liquid above the plate."""
    for tip in self.head.get_mounted_tips():
      assert tip is not None
      tip.tracker.set_volume(10)
    with self.assertRaisesRegex(ValueError, "empty tips"):
      await self.head.mix(self.plate.column(0), 20)
    self.api.submit_command.assert_not_awaited()

  async def test_partial_mix_checks_adjacent_clearance(self):
    """Partial-column mixing rejects an obstacle before descending."""
    await self.head.drop_tips(self.rack, column=0)
    await self.head.pick_up_tips(self.rack.column(0)[:4], use_channels=[4, 5, 6, 7])
    self.flex.deck.assign_child_at_slot(Resource("obstacle", 127, 85, 99), "C2")
    self.api.submit_command.reset_mock()
    with self.assertRaisesRegex(ValueError, "Collision risk"):
      await self.head.mix(self.plate.column(0)[:4], 20)
    self.api.submit_command.assert_not_awaited()

  async def test_no_tips_prevents_motion(self):
    """A bare pipette cannot enter a well for mixing."""
    await self.head.drop_tips(self.rack, column=0)
    self.api.submit_command.reset_mock()
    with self.assertRaisesRegex(OpentronsError, "No tip mounted"):
      await self.head.mix(self.plate.column(0), 20)
    self.api.submit_command.assert_not_awaited()

  async def test_dispense_then_mix_without_lifting_or_priming(self):
    """Dispensing and mixing share one descent and retract after the final stroke."""
    await self.head.aspirate(self.plate.column(1), 20)
    self.api.submit_command.reset_mock()
    await self.head.dispense(
      self.plate.column(0), 20, post_mix=Mix(volume=40, repetitions=3, flow_rate=25)
    )
    commands = self.api.submit_command.await_args_list
    self.assertEqual(
      [c.args[1] for c in commands],
      ["moveToCoordinates", "moveToCoordinates", "dispenseInPlace"]
      + ["aspirateInPlace", "dispenseInPlace"] * 3
      + ["savePosition", "moveRelative"],
    )
    dispenses = [c.args[2] for c in commands if c.args[1] == "dispenseInPlace"]
    self.assertEqual([c["pushOut"] for c in dispenses[:-1]], [0] * 3)
    self.assertNotIn("pushOut", dispenses[-1])
    self.assertTrue(all(c["flowRate"] == 25 for c in dispenses[1:]))
    self.assertTrue(
      all(c.args[2]["flowRate"] == 25 for c in commands if c.args[1] == "aspirateInPlace")
    )
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [120] * 8)
    self.assertTrue(all(t.tracker.volume == 0 for t in self.head.get_mounted_tips() if t))

  async def test_invalid_post_mix_prevents_dispense(self):
    """Invalid mixing parameters are rejected before dispensing or moving."""
    await self.head.aspirate(self.plate.column(1), 20)
    self.api.submit_command.reset_mock()
    for post_mix in (
      Mix(volume=20, repetitions=0, flow_rate=25),
      Mix(volume=20, repetitions=True, flow_rate=25),
      Mix(volume=20, repetitions=1.5, flow_rate=25),  # type: ignore[arg-type]
      Mix(volume=0, repetitions=1, flow_rate=25),
      Mix(volume=float("nan"), repetitions=1, flow_rate=25),
      Mix(volume=51, repetitions=1, flow_rate=25),
    ):
      with self.subTest(post_mix=post_mix), self.assertRaises(ValueError):
        await self.head.dispense(self.plate.column(0), 20, post_mix=post_mix)
      self.api.submit_command.assert_not_awaited()
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [100] * 8)

  async def test_failed_post_mix_keeps_completed_dispense(self):
    """Failure during mixing retains the preceding dispense and stops motion."""
    await self.head.aspirate(self.plate.column(1), 20)
    self.api.submit_command.reset_mock()
    with (
      patch.object(self.head, "aspirate_in_place", AsyncMock(side_effect=RuntimeError("failed"))),
      self.assertRaisesRegex(RuntimeError, "failed"),
    ):
      await self.head.dispense(
        self.plate, 20, column=0, post_mix=Mix(volume=40, repetitions=3, flow_rate=25)
      )
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [120] * 8)
    self.assertTrue(all(t.tracker.volume == 0 for t in self.head.get_mounted_tips() if t))
    self.assertEqual(self.api.submit_command.await_args_list[-1].args[1], "dispenseInPlace")

  async def test_pre_mix_then_aspirate_without_lifting_or_repriming(self):
    """Pre-mixing and the final draw share one descent and one initial prime."""
    await self.head.aspirate(
      self.plate.column(0),
      20,
      flow_rate=15,
      pre_mix=Mix(volume=40, repetitions=3, flow_rate=25),
    )
    commands = self.api.submit_command.await_args_list
    self.assertEqual(
      [c.args[1] for c in commands],
      ["moveToCoordinates", "prepareToAspirate", "moveToCoordinates"]
      + ["aspirateInPlace", "dispenseInPlace"] * 3
      + ["aspirateInPlace", "savePosition", "moveRelative"],
    )
    strokes = [c.args[2] for c in commands if c.args[1] in ("aspirateInPlace", "dispenseInPlace")]
    self.assertTrue(all(c["volume"] == 40 and c["flowRate"] == 25 for c in strokes[:-1]))
    self.assertEqual(strokes[-1]["volume"], 20)
    self.assertEqual(strokes[-1]["flowRate"], 15)
    self.assertTrue(
      all(c.args[2]["pushOut"] == 0 for c in commands if c.args[1] == "dispenseInPlace")
    )
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [80] * 8)
    self.assertTrue(all(t.tracker.volume == 20 for t in self.head.get_mounted_tips() if t))

  async def test_pre_mix_preflights_both_draw_volumes(self):
    """Neither an infeasible final draw nor first mix stroke causes motion."""
    self.plate.set_well_volumes([10] * 96)
    for volume, mix_volume in ((20, 5), (5, 20)):
      with self.subTest(volume=volume), self.assertRaises(TooLittleLiquidError):
        await self.head.aspirate(
          self.plate,
          volume,
          column=0,
          pre_mix=Mix(volume=mix_volume, repetitions=2, flow_rate=25),
        )
      self.api.submit_command.assert_not_awaited()
      self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [10] * 8)
      self.assertTrue(all(t.tracker.volume == 0 for t in self.head.get_mounted_tips() if t))

  async def test_failed_pre_mix_dispense_keeps_completed_draw(self):
    """Failed mixing leaves the drawn liquid tracked and never starts the final draw."""
    with (
      patch.object(self.head, "dispense_in_place", AsyncMock(side_effect=RuntimeError("failed"))),
      self.assertRaisesRegex(RuntimeError, "failed"),
    ):
      await self.head.aspirate(
        self.plate.column(0), 20, pre_mix=Mix(volume=40, repetitions=3, flow_rate=25)
      )
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [60] * 8)
    self.assertTrue(all(t.tracker.volume == 40 for t in self.head.get_mounted_tips() if t))
    self.assertEqual(self.api.submit_command.await_args_list[-1].args[1], "aspirateInPlace")

  async def test_invalid_transfer_mix_parameters_prevent_motion(self):
    """Both transfer directions reject invalid mix rates and repetitions before motion."""
    for parameter in ("pre_mix", "post_mix"):
      for mix in (Mix(20, 0, 25), Mix(20, 1, 0), Mix(20, 1, float("nan"))):
        with self.subTest(parameter=parameter, mix=mix), self.assertRaises(ValueError):
          if parameter == "pre_mix":
            await self.head.aspirate(self.plate.column(0), 20, pre_mix=mix)
          else:
            await self.head.dispense(self.plate.column(0), 20, post_mix=mix)
        self.api.submit_command.assert_not_awaited()

  async def test_insufficient_post_mix_liquid_prevents_dispense(self):
    """Validate mixing against the volume after the planned dispense, before motion."""
    await self.head.aspirate(self.plate.column(1), 20)
    for well in self.plate.column(0):
      well.tracker.set_volume(0)
    self.api.submit_command.reset_mock()
    with self.assertRaises(TooLittleLiquidError):
      await self.head.dispense(
        self.plate.column(0), 20, post_mix=Mix(volume=30, repetitions=2, flow_rate=25)
      )
    self.api.submit_command.assert_not_awaited()
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [0] * 8)
    self.assertTrue(all(t.tracker.volume == 20 for t in self.head.get_mounted_tips() if t))

  async def test_transfer_mixing_with_partial_tips(self):
    """Only the four mounted channels exchange liquid in a partial-column transfer."""
    await self.head.drop_tips(self.rack, column=0)
    await self.head.pick_up_tips(self.rack.column(0)[:4], use_channels=[4, 5, 6, 7])
    mix = Mix(volume=30, repetitions=2, flow_rate=25)
    await self.head.aspirate(self.plate.column(0)[:4], 20, pre_mix=mix)
    await self.head.dispense(self.plate.column(1)[:4], 20, post_mix=mix)
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [80] * 4 + [100] * 4)
    self.assertEqual([w.tracker.volume for w in self.plate.column(1)], [120] * 4 + [100] * 4)
    self.assertEqual(self.head._nozzle_layout, "PARTIAL")
    self.assertTrue(all(t.tracker.volume == 0 for t in self.head.get_mounted_tips() if t))

  async def test_transfer_mixing_in_shared_container(self):
    """A reservoir accounts for every nozzle sharing the same volume tracker."""
    trough = Container("trough", 127.76, 85.48, 31.4, material_z_thickness=1, max_volume=10000)
    self.flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(1000)
    mix = Mix(volume=30, repetitions=2, flow_rate=25)
    await self.head.aspirate(trough, 20, pre_mix=mix)
    self.assertEqual(trough.tracker.volume, 840)
    await self.head.dispense(trough, 20, post_mix=mix)
    self.assertEqual(trough.tracker.volume, 1000)
    self.assertTrue(all(t.tracker.volume == 0 for t in self.head.get_mounted_tips() if t))

  async def test_post_mix_can_use_newly_dispensed_liquid(self):
    """An empty destination becomes mixable after the planned dispense."""
    await self.head.aspirate(self.plate.column(1), 30)
    for well in self.plate.column(0):
      well.tracker.set_volume(0)
    await self.head.dispense(
      self.plate.column(0), 30, post_mix=Mix(volume=30, repetitions=2, flow_rate=25)
    )
    self.assertEqual([w.tracker.volume for w in self.plate.column(0)], [30] * 8)
    self.assertTrue(all(t.tracker.volume == 0 for t in self.head.get_mounted_tips() if t))

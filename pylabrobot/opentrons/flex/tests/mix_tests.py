"""In-place Flex mixing preserves positions and tracks each completed stroke."""

import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.opentrons import FlexHead8
from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import (
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
      ["moveToCoordinates", "prepareToAspirate", "savePosition", "moveRelative"]
      + ["aspirateInPlace", "dispenseInPlace"] * 5
      + ["savePosition", "moveRelative"],
    )
    self.assertEqual(commands[0].args[2]["coordinates"], {"x": 178.38, "y": 74.24, "z": 109})
    self.assertAlmostEqual(commands[3].args[2]["distance"], 6.05 - 100)
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

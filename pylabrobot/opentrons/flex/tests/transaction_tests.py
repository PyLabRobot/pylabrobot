"""Regressions for atomic, two-sided Flex liquid tracking."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.opentrons import FlexHead8
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import cor_96_wellplate_360uL_Fb, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.errors import TooLittleLiquidError
from pylabrobot.resources.opentrons import (
  FlexDeck,
  flex_96_tiprack_50ul,
)


class FlexTransactionTests(unittest.IsolatedAsyncioTestCase):
  """A failed operation must leave all participating trackers unchanged."""

  async def asyncSetUp(self):
    """Mount a column with mocked API replies and initialize source water volumes."""
    self.api = make_api(pipettes=[("p1000_multi_flex", 8, 5, 1000, "left")])
    self.flex = make_flex(deck=FlexDeck(), host="offline", api=self.api)
    self.rack = flex_96_tiprack_50ul("tips")
    self.plate = cor_96_wellplate_360uL_Fb("plate")
    self.flex.deck.assign_child_at_slot(self.rack, "D1")
    self.flex.deck.assign_child_at_slot(self.plate, "B1")
    set_tip_tracking(True)
    set_volume_tracking(True)
    await self.flex.setup()
    head = self.flex.left
    assert isinstance(head, FlexHead8)
    self.head: FlexHead8 = head
    await self.head.pick_up_tips(self.rack.column(0))
    for well in self.plate.column(0):
      well.tracker.set_volume(100)

  async def asyncTearDown(self):
    """Release the mocked run and restore global tracking flags."""
    await self.flex.disconnect()
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_transfer_tracks_wells_and_tips(self):
    """Both sides of a successful transfer change by the same volume."""
    await self.head.aspirate(self.plate.column(0), volume=20, flow_rate=10)
    self.assertTrue(all(w.tracker.get_used_volume() == 80 for w in self.plate.column(0)))
    self.assertTrue(
      all(t is not None and t.tracker.get_used_volume() == 20 for t in self.head.get_mounted_tips())
    )
    await self.head.dispense(self.plate.column(0), volume=20, flow_rate=10)
    self.assertTrue(all(w.tracker.get_used_volume() == 100 for w in self.plate.column(0)))
    self.assertTrue(
      all(t is not None and t.tracker.get_used_volume() == 0 for t in self.head.get_mounted_tips())
    )

  async def test_later_staging_failure_rolls_back_earlier_wells(self):
    """An empty later well must not leave earlier wells partly staged."""
    self.plate.get_item("B1").tracker.set_volume(0)
    with self.assertRaises(TooLittleLiquidError):
      await self.head.aspirate(self.plate.column(0), volume=20, flow_rate=10)
    self.assertEqual(self.plate.get_item("A1").tracker.get_used_volume(), 100)
    self.assertTrue(
      all(t is not None and t.tracker.get_used_volume() == 0 for t in self.head.get_mounted_tips())
    )

  async def test_cancellation_rolls_back_all_trackers(self):
    """Cancellation while issuing a command restores pending bookkeeping."""
    with patch.object(self.flex, "_execute_command", AsyncMock(side_effect=asyncio.CancelledError)):
      with self.assertRaises(asyncio.CancelledError):
        await self.head.aspirate(self.plate.column(0), volume=20, flow_rate=10)
    self.assertTrue(all(w.tracker.get_used_volume() == 100 for w in self.plate.column(0)))
    self.assertTrue(
      all(t is not None and t.tracker.get_used_volume() == 0 for t in self.head.get_mounted_tips())
    )

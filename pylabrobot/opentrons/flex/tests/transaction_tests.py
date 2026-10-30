"""Regressions for atomic, two-sided Flex liquid tracking."""

from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.opentrons import FlexHead8
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.opentrons.tracking import validate_liquid_transfer
from pylabrobot.resources import cor_96_wellplate_360uL_Fb, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.errors import TooLittleLiquidError
from pylabrobot.resources.opentrons import (
  FlexDeck,
  flex_96_tiprack_50ul,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class TestFlexTransaction(AnyioTestBase):
  """A failed operation must leave all participating trackers unchanged."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Mount a column with mocked API replies and initialize source water volumes."""
    await super()._enter_lifespan(stack)
    self.api = make_api(pipettes=[("p1000_multi_flex", 8, 5, 1000, "left")])
    self.flex = make_flex(deck=FlexDeck(), host="offline", api=self.api)
    self.rack = flex_96_tiprack_50ul("tips")
    self.plate = cor_96_wellplate_360uL_Fb("plate")
    self.flex.deck.assign_child_at_slot(self.rack, "D1")
    self.flex.deck.assign_child_at_slot(self.plate, "B1")
    set_tip_tracking(True)
    set_volume_tracking(True)
    stack.callback(set_tip_tracking, False)
    stack.callback(set_volume_tracking, False)
    await stack.enter_async_context(self.flex.lifespan())
    head = self.flex.left
    assert isinstance(head, FlexHead8)
    self.head: FlexHead8 = head
    await self.head.pick_up_tips(self.rack.column(0))
    for well in self.plate.column(0):
      well.tracker.set_volume(100)

  async def test_validation_does_not_mutate_or_notify_live_trackers(self):
    """Successful and failed validation preserve pending volumes and emit no callbacks."""
    source = self.plate.column(0)[0].tracker
    destination = self.plate.column(1)[0].tracker
    source.remove_liquid(10)
    changed = Mock()
    source.register_callback(changed)
    destination.register_callback(changed)
    validate_liquid_transfer([source], [destination], 20)
    with pytest.raises(TooLittleLiquidError):
      validate_liquid_transfer([source], [destination], 95)
    assert source.volume == 100
    assert source.get_used_volume() == 90
    assert destination.get_used_volume() == 0
    changed.assert_not_called()

  async def test_transfer_tracks_wells_and_tips(self):
    """Both sides of a successful transfer change by the same volume."""
    await self.head.aspirate(self.plate.column(0), volume=20, flow_rate=10)
    assert all(w.tracker.get_used_volume() == 80 for w in self.plate.column(0))
    assert all(
      t is not None and t.tracker.get_used_volume() == 20 for t in self.head.get_mounted_tips()
    )
    await self.head.dispense(self.plate.column(0), volume=20, flow_rate=10)
    assert all(w.tracker.get_used_volume() == 100 for w in self.plate.column(0))
    assert all(
      t is not None and t.tracker.get_used_volume() == 0 for t in self.head.get_mounted_tips()
    )

  async def test_later_staging_failure_rolls_back_earlier_wells(self):
    """An empty later well must not leave earlier wells partly staged."""
    self.plate.get_item("B1").tracker.set_volume(0)
    with pytest.raises(TooLittleLiquidError):
      await self.head.aspirate(self.plate.column(0), volume=20, flow_rate=10)
    assert self.plate.get_item("A1").tracker.get_used_volume() == 100
    assert all(
      t is not None and t.tracker.get_used_volume() == 0 for t in self.head.get_mounted_tips()
    )

  async def test_cancellation_rolls_back_all_trackers(self):
    """Cancellation while issuing a command restores pending bookkeeping."""
    scope = anyio.CancelScope()

    async def cancel_command(*args, **kwargs):
      scope.cancel()
      await anyio.lowlevel.checkpoint()

    with patch.object(self.flex, "_execute_command", AsyncMock(side_effect=cancel_command)):
      with scope:
        await self.head.aspirate(self.plate.column(0), volume=20, flow_rate=10)
    assert scope.cancelled_caught
    assert all(w.tracker.get_used_volume() == 100 for w in self.plate.column(0))
    assert all(
      t is not None and t.tracker.get_used_volume() == 0 for t in self.head.get_mounted_tips()
    )

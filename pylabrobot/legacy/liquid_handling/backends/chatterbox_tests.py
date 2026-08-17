import unittest

from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.chatterbox import (
  LiquidHandlerChatterboxBackend,
)
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError
from pylabrobot.resources import (
  Coordinate,
  cor_96_wellplate_360uL_Fb,
  hamilton_96_tiprack_1000uL_filter,
)
from pylabrobot.resources.hamilton import STARLetDeck


class ChatterboxBackendTests(unittest.IsolatedAsyncioTestCase):
  """Tests for chatterbox backend"""

  def setUp(self) -> None:
    self.deck = STARLetDeck()
    self.backend = LiquidHandlerChatterboxBackend(num_channels=8)
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    self.tip_rack = hamilton_96_tiprack_1000uL_filter(name="tip_rack")
    self.deck.assign_child_resource(self.tip_rack, rails=3)
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.deck.assign_child_resource(self.plate, rails=9)

  async def asyncSetUp(self) -> None:
    await super().asyncSetUp()
    await self.lh.setup()

  async def asyncTearDown(self) -> None:
    await self.lh.stop()
    await super().asyncTearDown()

  async def test_pick_up_tips(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])

  async def test_drop_tips(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.drop_tips(self.tip_rack["A1"])

  async def test_pick_up_tips96(self):
    await self.lh.pick_up_tips96(self.tip_rack)

  async def test_drop_tips96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.drop_tips96(self.tip_rack)

  async def test_aspirate(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.aspirate(self.plate["A1"], vols=[10])

  async def test_dispense(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.dispense(self.plate["A1"], vols=[10])

  async def test_aspirate96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=10)

  async def test_dispense96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate96(self.plate, volume=10)
    await self.lh.dispense96(self.plate, volume=10)

  async def test_move(self):
    await self.lh.move_resource(self.plate, Coordinate(0, 0, 0))

  async def test_failed_pickup_does_not_commit_pending_tips(self):
    async def fail_pickup(*args, **kwargs):
      raise RuntimeError("simulated pickup failure")

    self.backend.pick_up_tips = fail_pickup  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.lh.pick_up_tips(self.tip_rack["A1"])
    self.assertFalse(self.lh.head[0].has_tip)

  async def test_failed_drop_does_not_commit_pending_remove(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    self.assertTrue(self.lh.head[0].has_tip)

    async def fail_drop(*args, **kwargs):
      raise RuntimeError("simulated drop failure")

    self.backend.drop_tips = fail_drop  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.lh.drop_tips(self.tip_rack["A1"])
    self.assertTrue(self.lh.head[0].has_tip)

  async def test_failed_multi_channel_pickup_rolls_back_all_channels(self):
    async def fail_pickup(*args, **kwargs):
      raise RuntimeError("simulated pickup failure")

    self.backend.pick_up_tips = fail_pickup  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self.assertFalse(self.lh.head[0].has_tip)
    self.assertFalse(self.lh.head[1].has_tip)

  async def test_failed_pickup_presence_query_overrides_channelized_error(self):
    async def fail_pickup(*args, **kwargs):
      raise ChannelizedError(errors={0: Exception("channel 0 failed")})

    self.backend.pick_up_tips = fail_pickup  # type: ignore[method-assign]
    with self.assertRaises(ChannelizedError):
      await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self.assertFalse(self.lh.head[0].has_tip)
    self.assertFalse(self.lh.head[1].has_tip)

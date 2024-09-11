import unittest

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb, HTF_L, Coordinate
from pylabrobot.resources.hamilton import STARLetDeck


class ChatterboxBackendTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for chatterbox backend """
  def setUp(self) -> None:
    self.deck = STARLetDeck()
    self.backend = LiquidHandlerChatterboxBackend(num_channels=8)
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    self.tip_rack = HTF_L(name="tip_rack")
    self.deck.assign_child_resource(self.tip_rack, rails=3)
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
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

import unittest

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.chatterbox_backend import ChatterBoxBackend
from pylabrobot.resources import Cos_96_EZWash, HTF_L, Coordinate
from pylabrobot.resources.hamilton import STARLetDeck


class ChatterBoxBackendTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for setup and stop """
  def setUp(self) -> None:
    self.deck = STARLetDeck()
    self.backend = ChatterBoxBackend(num_channels=8)
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    self.tip_rack = HTF_L(name="tip_rack")
    self.deck.assign_child_resource(self.tip_rack, rails=1)
    self.plate = Cos_96_EZWash(name="plate")
    self.deck.assign_child_resource(self.plate, rails=8)

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
    await self.lh.aspirate(self.plate["A1"], vols=10)

  async def test_dispense(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.dispense(self.plate["A1"], vols=10)

  async def test_aspirate_plate(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate_plate(self.plate, volume=10)

  async def test_dispense_plate(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate_plate(self.plate, volume=10)
    await self.lh.dispense_plate(self.plate, volume=10)

  async def test_move(self):
    await self.lh.move_resource(self.plate, Coordinate(0, 0, 0))

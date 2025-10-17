import unittest
import unittest.mock

from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerBackend
from pylabrobot.plate_reading.byonoy import (
  byonoy_absorbance96_base_and_reader,
  byonoy_absorbance_adapter,
)
from pylabrobot.resources import PLT_CAR_L5_DWP, CellVis_96_wellplate_350uL_Fb, Coordinate, STARDeck


class ByonoyResourceTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.base, self.reader = byonoy_absorbance96_base_and_reader(name="byonoy_test", assign=True)
    self.adapter = byonoy_absorbance_adapter(name="byonoy_test_adapter")

    self.deck = STARDeck()
    self.lh = LiquidHandler(deck=self.deck, backend=unittest.mock.Mock(spec=LiquidHandlerBackend))
    self.plate_carrier = PLT_CAR_L5_DWP(name="plate_carrier")
    self.plate_carrier[1] = self.adapter
    self.deck.assign_child_resource(self.plate_carrier, rails=28)
    self.adapter.assign_child_resource(self.base)
    self.plate_carrier[2] = self.plate = CellVis_96_wellplate_350uL_Fb(name="plate")

  async def test_move_reader_to_base(self):
    # move reader to deck
    await self.lh.move_resource(self.reader, to=Coordinate(x=400, y=209.995, z=100))

    # move reader to base
    print("moving")
    await self.lh.move_resource(
      self.reader,
      self.base.reader_holder,
      pickup_distance_from_top=7.45,
    )
    assert self.reader.get_absolute_location() == Coordinate(x=706.48, y=162.145, z=204.38)

  async def test_move_plate_to_base(self):
    self.reader.unassign()
    await self.lh.move_resource(
      self.plate,
      self.base.plate_holder,
    )
    assert self.plate.get_absolute_location() == Coordinate(
      x=711.6,
      y=167.2,
      z=221.42,
    )

  async def test_move_plate_to_base_when_reader_present(self):
    with self.assertRaises(RuntimeError):
      await self.lh.move_resource(
        self.plate,
        self.base.plate_holder,
      )

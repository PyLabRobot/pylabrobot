import unittest
import unittest.mock

from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerBackend
from pylabrobot.plate_reading.byonoy import (
  byonoy_a96a,
  byonoy_sbs_adapter,
)
from pylabrobot.resources import PLT_CAR_L5_DWP, CellVis_96_wellplate_350uL_Fb, Coordinate, STARDeck


class ByonoyResourceTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.reader, self.illumination_unit = byonoy_a96a(name="byonoy_test", assign=True)
    self.adapter = byonoy_sbs_adapter(name="byonoy_test_adapter")

    self.deck = STARDeck()
    self.lh = LiquidHandler(deck=self.deck, backend=unittest.mock.Mock(spec=LiquidHandlerBackend))
    self.plate_carrier = PLT_CAR_L5_DWP(name="plate_carrier")
    self.plate_carrier[1] = self.adapter
    self.deck.assign_child_resource(self.plate_carrier, rails=28)
    self.adapter.assign_child_resource(self.reader)
    self.plate_carrier[2] = self.plate = CellVis_96_wellplate_350uL_Fb(name="plate")

  async def test_move_illumination_unit_to_reader(self):
    # move illumination unit to deck
    await self.lh.move_resource(self.illumination_unit, to=Coordinate(x=400, y=209.995, z=100))

    # move illumination unit to reader
    await self.lh.move_resource(
      self.illumination_unit,
      self.reader.illumination_unit_holder,
      pickup_distance_from_top=7.45,
    )
    assert self.illumination_unit.get_absolute_location() == Coordinate(x=697.85, y=162.2, z=213.2)

  async def test_move_plate_to_reader(self):
    self.illumination_unit.unassign()
    await self.lh.move_resource(
      self.plate,
      self.reader.plate_holder,
    )
    assert self.plate.get_absolute_location() == Coordinate(x=720.35, y=167.2, z=215.1)

  async def test_move_plate_to_reader_when_illumination_unit_present(self):
    with self.assertRaises(RuntimeError):
      await self.lh.move_resource(
        self.plate,
        self.reader.plate_holder,
      )

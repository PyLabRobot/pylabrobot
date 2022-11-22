from typing import cast
import unittest

from pylabrobot.liquid_handling.resources import (
  Coordinate,
  TipRack,
  TipCarrier,
  Plate,
  PlateCarrier,
)
from pylabrobot.liquid_handling.resources.hamilton import HamiltonDeck


class HamiltonDeckTests(unittest.TestCase):
  """ Tests for the HamiltonDeck class. """
  def test_parse_lay_file(self):
    fn = "./pylabrobot/testing/test_data/test_deck.lay"
    deck = HamiltonDeck.load_from_lay_file(fn)

    tip_car = deck.get_resource("TIP_CAR_480_A00_0001")
    assert isinstance(tip_car, TipCarrier)

    self.assertEqual(tip_car.get_absolute_location(), \
                     Coordinate(122.500, 63.000, 100.000))
    self.assertEqual(
      cast(TipRack, deck.get_resource("tips_01")).get_item("A1").get_absolute_location(), \
      Coordinate(140.400, 145.800, 164.450))
    self.assertEqual(
      cast(TipRack, deck.get_resource("STF_L_0001")).get_item("A1").get_absolute_location(), \
      Coordinate(140.400, 241.800, 164.450))
    self.assertEqual(
      cast(TipRack, deck.get_resource("tips_04")).get_item("A1").get_absolute_location(), \
      Coordinate(140.400, 433.800, 131.450))

    assert tip_car[0].resource is not None
    self.assertEqual(tip_car[0].resource.name, "tips_01")
    assert tip_car[1].resource is not None
    self.assertEqual(tip_car[1].resource.name, "STF_L_0001")
    self.assertIsNone(tip_car[2].resource)
    assert tip_car[3].resource is not None
    self.assertEqual(tip_car[3].resource.name, "tips_04")
    self.assertIsNone(tip_car[4].resource)

    self.assertEqual(
      cast(TipRack, deck.get_resource("PLT_CAR_L5AC_A00_0001")).get_absolute_location(), \
      Coordinate(302.500, 63.000, 100.000))
    self.assertEqual(
      cast(TipRack, deck.get_resource("Cos_96_DW_1mL_0001")).get_item("A1") \
        .get_absolute_location(), Coordinate(320.500, 146.000, 187.150))
    self.assertEqual(
      cast(TipRack, deck.get_resource("Cos_96_DW_500ul_0001")).get_item("A1") \
        .get_absolute_location(), Coordinate(320.500, 338.000, 188.150))
    self.assertEqual(
      cast(TipRack, deck.get_resource("Cos_96_DW_1mL_0002")).get_item("A1") \
        .get_absolute_location(), Coordinate(320.500, 434.000, 187.150))
    self.assertEqual(
      cast(TipRack, deck.get_resource("Cos_96_DW_2mL_0001")).get_item("A1") \
        .get_absolute_location(), Coordinate(320.500, 530.000, 187.150))

    plt_car1 = deck.get_resource("PLT_CAR_L5AC_A00_0001")
    assert isinstance(plt_car1, PlateCarrier)
    assert plt_car1[0].resource is not None
    self.assertEqual(plt_car1[0].resource.name, "Cos_96_DW_1mL_0001")
    self.assertIsNone(plt_car1[1].resource)
    assert plt_car1[2].resource is not None
    self.assertEqual(plt_car1[2].resource.name, "Cos_96_DW_500ul_0001")
    assert plt_car1[3].resource is not None
    self.assertEqual(plt_car1[3].resource.name, "Cos_96_DW_1mL_0002")
    assert plt_car1[4].resource is not None
    self.assertEqual(plt_car1[4].resource.name, "Cos_96_DW_2mL_0001")

    self.assertEqual(
      cast(Plate, deck.get_resource("PLT_CAR_L5AC_A00_0002")).get_absolute_location(), \
      Coordinate(482.500, 63.000, 100.000))
    self.assertEqual(
      cast(Plate, deck.get_resource("Cos_96_DW_1mL_0003")).get_item("A1") \
      .get_absolute_location(), Coordinate(500.500, 146.000, 187.150))
    self.assertEqual(
      cast(Plate, deck.get_resource("Cos_96_DW_500ul_0003")).get_item("A1") \
      .get_absolute_location(), Coordinate(500.500, 242.000, 188.150))
    self.assertEqual(
      cast(Plate, deck.get_resource("Cos_96_PCR_0001")).get_item("A1") \
      .get_absolute_location(), Coordinate(500.500, 434.000, 186.650))

    plt_car2 = deck.get_resource("PLT_CAR_L5AC_A00_0002")
    assert isinstance(plt_car2, PlateCarrier)
    assert plt_car2[0].resource is not None
    self.assertEqual(plt_car2[0].resource.name, "Cos_96_DW_1mL_0003")
    assert plt_car2[1].resource is not None
    self.assertEqual(plt_car2[1].resource.name, "Cos_96_DW_500ul_0003")
    self.assertIsNone(plt_car2[2].resource)
    assert plt_car2[3].resource is not None
    self.assertEqual(plt_car2[3].resource.name, "Cos_96_PCR_0001")
    self.assertIsNone(plt_car2[4].resource)

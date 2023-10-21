import textwrap
from typing import cast
import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.carrier import TipCarrier, PlateCarrier
from pylabrobot.resources.corning_costar import Cos_96_DW_1mL, Cos_96_DW_500ul
from pylabrobot.resources.hamilton import HamiltonSTARDeck, STARLetDeck
from pylabrobot.resources.ml_star import STF_L, HTF_L, TIP_CAR_480_A00, PLT_CAR_L5AC_A00
from pylabrobot.resources.resource import Resource


class HamiltonDeckTests(unittest.TestCase):
  """ Tests for the HamiltonDeck class. """

  def test_parse_lay_file(self):
    fn = "./pylabrobot/testing/test_data/test_deck.lay"
    deck = HamiltonSTARDeck.load_from_lay_file(fn)

    tip_car = deck.get_resource("TIP_CAR_480_A00_0001")
    assert isinstance(tip_car, TipCarrier)

    def get_item_center(name: str) -> Coordinate:
      tip_rack = cast(ItemizedResource, deck.get_resource(name))
      tip = cast(Resource, tip_rack.get_item("A1"))
      return tip.get_absolute_location() + tip.center()

    self.assertEqual(tip_car.get_absolute_location(), Coordinate(122.500, 63.000, 100.000))
    self.assertEqual(get_item_center("tips_01"),  Coordinate(140.400, 145.800, 164.450))
    self.assertEqual(get_item_center("STF_L_0001"), Coordinate(140.400, 241.800, 164.450))
    self.assertEqual(get_item_center("tips_04"), Coordinate(140.400, 433.800, 131.450))

    assert tip_car[0].resource is not None
    self.assertEqual(tip_car[0].resource.name, "tips_01")
    assert tip_car[1].resource is not None
    self.assertEqual(tip_car[1].resource.name, "STF_L_0001")
    self.assertIsNone(tip_car[2].resource)
    assert tip_car[3].resource is not None
    self.assertEqual(tip_car[3].resource.name, "tips_04")
    self.assertIsNone(tip_car[4].resource)

    self.assertEqual(deck.get_resource("PLT_CAR_L5AC_A00_0001").get_absolute_location(),
      Coordinate(302.500, 63.000, 100.000))
    self.assertEqual(get_item_center("Cos_96_DW_1mL_0001"), Coordinate(320.500, 146.000, 187.150))
    self.assertEqual(get_item_center("Cos_96_DW_500ul_0001"), Coordinate(320.500, 338.000, 188.150))
    self.assertEqual(get_item_center("Cos_96_DW_1mL_0002"), Coordinate(320.500, 434.000, 187.150))
    self.assertEqual(get_item_center("Cos_96_DW_2mL_0001"), Coordinate(320.500, 530.000, 187.150))

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

    self.assertEqual(deck.get_resource("PLT_CAR_L5AC_A00_0002").get_absolute_location(),
      Coordinate(482.500, 63.000, 100.000))
    self.assertEqual(get_item_center("Cos_96_DW_1mL_0003"), Coordinate(500.500, 146.000, 187.150))
    self.assertEqual(get_item_center("Cos_96_DW_500ul_0003"), Coordinate(500.500, 242.000, 188.150))
    self.assertEqual(get_item_center("Cos_96_PCR_0001"), Coordinate(500.500, 434.000, 186.650))

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

  def build_layout(self):
    """ Build a deck layout for testing """
    deck = STARLetDeck()

    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tip_rack_01")
    tip_car[1] = STF_L(name="tip_rack_02")
    tip_car[3] = HTF_L(name="tip_rack_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    plt_car[2] = Cos_96_DW_500ul(name="dispense plate")

    deck.assign_child_resource(tip_car, rails=1)
    deck.assign_child_resource(plt_car, rails=21)

    return deck

  def test_summary(self):
    self.maxDiff = None
    deck = self.build_layout()
    self.assertEqual(deck.summary(), textwrap.dedent("""
    Rail     Resource                   Type                Coordinates (mm)
    ===============================================================================================
    (1)  ├── tip_carrier                TipCarrier          (100.000, 063.000, 100.000)
         │   ├── tip_rack_01            TipRack             (117.900, 145.800, 164.450)
         │   ├── tip_rack_02            TipRack             (117.900, 241.800, 164.450)
         │   ├── <empty>
         │   ├── tip_rack_04            TipRack             (117.900, 433.800, 131.450)
         │   ├── <empty>
         │
    (21) ├── plate carrier              PlateCarrier        (550.000, 063.000, 100.000)
         │   ├── aspiration plate       Plate               (568.000, 146.000, 187.150)
         │   ├── <empty>
         │   ├── dispense plate         Plate               (568.000, 338.000, 188.150)
         │   ├── <empty>
         │   ├── <empty>
         │
    (32) ├── trash                      Trash               (800.000, 190.600, 137.100)
    """[1:]))

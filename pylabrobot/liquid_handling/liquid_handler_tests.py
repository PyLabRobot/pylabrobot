""" Tests for LiquidHandler """
# pylint: disable=missing-class-docstring

import io
import tempfile
import textwrap
import os
import unittest
import unittest.mock

from . import backends
from .liquid_handler import LiquidHandler
from .resources import (
  Coordinate,
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_DW_1mL,
  Cos_96_DW_500ul,
  Tips,
  TipCarrier,
  Plate,
  PlateCarrier,
  standard_volume_tip_with_filter
)
from .resources.ml_star import STF_L, HTF_L


class TestLiquidHandlerLayout(unittest.TestCase):
  def setUp(self):
    star = backends.Mock()
    self.lh = LiquidHandler(star)

  def test_resource_assignment(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tips_01")
    tip_car[1] = STF_L(name="tips_02")
    tip_car[3] = HTF_L("tips_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    plt_car[2] = Cos_96_DW_500ul(name="dispense plate")

    self.lh.assign_resource(tip_car, rails=1)
    self.lh.assign_resource(plt_car, rails=21)

    # Test placing a carrier at a location where another carrier is located.
    with self.assertRaises(ValueError):
      dbl_plt_car_1 = PLT_CAR_L5AC_A00(name="double placed carrier 1")
      self.lh.assign_resource(dbl_plt_car_1, rails=1)

    with self.assertRaises(ValueError):
      dbl_plt_car_2 = PLT_CAR_L5AC_A00(name="double placed carrier 2")
      self.lh.assign_resource(dbl_plt_car_2, rails=2)

    with self.assertRaises(ValueError):
      dbl_plt_car_3 = PLT_CAR_L5AC_A00(name="double placed carrier 3")
      self.lh.assign_resource(dbl_plt_car_3, rails=20)

    # Test carrier with same name.
    with self.assertRaises(ValueError):
      same_name_carrier = PLT_CAR_L5AC_A00(name="plate carrier")
      self.lh.assign_resource(same_name_carrier, rails=10)
    # Should not raise when replacing.
    self.lh.assign_resource(same_name_carrier, rails=10, replace=True)
    # Should not raise when unassinged.
    self.lh.unassign_resource("plate carrier")
    self.lh.assign_resource(same_name_carrier, rails=10, replace=True)

    # Test unassigning unassigned resource
    self.lh.unassign_resource("plate carrier")
    with self.assertRaises(KeyError):
      self.lh.unassign_resource("plate carrier")
    with self.assertRaises(KeyError):
      self.lh.unassign_resource("this resource is completely new.")

    # Test invalid rails.
    with self.assertRaises(ValueError):
      self.lh.assign_resource(plt_car, rails=-1)
    with self.assertRaises(ValueError):
      self.lh.assign_resource(plt_car, rails=42)
    with self.assertRaises(ValueError):
      self.lh.assign_resource(plt_car, rails=27)

  def test_get_resource(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tips_01")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    self.lh.assign_resource(tip_car, rails=1)
    self.lh.assign_resource(plt_car, rails=10)

    # Get resource.
    self.assertEqual(self.lh.get_resource("tip_carrier").name, "tip_carrier")
    self.assertEqual(self.lh.get_resource("plate carrier").name, "plate carrier")

    # Get subresource.
    self.assertEqual(self.lh.get_resource("tips_01").name, "tips_01")
    self.assertEqual(self.lh.get_resource("aspiration plate").name, "aspiration plate")

    # Get unknown resource.
    self.assertIsNone(self.lh.get_resource("unknown resource"))

  def test_subcoordinates(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tips_01")
    tip_car[3] = HTF_L(name="tips_04")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    plt_car[2] = Cos_96_DW_500ul(name="dispense plate")
    self.lh.assign_resource(tip_car, rails=1)
    self.lh.assign_resource(plt_car, rails=10)

    # Rails 10 should be left of rails 1.
    self.assertGreater(self.lh.get_resource("plate carrier").get_absolute_location().x,
                       self.lh.get_resource("tip_carrier").get_absolute_location().x)

    # Verified with Hamilton Method Editor.
    # Carriers.
    self.assertEqual(self.lh.get_resource("tip_carrier").get_absolute_location(),
                     Coordinate(100.0, 63.0, 100.0))
    self.assertEqual(self.lh.get_resource("plate carrier").get_absolute_location(),
                     Coordinate(302.5, 63.0, 100.0))

    # Subresources.
    self.assertEqual(self.lh.get_resource("tips_01").get_item("A1").get_absolute_location(),
                     Coordinate(117.900, 145.800, 164.450))
    self.assertEqual(self.lh.get_resource("tips_04").get_item("A1").get_absolute_location(),
                     Coordinate(117.900, 433.800, 131.450))

    self.assertEqual(self.lh.get_resource("dispense plate").get_item("A1").get_absolute_location(),
                     Coordinate(320.500, 338.000, 188.150))
    self.assertEqual(
      self.lh.get_resource("aspiration plate").get_item("A1") .get_absolute_location(),
      Coordinate(320.500, 146.000, 187.150))

  def test_illegal_subresource_assignment_before(self):
    # Test assigning subresource with the same name as another resource in another carrier. This
    # should raise an ValueError when the carrier is assigned to the liquid handler.
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="sub")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="sub")
    self.lh.assign_resource(tip_car, rails=1)
    with self.assertRaises(ValueError):
      self.lh.assign_resource(plt_car, rails=10)

  def test_illegal_subresource_assignment_after(self):
    # Test assigning subresource with the same name as another resource in another carrier, after
    # the carrier has been assigned. This should raise an error.
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="sub")
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="ok")
    self.lh.assign_resource(tip_car, rails=1)
    self.lh.assign_resource(plt_car, rails=10)
    with self.assertRaises(ValueError):
      plt_car[1] = Cos_96_DW_500ul(name="sub")

  def build_layout(self):
    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = STF_L(name="tips_01")
    tip_car[1] = STF_L(name="tips_02")
    tip_car[3] = HTF_L(name="tips_04")

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="aspiration plate")
    plt_car[2] = Cos_96_DW_500ul(name="dispense plate")

    self.lh.assign_resource(tip_car, rails=1, replace=True)
    self.lh.assign_resource(plt_car, rails=21, replace=True)

  @unittest.mock.patch("sys.stdout", new_callable=io.StringIO)
  def test_summary(self, out):
    with self.assertRaises(ValueError):
      self.lh.summary()

    self.build_layout()
    self.maxDiff = None # pylint: disable=invalid-name
    expected_out = textwrap.dedent("""
    Rail     Resource                   Type                Coordinates (mm)
    ===============================================================================================
    (1)  ├── tip_carrier                TipCarrier          (100.000, 063.000, 100.000)
         │   ├── tips_01                Tips                (117.900, 145.800, 164.450)
         │   ├── tips_02                Tips                (117.900, 241.800, 164.450)
         │   ├── <empty>
         │   ├── tips_04                Tips                (117.900, 433.800, 131.450)
         │   ├── <empty>
         │
    (21) ├── plate carrier              PlateCarrier        (550.000, 063.000, 100.000)
         │   ├── aspiration plate       Plate               (568.000, 146.000, 187.150)
         │   ├── <empty>
         │   ├── dispense plate         Plate               (568.000, 338.000, 188.150)
         │   ├── <empty>
         │   ├── <empty>
    """[1:])
    self.lh.summary()
    self.assertEqual(out.getvalue(), expected_out)

  def test_parse_lay_file(self):
    fn = "./pylabrobot/testing/test_data/test_deck.lay"
    self.lh.load_from_lay_file(fn)

    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001").get_absolute_location(), \
                     Coordinate(122.500, 63.000, 100.000))
    self.assertEqual(self.lh.get_resource("tips_01").get_item("A1").get_absolute_location(), \
                     Coordinate(140.400, 145.800, 164.450))
    self.assertEqual(self.lh.get_resource("STF_L_0001").get_item("A1").get_absolute_location(), \
                     Coordinate(140.400, 241.800, 164.450))
    self.assertEqual(self.lh.get_resource("tips_04").get_item("A1").get_absolute_location(), \
                     Coordinate(140.400, 433.800, 131.450))

    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001")[0].resource.name, "tips_01")
    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001")[1].resource.name, "STF_L_0001")
    self.assertIsNone(self.lh.get_resource("TIP_CAR_480_A00_0001")[2].resource)
    self.assertEqual(self.lh.get_resource("TIP_CAR_480_A00_0001")[3].resource.name, "tips_04")
    self.assertIsNone(self.lh.get_resource("TIP_CAR_480_A00_0001")[4].resource)

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001").get_absolute_location(), \
                     Coordinate(302.500, 63.000, 100.000))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_1mL_0001").get_item("A1") \
                    .get_absolute_location(), Coordinate(320.500, 146.000, 187.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_500ul_0001").get_item("A1") \
                    .get_absolute_location(), Coordinate(320.500, 338.000, 188.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_1mL_0002").get_item("A1") \
                    .get_absolute_location(), Coordinate(320.500, 434.000, 187.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_2mL_0001").get_item("A1") \
                    .get_absolute_location(), Coordinate(320.500, 530.000, 187.150))

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[0].resource.name,
      "Cos_96_DW_1mL_0001")
    self.assertIsNone(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[1].resource)
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[2].resource.name,
      "Cos_96_DW_500ul_0001")
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[3].resource.name,
      "Cos_96_DW_1mL_0002")
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0001")[4].resource.name,
      "Cos_96_DW_2mL_0001")

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002").get_absolute_location(), \
                     Coordinate(482.500, 63.000, 100.000))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_1mL_0003").get_item("A1") \
                     .get_absolute_location(), Coordinate(500.500, 146.000, 187.150))
    self.assertEqual(self.lh.get_resource("Cos_96_DW_500ul_0003").get_item("A1") \
                     .get_absolute_location(), Coordinate(500.500, 242.000, 188.150))
    self.assertEqual(self.lh.get_resource("Cos_96_PCR_0001").get_item("A1") \
                     .get_absolute_location(), Coordinate(500.500, 434.000, 186.650))

    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[0].resource.name,
      "Cos_96_DW_1mL_0003")
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[1].resource.name,
      "Cos_96_DW_500ul_0003")
    self.assertIsNone(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[2].resource)
    self.assertEqual(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[3].resource.name,
      "Cos_96_PCR_0001")
    self.assertIsNone(self.lh.get_resource("PLT_CAR_L5AC_A00_0002")[4].resource)

  def assert_same(self, lh1, lh2):
    """ Assert two liquid handler decks are the same. """
    self.assertEqual(lh1.deck.get_resources(), lh2.deck.get_resources())

  def test_json_serialization(self):
    self.maxDiff = None

    # test with standard resource classes
    self.build_layout()
    tmp_dir = tempfile.gettempdir()
    fn = os.path.join(tmp_dir, "layout.json")
    self.lh.save(fn)

    be = backends.Mock()
    recovered = LiquidHandler(be)
    recovered.load_from_json(fn)

    self.assert_same(self.lh, recovered)

    # test with custom classes
    custom_1 = LiquidHandler(be)
    tc = TipCarrier("tc", 200, 200, 200, location=Coordinate(0, 0, 0), sites=[
      Coordinate(10, 20, 30)
    ], site_size_x=10, site_size_y=10)

    tc[0] = Tips("tips", 10, 20, 30, standard_volume_tip_with_filter, -1, -1, -1, 1, 1, 1, 1)
    pc = PlateCarrier("pc", 100, 100, 100, location=Coordinate(0, 0, 0), sites=[
      Coordinate(10, 20, 30)
    ], site_size_x=10, site_size_y=10)
    pc[0] = Plate("plate", 10, 20, 30, -1, -1, -1, 0, 0, 0, 0, 0)

    fn = os.path.join(tmp_dir, "layout.json")
    custom_1.save(fn)
    custom_recover = LiquidHandler(be)
    custom_recover.load(fn)

    self.assertEqual(custom_1.deck,
                     custom_recover.deck)

    # unsupported format
    with self.assertRaises(ValueError):
      custom_recover.load(fn + ".unsupported")

  def test_move_plate_to_site(self):
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="plate")
    self.lh.assign_resource(plt_car, rails=21)

    self.lh.move_plate(plt_car[0], plt_car[2])
    self.assertIsNotNone(plt_car[2].resource)
    self.assertIsNone(plt_car[0].resource)
    self.assertEqual(plt_car[2].resource, self.lh.get_resource("plate"))
    self.assertEqual(plt_car[2].resource.get_item("A1").get_absolute_location(),
                     Coordinate(568.000, 338.000, 187.150))

  def test_move_plate_free(self):
    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="plate")
    self.lh.assign_resource(plt_car, rails=1)

    self.lh.move_plate(plt_car[0], Coordinate(100, 100, 100))
    self.assertIsNotNone(self.lh.get_resource("plate"))
    self.assertIsNone(plt_car[0].resource)
    # TODO: will probably update this test some time, when we make the deck universal and not just
    # star.
    self.assertEqual(self.lh.get_resource("plate").get_absolute_location(),
      Coordinate(100, 163, 200))


class TestLiquidHandlerCommands(unittest.TestCase):
  def setUp(self):
    self.lh = LiquidHandler(backends.Mock())

  def test_return_tips(self):
    # TODO: figure out a way to test "composite" commands
    pass

  def test_return_tips96(self):
    # TODO: figure out a way to test "composite" commands
    pass


if __name__ == "__main__":
  unittest.main()

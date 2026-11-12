"""Biomek's labware by class name, Biomek's touch-off distance, and where labware sits on a deck
position."""

import unittest

from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import (
  biomek_labware,
  biomek_tip,
  biomek_touch_distance,
)
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_deck
from pylabrobot.resources import (
  Plate,
  Tip,
  beckman_coulter_biomek_96_tiprack_230uL,
  beckman_coulter_biomek_96_wellplate_flat,
  beckman_coulter_biomek_modular_reservoir,
  beckman_coulter_biomek_tip_1070uL,
  cor_96_wellplate_360uL_Fb,
)


class LabwareByClassTests(unittest.TestCase):
  def test_a_class_is_named_in_any_case(self) -> None:
    box = biomek_labware("bc230", "box")
    self.assertEqual(box.model, beckman_coulter_biomek_96_tiprack_230uL.__name__)
    self.assertFalse(biomek_labware("BC230", "empty", with_tips=False).get_item("A1").has_tip())

  def test_a_class_with_no_definition_says_so(self) -> None:
    with self.assertRaises(NotImplementedError):
      biomek_labware("SmallTubeRack_Microfuge", "rack")
    with self.assertRaises(NotImplementedError):
      biomek_tip("T190F_LLS", "t")

  def test_a_plate_takes_the_lid_its_definition_has(self) -> None:
    for lid in ("TiterplateLid", "titerplatelid", "default"):
      plate = biomek_labware("BCFlat96", "plate", lid=lid)
      assert isinstance(plate, Plate)
      self.assertIsNotNone(plate.lid)
    with self.assertRaises(ValueError):
      biomek_labware("BCFlat96", "plate", lid="Greiner384Lid")
    with self.assertRaises(ValueError):
      biomek_labware("BCDeep96Square", "plate", lid="default")


class TouchOffTests(unittest.TestCase):
  def test_a_biomek_tip_in_a_well_with_a_volume_curve(self) -> None:
    tip = beckman_coulter_biomek_tip_1070uL("t")
    well = beckman_coulter_biomek_96_wellplate_flat("p").get_well("A1")
    distance = biomek_touch_distance(well, tip, 7.0)
    assert distance is not None
    self.assertGreater(distance, 0.0)
    # Any plate with height↔volume data works the same way.
    native = cor_96_wellplate_360uL_Fb("n").get_well("A1")
    also = biomek_touch_distance(native, tip, 7.0)
    assert also is not None
    self.assertGreater(also, 0.0)
    plain = Tip(
      "t", diameter=4.8, size_z=100, has_filter=False, maximal_volume=1000, fitting_depth=7
    )
    self.assertIsNone(biomek_touch_distance(well, plain, 7.0))
    # Modular sections have no height↔volume table.
    bare = beckman_coulter_biomek_modular_reservoir("m").get_well("A1")
    self.assertIsNone(biomek_touch_distance(bare, tip, 7.0))


class PositionTests(unittest.TestCase):
  def test_a_tip_load_position_takes_labware_lower(self) -> None:
    from pylabrobot.beckman_coulter.biomek_i7.layouts import place_labware

    deck = generic_deck()
    place_labware(
      deck,
      [
        {"position": "TL1", "class": "BC230", "name": "on_tl"},
        {"position": "TL2", "class": "BCFlat96", "name": "flat_on_tl"},
        {"position": "P1", "class": "BC230", "name": "on_static"},
      ],
    )
    # TipLoad1x1.alp: its adaptor plate takes labware 0.254 cm lower; none on a P position.
    for name, below in (("on_tl", 2.54), ("flat_on_tl", 2.54), ("on_static", 0.0)):
      labware = deck.get_resource(name)
      position, location = labware.parent, labware.location
      assert position is not None and location is not None
      with self.subTest(name=name):
        self.assertAlmostEqual(location.z, -below)
        self.assertAlmostEqual(location.x, (position.get_size_x() - labware.get_size_x()) / 2)


if __name__ == "__main__":
  unittest.main()

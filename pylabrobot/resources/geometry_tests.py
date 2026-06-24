import json
import unittest

from pylabrobot.resources import (
  Cor_96_wellplate_360ul_Fb,
  generate_geometry_library,
  hamilton_96_tiprack_1000uL_filter,
)
from pylabrobot.resources.hamilton import STARLetDeck


class GeometryLibraryTests(unittest.TestCase):
  def setUp(self):
    self.deck = STARLetDeck()
    self.tip_rack = hamilton_96_tiprack_1000uL_filter(name="tip_rack")
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(self.tip_rack, rails=3)
    self.deck.assign_child_resource(self.plate, rails=9)

  def test_generate_geometry_library_for_deck(self):
    library = generate_geometry_library(self.deck)
    json.dumps(library)

    self.assertEqual(library["root"], "deck")
    self.assertIn("plate", library["instances"])
    self.assertIn("plate_well_A1", library["instances"])

    well_instance = library["instances"]["plate_well_A1"]
    well_prototype = library["prototypes"][well_instance["prototype"]]
    self.assertEqual(well_prototype["geometry"]["shape"], "well")
    self.assertEqual(well_prototype["geometry"]["cross_section"], "circle")
    self.assertEqual(len(well_instance["pose"]), 3)

  def test_generate_geometry_library_for_single_labware(self):
    plate = Cor_96_wellplate_360ul_Fb(name="standalone_plate")
    library = generate_geometry_library(plate)

    self.assertEqual(library["root"], "standalone_plate")
    self.assertIn("standalone_plate", library["instances"])
    self.assertIn("standalone_plate_well_A1", library["instances"])
    self.assertEqual(library["instances"]["standalone_plate"]["pose"], [0, 0, 0])
    self.assertLess(len(library["prototypes"]), len(library["instances"]))

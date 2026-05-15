import json
import unittest

from pylabrobot.resources import (
  Cor_96_wellplate_360ul_Fb,
  generate_geometry_catalog,
  hamilton_96_tiprack_1000uL_filter,
)
from pylabrobot.resources.hamilton import STARLetDeck


class GeometryCatalogTests(unittest.TestCase):
  def setUp(self):
    self.deck = STARLetDeck()
    self.tip_rack = hamilton_96_tiprack_1000uL_filter(name="tip_rack")
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    self.deck.assign_child_resource(self.tip_rack, rails=3)
    self.deck.assign_child_resource(self.plate, rails=9)

  def test_generate_geometry_catalog_for_deck(self):
    catalog = generate_geometry_catalog(self.deck)
    json.dumps(catalog)

    self.assertEqual(catalog["root"], "deck")
    self.assertIn("plate", catalog["instances"])
    self.assertIn("plate_well_A1", catalog["instances"])

    well_instance = catalog["instances"]["plate_well_A1"]
    well_prototype = catalog["prototypes"][well_instance["prototype"]]
    self.assertEqual(well_prototype["geometry"]["shape"], "well")
    self.assertEqual(well_prototype["geometry"]["cross_section"], "circle")
    self.assertEqual(len(well_instance["pose"]), 3)

  def test_generate_geometry_catalog_for_single_labware(self):
    plate = Cor_96_wellplate_360ul_Fb(name="standalone_plate")
    catalog = generate_geometry_catalog(plate)

    self.assertEqual(catalog["root"], "standalone_plate")
    self.assertIn("standalone_plate", catalog["instances"])
    self.assertIn("standalone_plate_well_A1", catalog["instances"])
    self.assertEqual(catalog["instances"]["standalone_plate"]["pose"], [0, 0, 0])
    self.assertLess(len(catalog["prototypes"]), len(catalog["instances"]))

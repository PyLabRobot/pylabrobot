"""Tests for the Sarstedt tube's public factory and cavity geometry."""

import json
import unittest

from pylabrobot.resources import Coordinate, Deck, Tube, sarstedt_tube_2mL_Vb
from pylabrobot.resources.sarstedt import sarstedt_tube_2mL_Vb as manufacturer_factory


class TestSarstedtTube(unittest.TestCase):
  """Verify placement and serialization of the skirted tube."""

  def test_public_imports_and_cavity_placement(self):
    """The cavity floor is above the skirt's base and 44 mm below the top rim."""
    self.assertIs(sarstedt_tube_2mL_Vb, manufacturer_factory)
    tube = sarstedt_tube_2mL_Vb("sample")
    deck = Deck(size_x=100, size_y=100, size_z=100)
    deck.assign_child_resource(tube, location=Coordinate(10, 20, 30))

    cavity_bottom = tube.get_absolute_location(x="c", y="c", z="cavity_bottom")
    top = tube.get_absolute_location(x="c", y="c", z="top")
    self.assertEqual(cavity_bottom, Coordinate(15.4, 25.4, 31.0))
    self.assertEqual(top, Coordinate(15.4, 25.4, 75.0))
    self.assertEqual(top.z - cavity_bottom.z, 44.0)

  def test_json_round_trip(self):
    """Saving a layout preserves the tube geometry without inventing a volume model."""
    tube = sarstedt_tube_2mL_Vb("sample")
    restored = Tube.deserialize(json.loads(json.dumps(tube.serialize())))

    self.assertIsInstance(restored, Tube)
    self.assertEqual(restored.name, "sample")
    self.assertEqual(restored.model, "sarstedt_tube_2mL_Vb")
    self.assertEqual(restored.get_size_x(), 10.8)
    self.assertEqual(restored.get_size_y(), 10.8)
    self.assertEqual(restored.get_size_z(), 45.0)
    self.assertEqual(restored.max_volume, 2_000)
    self.assertEqual(restored.material_z_thickness, 1.0)
    self.assertEqual(restored.get_anchor(z="cavity_bottom").z, 1.0)
    self.assertFalse(restored.supports_compute_height_volume_functions())

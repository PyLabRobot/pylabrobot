"""Tests for the Sarstedt tube's geometry and measured height-volume calibration."""

import unittest

from pylabrobot.resources import Coordinate, Deck, sarstedt_tube_2mL_Vb
from pylabrobot.resources.sarstedt import sarstedt_tube_2mL_Vb as manufacturer_factory

_MEASUREMENTS = (
  (50.0, 3.55),
  (100.0, 4.12),
  (200.0, 7.20),
  (300.0, 9.61),
  (400.0, 11.29),
  (500.0, 13.35),
  (600.0, 15.10),
  (700.0, 16.81),
  (800.0, 18.63),
  (900.0, 20.62),
  (1000.0, 22.37),
  (1500.0, 31.55),
  (2000.0, 40.36),
)


class TestSarstedtTube(unittest.TestCase):
  """Verify placement and calibration of the skirted tube."""

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

  def test_measured_volume_height_conversion(self):
    """Every measurement converts between volume and height above the cavity floor."""
    tube = sarstedt_tube_2mL_Vb("sample")
    self.assertTrue(tube.supports_compute_height_volume_functions())
    for volume, height_from_base in _MEASUREMENTS:
      with self.subTest(volume=volume):
        height = tube.compute_height_from_volume(volume)
        self.assertAlmostEqual(height, height_from_base - 1.0)
        self.assertAlmostEqual(height + tube.material_z_thickness, height_from_base)
        self.assertAlmostEqual(tube.compute_volume_from_height(height_from_base - 1.0), volume)
        self.assertAlmostEqual(tube.compute_volume_from_height(height), volume)

  def test_interpolated_volume_height_conversion(self):
    """Intermediate volumes use the surrounding calibration points in both directions."""
    tube = sarstedt_tube_2mL_Vb("sample")
    for volume, height in ((750.0, 16.72), (1250.0, 25.96)):
      with self.subTest(volume=volume):
        self.assertAlmostEqual(tube.compute_height_from_volume(volume), height)
        self.assertAlmostEqual(tube.compute_volume_from_height(height), volume)

  def test_conversion_rejects_values_outside_measured_range(self):
    """No empty-tube anchor or extrapolation extends the measured calibration range."""
    tube = sarstedt_tube_2mL_Vb("sample")
    for volume in (-1.0, 0.0, 49.99, 2000.01):
      with self.subTest(volume=volume), self.assertRaises(ValueError):
        tube.compute_height_from_volume(volume)
    for height in (-1.0, 0.0, 2.54, 39.37, 44.0):
      with self.subTest(height=height), self.assertRaises(ValueError):
        tube.compute_volume_from_height(height)

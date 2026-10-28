import math
import unittest

from pylabrobot.resources.eppendorf.plates import Eppendorf_96_wellplate_250ul_Vb

# Liquid heights (mm) measured for dispensed volumes (uL).
MEASURED_VOLUMES = [0, 4, 8, 20, 70, 120, 170, 220, 260]
MEASURED_HEIGHTS = [0, 0.45, 1.45, 2.55, 6.45, 9.05, 11.6, 13.15, 14.349]


class Eppendorf96Wellplate250ulVbTests(unittest.TestCase):
  """Height/volume conversions of the Eppendorf twin.tec 96 well plate, 250 uL."""

  def setUp(self):
    self.well = Eppendorf_96_wellplate_250ul_Vb("plate").get_well("A1")

  def test_empty_well_is_zero_volume_at_zero_height(self):
    self.assertEqual(self.well.compute_volume_from_height(0), 0)
    self.assertEqual(self.well.compute_height_from_volume(0), 0)

  def test_volume_height_volume_round_trip(self):
    for i in range(1001):
      volume = self.well.max_volume * i / 1000
      height = self.well.compute_height_from_volume(volume)
      self.assertAlmostEqual(self.well.compute_volume_from_height(height), volume, delta=1e-9)

  def test_height_volume_height_round_trip(self):
    depth = self.well.get_size_z()
    for i in range(1001):
      height = depth * i / 1000
      volume = self.well.compute_volume_from_height(height)
      self.assertAlmostEqual(self.well.compute_height_from_volume(volume), height, delta=1e-9)

  def test_conversions_pass_through_measured_points(self):
    for volume, height in zip(MEASURED_VOLUMES, MEASURED_HEIGHTS):
      self.assertAlmostEqual(self.well.compute_height_from_volume(volume), height, delta=1e-9)
      self.assertAlmostEqual(self.well.compute_volume_from_height(height), volume, delta=1e-9)

  def test_max_volume_is_volume_at_rim(self):
    rim_cylinder = math.pi * (5.48 / 2) ** 2 * (19.5 - 14.349)
    self.assertAlmostEqual(self.well.max_volume, 260 + rim_cylinder)
    self.assertAlmostEqual(self.well.max_volume, 381.49, places=2)
    self.assertAlmostEqual(self.well.compute_height_from_volume(self.well.max_volume), 19.5)

  def test_conversions_refuse_outside_the_well(self):
    with self.assertRaises(ValueError):
      self.well.compute_volume_from_height(self.well.get_size_z() + 0.01)
    with self.assertRaises(ValueError):
      self.well.compute_height_from_volume(self.well.max_volume + 0.01)

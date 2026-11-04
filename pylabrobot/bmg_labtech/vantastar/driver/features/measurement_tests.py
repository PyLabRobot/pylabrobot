"""Tests for what the measurement features share: the plate geometry the reader takes from a
PyLabRobot plate, and the order of measured wells."""

import unittest
from typing import Any, Dict, List

from pylabrobot.bmg_labtech.vantastar.driver.features.measurement import (
  MeasurementSettings,
  ShakeSettings,
  _get_measured_wells,
  encode_reading_mode,
  get_plate_geometry,
)
from pylabrobot.resources import cor_96_wellplate_2mL_Vb, cor_96_wellplate_360uL_Fb


class PlateTests(unittest.TestCase):
  """The plate geometry the reader takes from a PyLabRobot plate, and the order of measured
  wells."""

  def test_geometry_from_the_back_left_corner(self):
    """Well centres are measured from the plate's left and back edges: A1 lies in the back half, the
    last well 99 mm right of it and 63 mm in front of it."""
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    geometry = get_plate_geometry(plate)
    self.assertEqual((geometry.columns, geometry.rows), (12, 8))
    self.assertAlmostEqual(geometry.last_x - geometry.first_x, 99.0, places=1)
    self.assertAlmostEqual(geometry.last_y - geometry.first_y, 63.0, places=1)
    self.assertLess(geometry.first_y, geometry.width / 2)

  def test_square_wells(self):
    """Round wells are sent as shape 0, square wells as shape 1."""
    self.assertEqual(get_plate_geometry(cor_96_wellplate_360uL_Fb(name="plate")).well_shape, 0)
    self.assertEqual(get_plate_geometry(cor_96_wellplate_2mL_Vb(name="plate")).well_shape, 1)

  def test_measured_wells_are_in_row_order(self):
    """Measured wells come in the reader's data order, row by row, as 0-based (row, column)."""
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    wells = plate.get_items(["B2", "A3", "A1"])
    self.assertEqual(_get_measured_wells(plate, wells), [(0, 0), (0, 2), (1, 1)])


class SettingsTests(unittest.TestCase):
  """The measurement and shake settings, with the reader's ranges."""

  def test_defaults(self):
    """By default a read goes row by row from the top left corner, back and forth, at the centre of
    each well, with the read's own flashes and settling time."""
    settings = MeasurementSettings()
    self.assertEqual(
      (settings.corner, settings.reading_direction, settings.bidirectional, settings.well_scan),
      ("top_left", "row_major", True, "point"),
    )
    self.assertEqual((settings.flashes, settings.settling_time), (None, None))

  def test_out_of_range_fields_are_refused(self):
    """Settling times outside 0.02-1.0 s, more than 500 flashes, a matrix scan, an unknown corner, a
    negative pause and a 0 mm scan are refused."""
    refused: List[Dict[str, Any]] = [
      {"settling_time": 0},
      {"settling_time": 1.04},
      {"flashes": 501},
      {"well_scan": "matrix"},
      {"matrix_size": 3},
      {"corner": "middle"},
      {"pause_before_reading": -1},
      {"scan_diameter": 0},
    ]
    for fields in refused:
      with self.subTest(fields=fields), self.assertRaises(ValueError):
        MeasurementSettings(**fields)

  def test_scan_diameter_fits_the_well(self):
    """An orbital scan of 6 mm fits the 6.96 mm wells of a 96-well plate, 7 mm does not (the reader
    refuses it, quit code 65)."""
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    MeasurementSettings(well_scan="orbital", scan_diameter=6).check(plate)
    with self.assertRaises(ValueError):
      MeasurementSettings(well_scan="orbital", scan_diameter=7).check(plate)

  def test_shake_settings(self):
    """Shake settings take the patterns orbital, linear and double_orbital, and speeds in steps of
    100 rpm."""
    ShakeSettings(pattern="linear", speed=300, duration=5)
    refused: List[Dict[str, Any]] = [
      {"pattern": "round", "speed": 300, "duration": 5},
      {"pattern": "orbital", "speed": 250, "duration": 5},
    ]
    for fields in refused:
      with self.subTest(fields=fields), self.assertRaises(ValueError):
        ShakeSettings(**fields)


class ReadingOrderTests(unittest.TestCase):
  """The RUN reading-mode bits of a reading order, as Voyager sets them for its reading
  directions."""

  def test_reading_mode_bits(self):
    """Top left, row by row, back and forth sets no bit; column by column sets 0x08, a right corner
    0x20, a bottom corner 0x40, one direction 0x80."""
    self.assertEqual(encode_reading_mode("top_left", "row_major", True), 0x00)
    self.assertEqual(encode_reading_mode("top_left", "column_major", True), 0x08)
    self.assertEqual(encode_reading_mode("top_right", "row_major", True), 0x20)
    self.assertEqual(encode_reading_mode("bottom_left", "row_major", True), 0x40)
    self.assertEqual(encode_reading_mode("bottom_right", "row_major", False), 0xE0)


if __name__ == "__main__":
  unittest.main()

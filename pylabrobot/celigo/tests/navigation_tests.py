"""Tests for plate/well navigation and the FOV galvo grid."""

import unittest

from pylabrobot.celigo.config import AxisConfig, CalibrationConfig, HardwareDefaultConfig
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.navigation import (
  CORNING_3603_96,
  NavigationConfig,
  PlateGeometry,
  effective_fov_mm,
  fovs_per_for,
  galvo_fov_offsets_mm,
  well_to_encoder_ticks,
  well_to_stage_mm,
)


def _coords():
  calib = CalibrationConfig(
    microns_per_pixel_x=1.05456,
    microns_per_pixel_y=1.05444,
    image_width_pixels=2048,
    image_height_pixels=2048,
    stage_x_scale=1.0,
    stage_y_scale=1.0,
  )
  hw = HardwareDefaultConfig(
    default_plate_x_corner_stage_coordinate=2.159,
    default_plate_y_corner_stage_coordinate=3.492,
  )
  return calib, hw, CoordinateSystems.from_config(calib, hw)


class TestPlateGeometry(unittest.TestCase):
  def test_well_names_and_parse(self):
    self.assertEqual(PlateGeometry.well_name(0, 0), "A1")
    self.assertEqual(PlateGeometry.well_name(7, 11), "H12")
    self.assertEqual(PlateGeometry.parse_well("H12"), (7, 11))

  def test_well_center_pitch(self):
    a1 = CORNING_3603_96.well_center_sample_mm(0, 0)
    a2 = CORNING_3603_96.well_center_sample_mm(0, 1)
    b1 = CORNING_3603_96.well_center_sample_mm(1, 0)
    self.assertAlmostEqual(a2[0] - a1[0], 9.0)  # column pitch
    self.assertAlmostEqual(b1[1] - a1[1], 9.0)  # row pitch

  def test_out_of_range(self):
    with self.assertRaises(ValueError):
      CORNING_3603_96.well_center_sample_mm(8, 0)


class TestWellToStage(unittest.TestCase):
  def test_a1_stage_position(self):
    _, _, cs = _coords()
    x, y = well_to_stage_mm(CORNING_3603_96, "A1", cs)
    # this config has no calibrated corner offset, so stage = sample + default corner:
    # (14.38 + 2.159, 11.24 + 3.492)
    self.assertAlmostEqual(x, 16.539, places=2)
    self.assertAlmostEqual(y, 14.732, places=2)

  def test_adjacent_wells_differ_by_pitch(self):
    _, _, cs = _coords()
    a1 = well_to_stage_mm(CORNING_3603_96, "A1", cs)
    a2 = well_to_stage_mm(CORNING_3603_96, "A2", cs)
    b1 = well_to_stage_mm(CORNING_3603_96, "B1", cs)
    self.assertAlmostEqual(a2[0] - a1[0], 9.0, places=2)
    self.assertAlmostEqual(b1[1] - a1[1], 9.0, places=2)

  def test_encoder_ticks(self):
    _, _, cs = _coords()
    xax = AxisConfig(motion_name="X", mm_per_encoder_tick=0.0127, home_offset=-18.0)
    yax = AxisConfig(
      motion_name="Y", mm_per_encoder_tick=0.0127, home_offset=71.75, invert_axis_direction=True
    )
    xt, yt = well_to_encoder_ticks(CORNING_3603_96, "A1", cs, xax, yax)
    self.assertIsInstance(xt, int)
    self.assertIsInstance(yt, int)
    # consistent with the mm->ticks formula
    sx, sy = well_to_stage_mm(CORNING_3603_96, "A1", cs)
    self.assertEqual(xt, round((sx - 18.0) / 0.0127))


class TestFovGrid(unittest.TestCase):
  def setUp(self):
    self.calib, self.hw, self.cs = _coords()
    self.nav = NavigationConfig(
      frame_overlap_x_mm=0.1,
      frame_overlap_y_mm=0.1,
      max_galvo_deflection_x_mm=4.5,
      max_galvo_deflection_y_mm=4.5,
    )

  def test_effective_fov(self):
    ex, ey = effective_fov_mm(self.calib, self.nav)
    # 2048 * 1.05456 / 1000 = ~2.16 mm, minus 0.2 overlap
    self.assertAlmostEqual(ex, 2048 * 1.05456 / 1000.0 - 0.2, places=4)

  def test_fovs_per_for(self):
    nx, ny = fovs_per_for(self.calib, self.nav)
    self.assertEqual((nx, ny), (4, 4))  # floor(2*4.5/1.96) == 4

  def test_offsets_count_and_centered(self):
    offsets = galvo_fov_offsets_mm(self.calib, self.nav)
    self.assertEqual(len(offsets), 16)  # 4x4
    # symmetric about origin -> mean ~ 0
    mx = sum(o[0] for o in offsets) / len(offsets)
    my = sum(o[1] for o in offsets) / len(offsets)
    self.assertAlmostEqual(mx, 0.0, places=9)
    self.assertAlmostEqual(my, 0.0, places=9)

  def test_serpentine_rows_reverse(self):
    offsets = galvo_fov_offsets_mm(self.calib, self.nav)
    # first row left->right (increasing x), second row right->left (decreasing x)
    row0 = offsets[0:4]
    row1 = offsets[4:8]
    self.assertTrue(row0[0][0] < row0[-1][0])
    self.assertTrue(row1[0][0] > row1[-1][0])


if __name__ == "__main__":
  unittest.main()

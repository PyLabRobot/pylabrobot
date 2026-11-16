"""Tests for the Celigo affine coordinate systems and calibration loaders."""

import math
import os
import tempfile
import unittest

from pylabrobot.revvity.celigo.config import (
  CalibrationConfig,
  HardwareDefaultConfig,
)
from pylabrobot.revvity.celigo.coordinates import (
  CoordinateSystems,
  sample_offset_mm_to_galvo_offset_mm,
)
from pylabrobot.revvity.celigo.tests.helpers import (
  make_calibration_config,
  make_hardware_default_config,
)

CALIB_XML = """<?xml version="1.0"?>
<configuration>
  <section name="CalibrationSection">
    <setting key="CalibrationConfig">
      <CalibrationParameters xmlns="ns">
        <MicronsPerPixelX>1.05456</MicronsPerPixelX>
        <MicronsPerPixelY>1.05444</MicronsPerPixelY>
        <ImageWidthPixels>2048</ImageWidthPixels>
        <ImageHeightPixels>2048</ImageHeightPixels>
        <ImageToStageThetaRadians>0</ImageToStageThetaRadians>
        <GalvoToStageThetaRadians>0</GalvoToStageThetaRadians>
        <CalibratedPlateCornerX>-0.0798</CalibratedPlateCornerX>
        <CalibratedPlateCornerY>-0.0361</CalibratedPlateCornerY>
        <CalibratedPlateToStageThetaRadians>2.566e-06</CalibratedPlateToStageThetaRadians>
        <StageXScale>0.99972</StageXScale>
        <StageYScale>1.00001</StageYScale>
        <StageShear>1.561e-4</StageShear>
        <StageXShearOffset>2.05e-3</StageXShearOffset>
        <StageYShearOffset>-1.10e-4</StageYShearOffset>
        <CalibratedZPosition>2.5654</CalibratedZPosition>
        <CalibratedZGlassPlateDelta>0</CalibratedZGlassPlateDelta>
        <ZPlaneXCoeff>0</ZPlaneXCoeff>
        <ZPlaneYCoeff>0</ZPlaneYCoeff>
      </CalibrationParameters>
    </setting>
  </section>
</configuration>
"""

HW_XML = """<?xml version="1.0"?>
<configuration>
  <section name="HardwareDefaultSection">
    <setting key="HardwareDefaultConfig">
      <HardwareDefaultParameters xmlns="ns">
        <DefaultCalibratedZ>2.5654</DefaultCalibratedZ>
        <DefaultPlateXCornerStageCoordinate>2.159</DefaultPlateXCornerStageCoordinate>
        <DefaultPlateYCornerStageCoordinate>3.492</DefaultPlateYCornerStageCoordinate>
        <DefaultXFieldOfViewMM>2.15</DefaultXFieldOfViewMM>
        <DefaultYFieldOfViewMM>2.15</DefaultYFieldOfViewMM>
        <DefaultXGalvoMMPerVolt>1.3</DefaultXGalvoMMPerVolt>
        <DefaultYGalvoMMPerVolt>1.3</DefaultYGalvoMMPerVolt>
      </HardwareDefaultParameters>
    </setting>
  </section>
</configuration>
"""


def _write(xml: str) -> str:
  fd, path = tempfile.mkstemp(suffix=".xml")
  with os.fdopen(fd, "w") as f:
    f.write(xml)
  return path


class TestCalibrationLoaders(unittest.TestCase):
  def test_calibration_fields(self):
    c = CalibrationConfig.from_xml(_write(CALIB_XML))
    self.assertAlmostEqual(c.microns_per_pixel_x, 1.05456)
    self.assertEqual(c.image_width_pixels, 2048)
    self.assertAlmostEqual(c.stage_x_scale, 0.99972)
    self.assertAlmostEqual(c.stage_shear, 1.561e-4)
    self.assertAlmostEqual(c.calibrated_z_position, 2.5654)

  def test_hardware_defaults(self):
    h = HardwareDefaultConfig.from_xml(_write(HW_XML))
    self.assertAlmostEqual(h.default_plate_x_corner_stage_coordinate, 2.159)
    self.assertAlmostEqual(h.default_x_galvo_mm_per_volt, 1.3)


class TestAffineIdentityCase(unittest.TestCase):
  """With unit scaling and zero rotation/shear, the math is easy to reason about."""

  def setUp(self):
    self.calib = make_calibration_config(
      microns_per_pixel_x=1.0,
      microns_per_pixel_y=1.0,  # -> 1000 px/mm
      image_width_pixels=2048,
      image_height_pixels=2048,  # center (1024,1024)
      image_to_stage_theta_radians=0.0,
      calibrated_plate_corner_x=0.0,
      calibrated_plate_corner_y=0.0,
      calibrated_plate_to_stage_theta_radians=0.0,
      stage_x_scale=1.0,
      stage_y_scale=1.0,
      stage_shear=0.0,
      stage_x_shear_offset=0.0,
      stage_y_shear_offset=0.0,
    )
    self.hw = make_hardware_default_config(
      default_plate_x_corner_stage_coordinate=0.0,
      default_plate_y_corner_stage_coordinate=0.0,
    )
    self.cs = CoordinateSystems.from_config(self.calib, self.hw)

  def test_center_pixel_maps_to_origin(self):
    x, y = self.cs.image_pixel_to_sample_mm(1024, 1024)
    self.assertAlmostEqual(x, 0.0)
    self.assertAlmostEqual(y, 0.0)

  def test_pixel_offset_is_mm(self):
    # +1000 px in x == +1 mm at 1000 px/mm
    x, y = self.cs.image_pixel_to_sample_mm(2024, 1024)
    self.assertAlmostEqual(x, 1.0)
    self.assertAlmostEqual(y, 0.0)

  def test_sample_origin_maps_to_plate_corner(self):
    cs = CoordinateSystems.from_config(
      self.calib,
      make_hardware_default_config(
        default_plate_x_corner_stage_coordinate=2.159,
        default_plate_y_corner_stage_coordinate=3.492,
      ),
    )
    x, y = cs.sample_mm_to_stage_mm(0.0, 0.0)
    self.assertAlmostEqual(x, 2.159)
    self.assertAlmostEqual(y, 3.492)


class TestGalvoCoordinateConvention(unittest.TestCase):
  def test_sample_x_is_reversed_once_at_the_galvo_boundary(self):
    self.assertEqual(sample_offset_mm_to_galvo_offset_mm(2.5, -1.25), (-2.5, -1.25))

  def test_non_finite_offsets_are_rejected(self):
    with self.assertRaisesRegex(ValueError, "finite"):
      sample_offset_mm_to_galvo_offset_mm(float("nan"), 0)


class TestAffineRoundTrips(unittest.TestCase):
  """Real-ish calibration values; forward/inverse must round-trip."""

  def setUp(self):
    self.calib = CalibrationConfig.from_xml(_write(CALIB_XML))
    self.hw = HardwareDefaultConfig.from_xml(_write(HW_XML))
    self.cs = CoordinateSystems.from_config(self.calib, self.hw)

  def test_sample_stage_roundtrip(self):
    for x, y in ((0.0, 0.0), (12.7, 8.5), (50.0, 30.0)):
      sx, sy = self.cs.sample_mm_to_stage_mm(x, y)
      rx, ry = self.cs.stage_mm_to_sample_mm(sx, sy)
      self.assertAlmostEqual(rx, x, places=4)
      self.assertAlmostEqual(ry, y, places=4)

  def test_pixel_sample_roundtrip(self):
    for px, py in ((1024, 1024), (1500, 700), (300, 1900)):
      mx, my = self.cs.image_pixel_to_sample_mm(px, py)
      rpx, rpy = self.cs.sample_mm_to_image_pixel(mx, my)
      self.assertAlmostEqual(rpx, px, places=2)
      self.assertAlmostEqual(rpy, py, places=2)

  def test_pixel_to_stage_close_to_chained(self):
    # GetLowestBaseCoord (pixel->stage) should be close to manually chaining
    # pixel->sample then sample->stage (they differ only by the base frame's
    # sub-1.0 scale/shear, which is ~identity here).
    px, py = 1400, 900
    direct = self.cs.image_pixel_to_stage_mm(px, py)
    msx, msy = self.cs.image_pixel_to_sample_mm(px, py)
    chained = self.cs.sample_mm_to_stage_mm(msx, msy)
    self.assertAlmostEqual(direct[0], chained[0], places=1)
    self.assertAlmostEqual(direct[1], chained[1], places=1)


class TestRotation(unittest.TestCase):
  def test_90_degree_rotation(self):
    calib = make_calibration_config(
      microns_per_pixel_x=1.0,
      microns_per_pixel_y=1.0,
      image_width_pixels=2048,
      image_height_pixels=2048,
      calibrated_plate_to_stage_theta_radians=math.pi / 2,
      stage_x_scale=1.0,
      stage_y_scale=1.0,
    )
    hw = make_hardware_default_config()
    cs = CoordinateSystems.from_config(calib, hw)
    # sample (1,0) under +90deg plate->stage: GetBaseCoord uses R^-1; for theta=pi/2
    # R^-1 = [[0,-1],[1,0]] so (1,0) -> (0,1).
    x, y = cs.sample_mm_to_stage_mm(1.0, 0.0)
    self.assertAlmostEqual(x, 0.0, places=6)
    self.assertAlmostEqual(y, 1.0, places=6)


if __name__ == "__main__":
  unittest.main()

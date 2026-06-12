"""Tests for the Celigo coordinate transforms."""

import unittest

from pylabrobot.celigo.config import AxisConfig, Calibrated2DCubicTransform
from pylabrobot.celigo.transforms import (
  encoder_ticks_to_mm,
  evaluate_cubic_2d,
  galvo_mm_to_dac,
  galvo_mm_to_volts,
  galvo_volts_to_mm,
  mm_per_sec_to_ticks_per_sec,
  mm_to_encoder_ticks,
)


class TestEncoderTicks(unittest.TestCase):
  def setUp(self):
    # X axis: 0.0127 mm/tick, home offset -18, not inverted (real-ish values)
    self.x = AxisConfig(
      motion_name="X",
      mm_per_encoder_tick=0.0127,
      home_offset=-18.0,
      invert_axis_direction=False,
    )
    # Y axis: inverted
    self.y = AxisConfig(
      motion_name="Y",
      mm_per_encoder_tick=0.0127,
      home_offset=71.75,
      invert_axis_direction=True,
    )

  def test_mm_to_ticks_formula(self):
    # (10 * 1 + -18) / 0.0127 = -8/0.0127 = -629.9 -> -630
    self.assertEqual(mm_to_encoder_ticks(10.0, self.x), round((10 - 18) / 0.0127))

  def test_roundtrip_x(self):
    for mm in (0.0, 5.0, 23.7, 50.0):
      ticks = mm_to_encoder_ticks(mm, self.x)
      self.assertAlmostEqual(encoder_ticks_to_mm(ticks, self.x), mm, places=1)

  def test_roundtrip_inverted_y(self):
    for mm in (0.0, 12.3, 60.0):
      ticks = mm_to_encoder_ticks(mm, self.y)
      self.assertAlmostEqual(encoder_ticks_to_mm(ticks, self.y), mm, places=1)

  def test_velocity_conversion(self):
    self.assertEqual(mm_per_sec_to_ticks_per_sec(1.27, self.x), 100)

  def test_zero_tick_raises(self):
    with self.assertRaises(ValueError):
      mm_to_encoder_ticks(1.0, AxisConfig(motion_name="bad", mm_per_encoder_tick=0))


class TestGalvoPolynomial(unittest.TestCase):
  def test_pure_linear_volts_to_mm(self):
    # Forward linear-only: 1.3 mm per volt on each axis, no cross terms.
    t = Calibrated2DCubicTransform(
      forward={"LinearXTerm": (1.3, 0.0), "LinearYTerm": (0.0, 1.3)},
      reverse={"LinearXTerm": (1.0 / 1.3, 0.0), "LinearYTerm": (0.0, 1.0 / 1.3)},
    )
    self.assertAlmostEqual(galvo_volts_to_mm(t, 1.0, 0.0)[0], 1.3)
    self.assertAlmostEqual(galvo_volts_to_mm(t, 0.0, 2.0)[1], 2.6)

  def test_mm_to_volts_inverse_linear(self):
    t = Calibrated2DCubicTransform(
      forward={"LinearXTerm": (1.3, 0.0), "LinearYTerm": (0.0, 1.3)},
      reverse={"LinearXTerm": (1.0 / 1.3, 0.0), "LinearYTerm": (0.0, 1.0 / 1.3)},
    )
    vx, vy = galvo_mm_to_volts(t, 1.3, 0.0)
    self.assertAlmostEqual(vx, 1.0)

  def test_offset_and_cross_terms(self):
    terms = {
      "OffsetTerm": (0.5, -0.5),
      "LinearXTerm": (2.0, 0.0),
      "LinearYTerm": (0.0, 3.0),
      "CrossTerm": (0.1, 0.0),
    }
    # at (vx=2, vy=1): out_x = 0.5 + 2*2 + 0.1*2*1 = 4.7 ; out_y = -0.5 + 3*1 = 2.5
    out_x, out_y = evaluate_cubic_2d(terms, 2.0, 1.0)
    self.assertAlmostEqual(out_x, 4.7)
    self.assertAlmostEqual(out_y, 2.5)

  def test_cubic_terms(self):
    # CubicXTerm contributes vx**3 ; QuadraticXLinearYTerm contributes vx**2 * vy
    terms = {"CubicXTerm": (1.0, 0.0), "QuadraticXLinearYTerm": (0.0, 1.0)}
    out_x, out_y = evaluate_cubic_2d(terms, 2.0, 3.0)
    self.assertAlmostEqual(out_x, 8.0)  # 2**3
    self.assertAlmostEqual(out_y, 12.0)  # 2**2 * 3

  def test_mm_to_dac_is_16bit(self):
    t = Calibrated2DCubicTransform(
      reverse={"LinearXTerm": (1.0 / 1.3, 0.0), "LinearYTerm": (0.0, 1.0 / 1.3)},
    )
    xdac, ydac = galvo_mm_to_dac(t, 0.0, 0.0)
    self.assertEqual(xdac, 32768)  # 0 V -> midscale
    self.assertEqual(ydac, 32768)
    for d in galvo_mm_to_dac(t, 5.0, -5.0):
      self.assertTrue(0 <= d <= 65535)


if __name__ == "__main__":
  unittest.main()

"""Tests for calibrated Galvo field-offset conversion."""

import unittest
from unittest.mock import patch

import anyio

from pylabrobot.revvity.celigo.config import (
  Calibrated2DPolynomialTransform,
  GalvoAxisOpticalCalibration,
  GalvoMagnificationCalibration,
  GalvoOpticalCalibration,
)
from pylabrobot.revvity.celigo.errors import CeligoError
from pylabrobot.revvity.celigo.galvo import GalvoControllerStatus, volts_to_dac_count
from pylabrobot.revvity.celigo.tests.helpers import make_celigo


class TestGalvoPolynomial(unittest.TestCase):
  @staticmethod
  def _voltages_for_offset(reverse_terms, offset_mm):
    celigo = make_celigo()
    celigo.config.magnification = 3
    center = GalvoMagnificationCalibration(center_voltage=0.0, frame_size_volts=0.0)
    celigo.config.galvo_optical_calibration = GalvoOpticalCalibration(
      x=GalvoAxisOpticalCalibration({3: center}, {}, 0.0, 0.0),
      y=GalvoAxisOpticalCalibration({3: center}, {}, 0.0, 0.0),
    )
    celigo.config.galvo_calibrations = {
      2: Calibrated2DPolynomialTransform(forward={}, reverse=reverse_terms, order=3)
    }
    return celigo.galvo.voltages_for_offset(2, offset_mm)

  def test_mm_to_volts_inverse_linear(self):
    x_voltage, y_voltage = self._voltages_for_offset(
      {"LinearXTerm": (1.0 / 1.3, 0.0), "LinearYTerm": (0.0, 1.0 / 1.3)},
      (1.3, 2.6),
    )
    self.assertAlmostEqual(x_voltage, -1.0)
    self.assertAlmostEqual(y_voltage, 2.0)

  def test_offset_and_cross_terms(self):
    x_voltage, y_voltage = self._voltages_for_offset(
      {
        "OffsetTerm": (0.5, -0.5),
        "LinearXTerm": (2.0, 0.0),
        "LinearYTerm": (0.0, 3.0),
        "CrossTerm": (0.1, 0.0),
      },
      (2.0, 1.0),
    )
    self.assertAlmostEqual(x_voltage, -3.7)
    self.assertAlmostEqual(y_voltage, 2.5)

  def test_cubic_terms(self):
    x_voltage, y_voltage = self._voltages_for_offset(
      {"CubicXTerm": (1.0, 0.0), "QuadraticXLinearYTerm": (0.0, 1.0)},
      (2.0, 3.0),
    )
    self.assertAlmostEqual(x_voltage, -8.0)
    self.assertAlmostEqual(y_voltage, 12.0)

  def test_unknown_polynomial_term_is_rejected(self):
    with self.assertRaisesRegex(CeligoError, "Unsupported.*MysteryTerm"):
      self._voltages_for_offset({"MysteryTerm": (1.0, 1.0)}, (1.0, 1.0))

  def test_non_finite_polynomial_coefficient_is_rejected(self):
    with self.assertRaisesRegex(CeligoError, "LinearXTerm.*not finite"):
      self._voltages_for_offset({"LinearXTerm": (float("nan"), 0.0)}, (1.0, 1.0))

  def test_dac_conversion_rejects_out_of_range_voltage(self):
    with self.assertRaisesRegex(ValueError, "range -10..10 V"):
      volts_to_dac_count(10.01)


class TestGalvoSettling(unittest.IsolatedAsyncioTestCase):
  async def test_zero_timeout_checks_the_first_status(self):
    galvo = make_celigo().galvo

    async def status():
      await anyio.lowlevel.checkpoint()
      return GalvoControllerStatus(False, False, 0, 0, 0, 0, 0, 0, False, 0)

    with patch.object(galvo, "request_controller_status", status):
      await galvo._wait_until_settled(0)

    async def busy():
      await anyio.lowlevel.checkpoint()
      return GalvoControllerStatus(True, False, 0, 0, 0, 0, 0, 0, False, 0)

    with patch.object(galvo, "request_controller_status", busy):
      with self.assertRaisesRegex(TimeoutError, "Galvos did not settle"):
        await galvo._wait_until_settled(0)

  async def test_shielded_status_wait_times_out_when_status_stalls(self):
    galvo = make_celigo().galvo

    async def stalled():
      await anyio.sleep_forever()

    with patch.object(galvo, "request_controller_status", stalled):
      with anyio.CancelScope(shield=True):
        with anyio.fail_after(0.2):
          with self.assertRaisesRegex(TimeoutError, "Galvos did not settle"):
            await galvo._wait_until_settled(0.02)


if __name__ == "__main__":
  unittest.main()

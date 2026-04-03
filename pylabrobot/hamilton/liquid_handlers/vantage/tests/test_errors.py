"""Tests for Vantage error handling."""

import unittest

from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
from pylabrobot.hamilton.liquid_handlers.vantage.errors import (
  VantageFirmwareError,
  convert_vantage_firmware_error_to_plr_error,
  vantage_response_string_to_error,
)
from pylabrobot.resources.errors import HasTipError, NoTipError


class TestVantageResponseStringToError(unittest.TestCase):
  def test_pip_error(self):
    error_str = 'A1PMDAid1234er1es"P175"'
    error = vantage_response_string_to_error(error_str)
    self.assertIsInstance(error, VantageFirmwareError)
    self.assertIn("Pipetting channel 1", error.errors)
    self.assertEqual(error.errors["Pipetting channel 1"], "No tip picked up")

  def test_core96_error(self):
    error_str = 'A1HMDAid1234er1es"H075"'
    error = vantage_response_string_to_error(error_str)
    self.assertIsInstance(error, VantageFirmwareError)
    self.assertIn("Core 96", error.errors)
    self.assertEqual(error.errors["Core 96"], "No tip picked up")

  def test_et_format_error(self):
    error_str = 'A1PMDAid1234et"some error text"'
    error = vantage_response_string_to_error(error_str)
    self.assertIsInstance(error, VantageFirmwareError)
    self.assertIn("Pip", error.errors)
    self.assertEqual(error.errors["Pip"], "some error text")

  def test_error_equality(self):
    e1 = VantageFirmwareError({"ch": "test"}, "raw")
    e2 = VantageFirmwareError({"ch": "test"}, "raw")
    self.assertEqual(e1, e2)

  def test_error_str(self):
    e = VantageFirmwareError({"ch": "test"}, "raw")
    self.assertIn("VantageFirmwareError", str(e))


class TestConvertToPLRError(unittest.TestCase):
  def test_tip_already_picked_up(self):
    error = VantageFirmwareError(
      {"Pipetting channel 1": "Tip already picked up"},
      "raw",
    )
    result = convert_vantage_firmware_error_to_plr_error(error)
    assert isinstance(result, ChannelizedError)
    self.assertIsInstance(result.errors[0], HasTipError)

  def test_no_tip_picked_up(self):
    error = VantageFirmwareError(
      {"Pipetting channel 1": "No tip picked up"},
      "raw",
    )
    result = convert_vantage_firmware_error_to_plr_error(error)
    assert isinstance(result, ChannelizedError)
    self.assertIsInstance(result.errors[0], NoTipError)

  def test_non_channel_error_returns_none(self):
    error = VantageFirmwareError(
      {"Core 96": "No tip picked up"},
      "raw",
    )
    result = convert_vantage_firmware_error_to_plr_error(error)
    self.assertIsNone(result)


if __name__ == "__main__":
  unittest.main()

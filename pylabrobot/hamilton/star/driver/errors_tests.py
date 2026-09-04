import unittest

from .errors import (
  StopError,
  UnknownHamiltonError,
  error_code_to_exception,
  star_firmware_string_to_error,
  trace_information_to_string,
)


class TestSTARErrorParsing(unittest.TestCase):
  def test_error_code_36_maps_to_stop_error(self):
    self.assertIs(error_code_to_exception(36), StopError)

  def test_i0_stop_trace_has_hood_open_message(self):
    self.assertEqual(
      trace_information_to_string("I0", 36),
      "Hamilton will not run while the hood is open",
    )

  def test_i0_short_form_36_is_stop_error(self):
    firmware_error = star_firmware_string_to_error(
      {"I0": "36"}, raw_response="C0QMid0001er99/00 I036"
    )

    error = firmware_error.errors["Auto Load"]
    self.assertIsInstance(error, StopError)
    self.assertEqual(error.message, "Hamilton will not run while the hood is open")
    self.assertEqual(error.trace_information, 36)
    self.assertEqual(error.raw_response, "36")
    self.assertEqual(error.raw_module, "I0")
    self.assertEqual(firmware_error.raw_response, "C0QMid0001er99/00 I036")

  def test_master_code_36_is_stop_error_with_trace(self):
    firmware_error = star_firmware_string_to_error(
      {"C0": "36/10"}, raw_response="C0QMid0001er36/10"
    )

    error = firmware_error.errors["Master"]
    self.assertIsInstance(error, StopError)
    self.assertEqual(error.message, "CAN error")
    self.assertEqual(error.trace_information, 10)
    self.assertEqual(error.raw_response, "36/10")
    self.assertEqual(error.raw_module, "C0")

  def test_x0_short_form_36_remains_unknown_error(self):
    firmware_error = star_firmware_string_to_error({"X0": "36"}, raw_response="X0QMid0001er36")

    error = firmware_error.errors["X-drives"]
    self.assertIsInstance(error, UnknownHamiltonError)
    self.assertEqual(error.message, "Stop during execution of command")
    self.assertEqual(error.trace_information, 36)
    self.assertEqual(error.raw_response, "36")
    self.assertEqual(error.raw_module, "X0")

  def test_other_short_form_36_remains_unknown_error(self):
    firmware_error = star_firmware_string_to_error({"P1": "36"}, raw_response="P1QMid0001er36")

    error = firmware_error.errors["Pipetting channel 1"]
    self.assertIsInstance(error, UnknownHamiltonError)
    self.assertEqual(error.message, "Stop during execution of command")

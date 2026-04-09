"""Tests for MT-SICS response parsing, validation, and protocol simulation."""

import unittest

from pylabrobot.scales.mettler_toledo.backend import (
  MettlerToledoResponse,
  MettlerToledoWXS205SDUBackend,
)
from pylabrobot.scales.mettler_toledo.errors import MettlerToledoError

R = MettlerToledoResponse


class MTSICSResponseParsingTests(unittest.TestCase):
  """Tests for response parsing helpers - no hardware or simulator needed."""

  def setUp(self):
    self.backend = MettlerToledoWXS205SDUBackend.__new__(MettlerToledoWXS205SDUBackend)

  def test_parse_errors_ES_ET_EL(self):
    """General error codes (ES, ET, EL) must raise the correct MettlerToledoError.
    These are the first line of defense against protocol-level failures."""
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("ES", ""))
    self.assertIn("Syntax error", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("ET", ""))
    self.assertIn("Transmission error", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("EL", ""))
    self.assertIn("Logical error", str(ctx.exception))

  def test_parse_errors_status_codes(self):
    """Command-specific status codes (I, L, +, -) must raise descriptive errors.
    These catch device-busy, bad parameters, and overload/underload conditions."""
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "I"))
    self.assertIn("not executable at present", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "L"))
    self.assertIn("incorrect parameter", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "+"))
    self.assertIn("overload", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "-"))
    self.assertIn("underload", str(ctx.exception))

  def test_validate_response_rejects_short(self):
    """Responses with fewer fields than expected must be rejected.
    Prevents silent IndexError when accessing data fields."""
    with self.assertRaises(MettlerToledoError):
      MettlerToledoWXS205SDUBackend._validate_response(R("I4", "A"), 3, "I4")

    # should not raise
    MettlerToledoWXS205SDUBackend._validate_response(R("I4", "A", ["B207696838"]), 3, "I4")

  def test_validate_unit_rejects_wrong(self):
    """Non-gram unit responses must be rejected.
    The backend assumes grams throughout - a wrong unit would produce wrong values."""
    with self.assertRaises(MettlerToledoError):
      MettlerToledoWXS205SDUBackend._validate_unit("kg", "S")

    # should not raise
    MettlerToledoWXS205SDUBackend._validate_unit("g", "S")

  def test_parse_errors_passes_valid_success(self):
    """A valid success response (status A) must not raise.
    Ensures the happy path is not accidentally blocked."""
    self.backend._parse_basic_errors(R("Z", "A"))

  def test_parse_errors_weight_response_error(self):
    """S S Error responses (hardware faults detected during weighing) must raise.
    These indicate boot errors, EEPROM failures, etc. on the physical device."""
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "S", ["Error", "10b"]))
    self.assertIn("EEPROM error", str(ctx.exception))

  def test_dataclass_construction(self):
    """MettlerToledoResponse dataclass must correctly separate command, status, and data.
    This is the foundation for all response access throughout the backend."""
    resp = R("S", "S", ["0.00006", "g"])
    self.assertEqual(resp.command, "S")
    self.assertEqual(resp.status, "S")
    self.assertEqual(resp.data, ["0.00006", "g"])

    # Error-only response (no status)
    resp = R("ES", "")
    self.assertEqual(resp.command, "ES")
    self.assertEqual(resp.status, "")
    self.assertEqual(resp.data, [])


if __name__ == "__main__":
  unittest.main()

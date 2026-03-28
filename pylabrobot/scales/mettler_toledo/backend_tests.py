"""Tests for MT-SICS response parsing and validation."""

import unittest

from pylabrobot.scales.mettler_toledo.backend import MettlerToledoWXS205SDUBackend
from pylabrobot.scales.mettler_toledo.errors import MettlerToledoError


class MTSICSResponseParsingTests(unittest.TestCase):
  """Tests for MT-SICS response parsing - no hardware needed."""

  def setUp(self):
    self.backend = MettlerToledoWXS205SDUBackend.__new__(MettlerToledoWXS205SDUBackend)

  def test_parse_errors_ES_ET_EL(self):
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["ES"])
    self.assertIn("Syntax error", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["ET"])
    self.assertIn("Transmission error", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["EL"])
    self.assertIn("Logical error", str(ctx.exception))

  def test_parse_errors_status_codes(self):
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["S", "I"])
    self.assertIn("not executable at present", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["S", "L"])
    self.assertIn("incorrect parameter", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["S", "+"])
    self.assertIn("overload", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["S", "-"])
    self.assertIn("underload", str(ctx.exception))

  def test_validate_response_rejects_short(self):
    with self.assertRaises(MettlerToledoError):
      MettlerToledoWXS205SDUBackend._validate_response(["I4", "A"], 3, "I4")

    # should not raise
    MettlerToledoWXS205SDUBackend._validate_response(["I4", "A", '"B207696838"'], 3, "I4")

  def test_validate_unit_rejects_wrong(self):
    with self.assertRaises(MettlerToledoError):
      MettlerToledoWXS205SDUBackend._validate_unit("kg", "S")

    # should not raise
    MettlerToledoWXS205SDUBackend._validate_unit("g", "S")

  def test_parse_errors_handles_edge_cases(self):
    # empty response
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors([])
    self.assertIn("Empty response", str(ctx.exception))

    # single non-error token
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(["Z"])
    self.assertIn("Expected at least 2 fields", str(ctx.exception))

    # valid success - should not raise
    self.backend._parse_basic_errors(["Z", "A"])


if __name__ == "__main__":
  unittest.main()

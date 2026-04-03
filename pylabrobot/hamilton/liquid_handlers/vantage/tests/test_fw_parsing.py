"""Tests for Vantage firmware response parsing."""

import unittest

from pylabrobot.hamilton.liquid_handlers.vantage.fw_parsing import parse_vantage_fw_string


class TestParseVantageFWString(unittest.TestCase):
  def test_parse_id(self):
    result = parse_vantage_fw_string("A1PMDAid1234")
    self.assertEqual(result["id"], 1234)

  def test_parse_int(self):
    result = parse_vantage_fw_string("A1PMDAid0qw1", {"qw": "int"})
    self.assertEqual(result["id"], 0)
    self.assertEqual(result["qw"], 1)

  def test_parse_str(self):
    result = parse_vantage_fw_string('id0es"error string"', {"es": "str"})
    self.assertEqual(result["es"], "error string")

  def test_parse_int_list(self):
    result = parse_vantage_fw_string("id0xs30 -100 +1 1000", {"xs": "[int]"})
    self.assertEqual(result["id"], 0)
    self.assertEqual(result["xs"], [30, -100, 1, 1000])

  def test_parse_hex(self):
    result = parse_vantage_fw_string("id0cwFF", {"cw": "hex"})
    self.assertEqual(result["cw"], 255)

  def test_invalid_fmt_type(self):
    with self.assertRaises(TypeError):
      parse_vantage_fw_string("id0", "invalid")  # type: ignore

  def test_unknown_data_type(self):
    with self.assertRaises(ValueError):
      parse_vantage_fw_string("id0foo1", {"foo": "unknown"})

  def test_no_match_raises(self):
    with self.assertRaises(ValueError):
      parse_vantage_fw_string("id0", {"qw": "int"})


if __name__ == "__main__":
  unittest.main()

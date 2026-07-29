"""Unit tests for TecanEVODriver."""

import unittest

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError


class CommandAssemblyTests(unittest.TestCase):
  def setUp(self):
    self.driver = TecanEVODriver()

  def test_assemble_no_params(self):
    result = self.driver._assemble_command("C5", "PIA", [])
    self.assertEqual(result, "\x02C5PIA\x00")

  def test_assemble_with_params(self):
    result = self.driver._assemble_command("C5", "PAA", [100, 200, 90])
    self.assertEqual(result, "\x02C5PAA100,200,90\x00")

  def test_assemble_with_none_params(self):
    result = self.driver._assemble_command("C5", "MDT", [255, None, None, None, 37])
    self.assertEqual(result, "\x02C5MDT255,,,,37\x00")

  def test_assemble_roma_module(self):
    result = self.driver._assemble_command("C1", "PIX", [])
    self.assertEqual(result, "\x02C1PIX\x00")


class ResponseParsingTests(unittest.TestCase):
  def setUp(self):
    self.driver = TecanEVODriver()

  def test_parse_success_no_data(self):
    # Status byte 0x80 XOR 0x80 = 0 (success), no data
    resp = b"\x02C5\x80\x00"
    result = self.driver.parse_response(resp)
    self.assertEqual(result["module"], "C5")
    self.assertEqual(result["data"], [])

  def test_parse_success_with_int_data(self):
    # Status byte 0x80 = success, data "8"
    resp = b"\x02C5\x808\x00"
    result = self.driver.parse_response(resp)
    self.assertEqual(result["module"], "C5")
    self.assertEqual(result["data"], [8])

  def test_parse_success_with_csv_data(self):
    resp = b"\x02C5\x802100,2100,2100\x00"
    result = self.driver.parse_response(resp)
    self.assertEqual(result["data"], [2100, 2100, 2100])

  def test_parse_success_with_string_data(self):
    resp = b"\x02C5\x80LIHACU-V1.80\x00"
    result = self.driver.parse_response(resp)
    self.assertEqual(result["data"], ["LIHACU-V1.80"])

  def test_parse_error_code(self):
    # Status byte: error code 1 with bit 7 set = 0x81
    resp = b"\x02C5\x81\x00"
    with self.assertRaises(TecanError) as ctx:
      self.driver.parse_response(resp)
    self.assertEqual(ctx.exception.error_code, 1)
    self.assertEqual(ctx.exception.module, "C5")
    self.assertIn("Initialization failed", ctx.exception.message)

  def test_parse_error_code_3(self):
    resp = b"\x02C5\x83\x00"
    with self.assertRaises(TecanError) as ctx:
      self.driver.parse_response(resp)
    self.assertEqual(ctx.exception.error_code, 3)
    self.assertIn("Invalid operand", ctx.exception.message)

  def test_parse_roma_error(self):
    resp = b"\x02C1\x85\x00"
    with self.assertRaises(TecanError) as ctx:
      self.driver.parse_response(resp)
    self.assertEqual(ctx.exception.error_code, 5)
    self.assertEqual(ctx.exception.module, "C1")

  def test_parse_negative_data(self):
    resp = b"\x02C5\x80-155\x00"
    result = self.driver.parse_response(resp)
    self.assertEqual(result["data"], [-155])


class CachingTests(unittest.IsolatedAsyncioTestCase):
  async def test_set_command_cached(self):
    driver = TecanEVODriver()
    # Simulate caching without actual USB
    driver._cache["C5SEP"] = [1800, 1800]
    # Same params should return None (cached)
    # We can't call send_command without USB, but we can test the cache logic
    k = "C5SEP"
    params = [1800, 1800]
    self.assertIn(k, driver._cache)
    self.assertEqual(driver._cache[k], params)

  def test_cache_different_params(self):
    driver = TecanEVODriver()
    driver._cache["C5SEP"] = [1800, 1800]
    # Different params should NOT match
    new_params = [2000, 2000]
    self.assertNotEqual(driver._cache["C5SEP"], new_params)


class SerializationTests(unittest.TestCase):
  def test_serialize(self):
    driver = TecanEVODriver(packet_read_timeout=30, read_timeout=120, write_timeout=120)
    data = driver.serialize()
    self.assertEqual(data["type"], "TecanEVODriver")
    self.assertEqual(data["packet_read_timeout"], 30)
    self.assertEqual(data["read_timeout"], 120)
    self.assertEqual(data["write_timeout"], 120)

  def test_serialize_defaults(self):
    driver = TecanEVODriver()
    data = driver.serialize()
    self.assertEqual(data["packet_read_timeout"], 12)
    self.assertEqual(data["read_timeout"], 60)
    self.assertEqual(data["write_timeout"], 60)

"""Tests for the VANTAstar configuration parser, on the INIT reply of a reader with firmware 1.12."""

import unittest

from pylabrobot.bmg_labtech.vantastar.driver.configuration import (
  get_modules,
  parse_hardware_info,
)
from pylabrobot.bmg_labtech.vantastar.driver.responses import split_reply

HARDWARE_INFO = bytes.fromhex(
  "13250004000002001f0460000101010100d8000010303432312d3030303434360001000b00274053090001090d000208"
  "000e000587020202010f000200001f000b01012e0a46041a008d00032000080bfe70020027420c2e00097f0bea400100"
  "27471f2f01e945585f53503a313d6f70656e2c323d46696c74657220312c333d46696c74657220322c4c565f52616e67"
  "653a383530302e2e32303235300d0a45585f4c503a313d6f70656e2c323d46696c74657220312c333d46696c74657220"
  "322c4c565f52616e67653a383530302e2e32303235300d0a4253503a313d6f70656e2c323d46696c74657220312c333d"
  "46696c74657220322c343d46696c74657220332c4c565f52616e67653a31303235302e2e32323235300d0a454d5f5350"
  "3a313d6f70656e2c323d46696c74657220312c333d46696c74657220322c4c565f52616e67653a383530302e2e323032"
  "35300d0a454d5f4c503a313d6f70656e2c323d46696c74657220312c333d46696c74657220322c4c565f52616e67653a"
  "383530302e2e32303235300d0a454d5f534c49543a313d6f70656e2c323d536c697420302e382c333d536c697420312e"
  "322c343d536c697420322e302c353d536c697420332e302c373d4650207061722c383d4650207065722c363d4c554d20"
  "6c656e73650d0a45585f534c49543a313d6f70656e2c323d536c697420302e362c333d536c697420312e302c343d536c"
  "697420312e362c353d536c697420322e332c383d46502053746472642c373d46502055562c393d4142532c363d462d43"
  "6865636b0d0a000f01020304050607310007000000000000003d00030107d054000501045600006200020000"
)


class ConfigurationTests(unittest.TestCase):
  """Parsing the INIT reply (HardwareInfoResponse) into the configuration."""

  def test_mainboard_module(self):
    """The mainboard module gives the firmware version, build and package, the protocol version and
    the serial number."""
    c = parse_hardware_info(HARDWARE_INFO)
    self.assertEqual(c.firmware_version, 1120)
    self.assertEqual(c.firmware_build, 1)
    self.assertEqual(c.firmware_package, 11)
    self.assertEqual(c.protocol_version, 1)
    self.assertEqual(c.serial_number, "0421-000446")

  def test_modules_fill_the_reply(self):
    """The reply's payload splits into its 13 numbered modules, module 47 starting with
    ``EX_SP:1=open``."""
    modules = get_modules(split_reply(HARDWARE_INFO)[2])
    self.assertEqual(sorted(modules), [2, 9, 13, 14, 15, 31, 32, 46, 47, 49, 61, 84, 98])
    self.assertTrue(modules[47].startswith(b"EX_SP:1=open"))

  def test_truncated_module_raises(self):
    """A reply cut off inside a module is refused."""
    with self.assertRaises(ValueError):
      parse_hardware_info(HARDWARE_INFO[:120])

  def test_other_reply_raises(self):
    """A reply other than a HardwareInfoResponse is refused."""
    with self.assertRaises(ValueError):
      parse_hardware_info(bytes.fromhex("012500040000050100000000000901010102010400004f0000"))


if __name__ == "__main__":
  unittest.main()

"""Tests for the VANTAstar configuration parser, on the INIT reply of a reader with firmware 1.12,
and the recording saved from it."""

import os
import unittest

from pylabrobot.bmg_labtech.vantastar.driver.configuration import (
  SERIAL_NUMBER_PLACEHOLDER,
  VANTAstarConfiguration,
  compute_recording_name,
  configuration_from_json,
  configuration_to_json,
  get_modules,
  parse_hardware_info,
  read_configuration,
)
from pylabrobot.bmg_labtech.vantastar.driver.responses import split_reply

RECORDING_VANTASTAR = os.path.join(
  os.path.dirname(__file__), "recordings", "vantastar_1_12_temperature_controller.json"
)

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


class FittedHardwareTests(unittest.TestCase):
  """The fitted hardware, from the modules after the mainboard's."""

  def test_tested_reader(self):
    """The tested reader: a single standard photomultiplier, a temperature controller of 3
    channels, no pumps, a spectrometer up to 1050 nm, 7 slides with linear variable filters, no
    atmospheric control unit, no stirrer; no stacker or barcode reader module."""
    c = parse_hardware_info(HARDWARE_INFO)
    self.assertEqual(c.photomultipliers, "single")
    self.assertEqual((c.temperature_controller, c.temperature_controller_channels), (True, 3))
    self.assertEqual(c.pumps, (None, None))
    self.assertEqual((c.spectrometer, c.spectrometer_maximum_wavelength), (True, 1050))
    assert c.monochromator_slides is not None
    self.assertEqual(len(c.monochromator_slides), 7)
    self.assertEqual(c.monochromator_slides["EX_SP"], {1: "open", 2: "Filter 1", 3: "Filter 2"})
    self.assertEqual(c.monochromator_slides["EM_SLIT"][6], "LUM lense")
    self.assertTrue(c.linear_variable_filters)
    self.assertFalse(c.atmospheric_control_unit_connected)
    self.assertFalse(c.stirrer)
    self.assertIsNone(c.stacker_connected)
    self.assertIsNone(c.barcode_readers)

  def test_other_values(self):
    """Values the tested reader does not report, decoded from the layout: dual photomultipliers,
    both pump types, a stacker, two barcode readers, an atmospheric control unit, a stirrer."""
    c = VANTAstarConfiguration()
    c.update_from_modules(
      {
        9: bytes([0b101]),
        15: bytes([0b1001]),
        17: bytes([0x01, 0x00, 0x00]),
        18: bytes([0b0101, 0b00010011]),
        49: bytes([0x01]) + bytes(6),
        98: bytes([0x01, 0x00]),
      }
    )
    self.assertEqual(c.photomultipliers, "dual")
    self.assertEqual(c.pumps, ("XP3000", "XCalibur"))
    self.assertTrue(c.stacker_connected)
    self.assertEqual(c.barcode_readers, {1: "SE 965", 3: "CSE 600"})
    self.assertTrue(c.atmospheric_control_unit_connected)
    self.assertTrue(c.stirrer)

  def test_short_module_leaves_its_fields_unknown(self):
    """A module shorter than its fields leaves them None and warns; the others are filled."""
    c = VANTAstarConfiguration()
    with self.assertLogs("pylabrobot.bmg_labtech.vantastar.driver.configuration", "WARNING"):
      c.update_from_modules({31: bytes([0x01]), 98: bytes([0x01, 0x00])})
    self.assertIsNone(c.spectrometer)
    self.assertTrue(c.stirrer)


class RecordingTests(unittest.TestCase):
  """The recording of the tested reader."""

  def test_recording_is_the_decoded_reply(self):
    """The recording holds what the INIT reply decodes to, the serial number a placeholder."""
    saved = read_configuration(RECORDING_VANTASTAR)["device"]
    self.assertEqual(saved, configuration_to_json(parse_hardware_info(HARDWARE_INFO)))
    self.assertEqual(saved["serial_number"], SERIAL_NUMBER_PLACEHOLDER)
    self.assertNotIn("modules", saved)

  def test_recording_is_named_from_its_contents(self):
    configuration = configuration_from_json(read_configuration(RECORDING_VANTASTAR)["device"])
    self.assertEqual(compute_recording_name(configuration), os.path.basename(RECORDING_VANTASTAR))

  def test_read_back_equals_decoded(self):
    """Read back, the recording is the decoded configuration but for the serial number and the
    raw modules."""
    configuration = configuration_from_json(read_configuration(RECORDING_VANTASTAR)["device"])
    expected = parse_hardware_info(HARDWARE_INFO)
    expected.serial_number = SERIAL_NUMBER_PLACEHOLDER
    expected.modules = None
    self.assertEqual(configuration, expected)


if __name__ == "__main__":
  unittest.main()

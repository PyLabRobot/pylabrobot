"""Configuration tests: the INIT reply recorded from a CLARIOstar Plus, decoded and declared."""

import json
import os
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

from pylabrobot.bmg_labtech.clariostar.driver import configuration as configuration_module
from pylabrobot.bmg_labtech.clariostar.driver import master as master_module
from pylabrobot.bmg_labtech.clariostar.driver.configuration import (
  DeviceConfiguration,
  _parse_monochromator_slides,
)
from pylabrobot.bmg_labtech.clariostar.driver.framing import FTDI_HEADER
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  FIRMWARE_INFO_REPLY,
  INIT_REPLY,
  PARAMETERS_FRAME,
  PARAMETERS_REPLY,
  STATUS_IDLE,
  STATUS_QUERY_FRAME,
  FakeFTDI,
  make_driver,
)
from pylabrobot.bmg_labtech.clariostar.driver.parameter_table import (
  PARAMETER_TABLE,
  REPLY_OFFSET,
  SERIAL_NUMBER_ADDRESSES,
  SIGNED_PARAMETERS,
)
from pylabrobot.bmg_labtech.protocol.framing import ftdi_extract_payload, ftdi_wrap_payload

INIT_PAYLOAD = ftdi_extract_payload(INIT_REPLY)


def decoded() -> DeviceConfiguration:
  configuration = DeviceConfiguration()
  configuration.update_from_init_reply(INIT_PAYLOAD)
  return configuration


class DecodeTests(unittest.TestCase):
  def test_real_reply(self):
    c = decoded()
    self.assertEqual(
      (c.model, c.mainboard_revision, c.firmware_version), ("CLARIOstarPlus", 2, "1.35")
    )
    self.assertTrue(c.extended_red_photomultiplier_tube)
    self.assertTrue(c.dual_photomultiplier_tubes)
    self.assertFalse(c.photomultiplier_tube_crossover)
    self.assertEqual(c.excitation_slide_positions, 4)
    self.assertTrue(c.spectrometer)
    self.assertTrue(c.linear_variable_filters)
    self.assertTrue(c.temperature_controller)
    self.assertEqual(c.temperature_controller_channels, 2)
    self.assertEqual(c.pumps, (None, None, None))
    self.assertFalse(c.stacker_connected)
    self.assertEqual(c.barcode_readers, {})
    self.assertFalse(c.atmospheric_control_unit_connected)

  def test_monochromator_slides(self):
    slides = decoded().monochromator_slides
    assert slides is not None
    self.assertEqual(
      list(slides), ["EX_SP", "EX_LP", "BSP", "EM_SP", "EM_LP", "EM_SLIT", "EX_SLIT"]
    )
    self.assertEqual(slides["BSP"], {1: "open", 2: "Filter 1", 3: "Filter 2", 4: "Filter 3"})
    self.assertEqual(slides["EM_SLIT"][6], "LUM lense")

  def test_undecoded_firmware_fills_only_the_main_board(self):
    # Not a reader's reply: the recorded one with its firmware field set to 1.19, and to 2.11, the
    # line with native USB; no reader with either has answered.
    for firmware, version in ((1190, "1.19"), (2110, "2.11")):
      payload = bytearray(INIT_PAYLOAD)
      payload[9:11] = firmware.to_bytes(2, "big")
      configuration = DeviceConfiguration()
      with self.assertRaises(ValueError):
        configuration.update_from_init_reply(bytes(payload))
      self.assertEqual(
        (configuration.model, configuration.firmware_version), ("CLARIOstarPlus", version)
      )
      self.assertIsNone(configuration.spectrometer)

  def test_a_missing_block_leaves_only_its_fields_unknown(self):
    # Not a reader's reply: the recorded one without its block `2F`, the slides.
    payload, offset = bytearray(INIT_PAYLOAD), 6
    while payload[offset] != 0x2F:
      offset += 3 + int.from_bytes(payload[offset + 1 : offset + 3], "big")
    del payload[offset : offset + 3 + int.from_bytes(payload[offset + 1 : offset + 3], "big")]
    configuration = DeviceConfiguration()
    with patch.object(configuration_module.logger, "warning") as warning:
      configuration.update_from_init_reply(bytes(payload))
    self.assertIn("0x2f", warning.call_args.args[1].args[0])
    self.assertIsNone(configuration.monochromator_slides)
    self.assertIsNone(configuration.linear_variable_filters)
    complete = decoded()
    for field in ("spectrometer", "temperature_controller", "pumps", "barcode_readers"):
      self.assertEqual(getattr(configuration, field), getattr(complete, field), field)
    self.assertTrue(configuration.temperature_controller)

  def test_an_unreadable_slide_position_is_left_out(self):
    slides = _parse_monochromator_slides("BSP:1=open,x=Filter 1,3=Filter 2\nEM_SP:1=open")
    self.assertEqual(slides, {"BSP": {1: "open", 3: "Filter 2"}, "EM_SP": {1: "open"}})

  def test_not_an_init_reply(self):
    with self.assertRaises(ValueError):
      DeviceConfiguration().update_from_init_reply(ftdi_extract_payload(STATUS_IDLE))

  def test_cut_short_reply(self):
    with self.assertRaises(ValueError):
      DeviceConfiguration().update_from_init_reply(INIT_PAYLOAD[:-5])


class DriverTests(unittest.IsolatedAsyncioTestCase):
  async def test_initialize_reads_the_configuration(self):
    io = FakeFTDI()
    io.replies = [INIT_REPLY, STATUS_IDLE]
    driver = make_driver(io)
    await driver.initialize()
    self.assertEqual(driver.configuration.model, "CLARIOstarPlus")

  async def test_parameters_agreeing_with_init_do_not_warn(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY, STATUS_IDLE, FIRMWARE_INFO_REPLY]
    driver = make_driver(io)
    driver.configuration = decoded()
    with patch.object(master_module.logger, "warning") as warning:
      await driver.discover()
    warning.assert_not_called()

  async def test_parameters_disagreeing_with_init_warn(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY, STATUS_IDLE, FIRMWARE_INFO_REPLY]
    driver = make_driver(io)
    driver.configuration.firmware_version = "1.34"
    with self.assertLogs(master_module.logger, "WARNING") as logs:
      await driver.discover()
    self.assertIn(
      "firmware versions disagree: INIT reply 1.34, stored parameters 1.35",
      logs.output[0],
    )


class ParametersTests(unittest.IsolatedAsyncioTestCase):
  def make_driver(self, replies_of_parameters: int = 1):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY] * replies_of_parameters
    return make_driver(io), io

  def test_the_parameter_table_keys_and_addresses(self):
    keys = [key for key, _, _ in PARAMETER_TABLE]
    self.assertEqual(len(keys), len(set(keys)))
    owners: Dict[int, List[str]] = {}
    for key, address, size in PARAMETER_TABLE:
      self.assertLessEqual(address + size, 256, key)
      for byte in range(address, address + size):
        owners.setdefault(byte, []).append(key)
    for address in SERIAL_NUMBER_ADDRESSES:
      self.assertNotIn(address, owners)
    self.assertEqual([byte for byte, owner in owners.items() if len(owner) > 1], [])
    self.assertLessEqual(set(SIGNED_PARAMETERS), set(keys))

  async def test_request_parameters_from_real_reply(self):
    driver, io = self.make_driver()
    parameters = await driver.request_parameters()
    self.assertEqual(io.written, [STATUS_QUERY_FRAME, PARAMETERS_FRAME])
    self.assertEqual(parameters["firmware_version"], 1350)
    self.assertEqual(parameters["fluorescence_measurement_channel_count"], 1)
    self.assertEqual(parameters["fluorescence_auto_gain_level_1_photomultiplier_tube_1"], 494)
    self.assertEqual(parameters["top_bottom_slide_offset"], -5)
    self.assertEqual(parameters["x_transport_lock_position"], -168)
    self.assertEqual(parameters["temperature_sensor_1_offset"], 20)
    self.assertNotIn("serial_number", parameters)
    self.assertNotIn("project_number", parameters)

  async def test_convenience_methods(self):
    driver, _ = self.make_driver(replies_of_parameters=4)
    self.assertTrue(await driver.request_absorbance_available())
    self.assertEqual(await driver.request_time_resolved_fluorescence_channels(), 1)
    self.assertFalse(await driver.request_extended_temperature_range())
    self.assertFalse(await driver.request_well_1536_plates_supported())

  async def test_configuration_from_parameters(self):
    driver, _ = self.make_driver()
    c = DeviceConfiguration()
    c.update_from_parameters(await driver.request_parameters())
    self.assertEqual((c.fluorescence_channels, c.luminescence_channels), (1, 1))
    self.assertEqual(c.time_resolved_fluorescence_channels, 1)
    self.assertTrue(c.absorbance_available)
    self.assertTrue(c.bottom_measurement_available)
    self.assertFalse(c.alpha_screen_available)
    self.assertFalse(c.fluorescence_polarization_available)
    self.assertFalse(c.fluorescence_polarization_ultraviolet_available)
    self.assertFalse(c.extended_temperature_range)
    self.assertFalse(c.high_speed_shaking_allowed)
    self.assertFalse(c.well_1536_plates_supported)

  async def test_discover_reads_the_serial_number_and_checks_the_link(self):
    # Not a reader's reply: the recorded one, its serial cleaned to zeros, given made-up ones.
    def reply_with_serial(first: int, second: int) -> bytes:
      payload = bytearray(ftdi_extract_payload(PARAMETERS_REPLY))
      for address, value in zip(SERIAL_NUMBER_ADDRESSES, (first, second)):
        payload[address + REPLY_OFFSET : address + REPLY_OFFSET + 2] = value.to_bytes(2, "little")
      return ftdi_wrap_payload(bytes(payload), header=FTDI_HEADER)

    cases: List[Tuple[Tuple[int, int], Dict[str, Optional[str]], str, bool]] = [
      ((123, 4567), {"device_id": None}, "123-4567", False),
      ((123, 4567), {"device_id": "123-4567"}, "123-4567", False),
      ((123, 4567), {"device_id": "999-0001"}, "123-4567", True),
      ((12, 345), {"device_id": "012-0345"}, "012-0345", False),  # padded as the link has it
      ((123, 4567), {"device_id": "1:4", "usb_address": "1:4"}, "123-4567", False),  # by address
    ]
    for serial, link, expected, warns in cases:
      io = FakeFTDI()
      io.replies = [STATUS_IDLE, reply_with_serial(*serial), STATUS_IDLE, FIRMWARE_INFO_REPLY]
      driver = make_driver(io)
      driver.configuration = decoded()
      with (
        self.subTest(link=link),
        patch.object(io, "serialize", return_value={"vid": 0x0403, "pid": 0xBB68, **link}),
        patch.object(master_module.logger, "warning") as warning,
      ):
        await driver.discover()
        self.assertEqual(driver.configuration.serial_number, expected)
        mismatch = any("serial number" in str(call.args[0]) for call in warning.call_args_list)
        self.assertEqual(mismatch, warns)

  async def test_other_reply_is_not_decoded(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, FIRMWARE_INFO_REPLY]
    with self.assertRaises(ValueError):
      await make_driver(io).request_parameters()


# The driver's asyncio.Lock needs an event loop on Python 3.9: build drivers inside one.
class DeclaredConfigurationTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.path = os.path.join(self.directory.name, "clariostar.json")
    driver = make_driver(FakeFTDI())
    driver.configuration = decoded()
    driver.save_configuration(self.path)

  def tearDown(self):
    self.directory.cleanup()

  def declared_driver(self):
    driver = make_driver(FakeFTDI())
    driver.declared_configuration_json = self.path
    driver.configuration = decoded()
    return driver

  def test_matching_reader_passes(self):
    self.declared_driver()._check_declared_configuration()

  def test_differing_firmware_passes(self):
    driver = self.declared_driver()
    driver.configuration.firmware_version = "2.11"
    driver._check_declared_configuration()

  def test_differently_fitted_reader_is_refused(self):
    with open(self.path) as f:
      saved = json.load(f)
    saved["device"]["stacker_connected"] = True
    with open(self.path, "w") as f:
      json.dump(saved, f)
    with self.assertRaises(RuntimeError) as ctx:
      self.declared_driver()._check_declared_configuration()
    self.assertIn("stacker_connected: declared True, reader False", str(ctx.exception))

  def test_nothing_read_cannot_be_saved(self):
    with self.assertRaises(RuntimeError):
      make_driver(FakeFTDI()).save_configuration(self.path)


if __name__ == "__main__":
  unittest.main()


# An original CLARIOstar (main board revision 1) on firmware 1.20, compiled 2017-04-27: the
# payloads of its INIT, stored parameters and firmware info replies at its first connection. The
# serial number is zeroed at SERIAL_NUMBER_ADDRESSES, and the pumps' part numbers in block 0F's
# text are zeroed too; neither is decoded.
# INIT reply payload.
CLARIOSTAR_1_20_INIT_PAYLOAD = bytes.fromhex(
  "13250006300801000604b001020102090009010942500100013d460d000220000e00032700000f001f030550313a"
  "303030303030303020430d0a50323a303030303030303020430011000300000012000200001f00090100ec102204"
  "3d00212e000901044c0001001b32b12f017845585f53503a313d6f70656e2c323d46696c74657220312c333d4669"
  "6c74657220320d0a45585f4c503a313d6f70656e2c323d46696c74657220312c333d46696c74657220320d0a4253"
  "503a313d6f70656e2c323d46696c74657220312c333d46696c74657220322c343d46696c74657220330d0a454d5f"
  "53503a313d6f70656e2c323d46696c74657220312c333d46696c74657220320d0a454d5f4c503a313d6f70656e2c"
  "323d46696c74657220312c333d46696c74657220320d0a454d5f534c49543a313d6f70656e2c323d536c69742030"
  "2e382c333d536c697420312e322c343d536c697420322e302c353d536c697420332e302c373d4650207061722c38"
  "3d4650207065722c363d4c554d206c656e73650d0a45585f534c49543a313d6f70656e2c323d536c697420302e36"
  "2c333d536c697420312e302c343d536c697420312e362c353d536c697420322e332c383d46502053746472642c37"
  "3d46502055562c393d4142532c363d462d436865636b0d0a000131000700000000000000"
)
# Stored parameters reply payload.
CLARIOSTAR_1_20_PARAMETERS_PAYLOAD = bytes.fromhex(
  "070500263008000100000a0c0c010100000100540b00000f00c50000000000000003040000010000010200000000"
  "00000000000032000000000000000000000000000000000000000074006f0000000000000065000000dc05000000"
  "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000010000"
  "000101000000000000000100000100000000000f02980600000000b004000004005b0c00000000a40058ff8e030d"
  "0560ff6211fe0b621100001102990655ff950600000000b0140000000091009001463228460a0046061e00000000"
  "00000000001e03bd0628002c01900146001e000010120000000000000000000000"
)
# Firmware info reply payload.
CLARIOSTAR_1_20_FIRMWARE_INFO_PAYLOAD = bytes.fromhex(
  "0a050026300804b041707220323720323031370031333a32343a333400737570706f7274206f6620646f75626c65"
  "20504d5420636f6e66696775726174696f6e20287769746820657874656e64656420495220504d54293b206e6577"
  "207363616e206d6f64652073706972616c207363616e6e696e673b20636f6e74726f6c206f662041435520647269"
  "76656e2046414e323b20696d70726f76656420706175736520666561747572653a20706175736520696e2077656c"
  "6c206d6f64652c20636f6e74696e7565207768656e20656e7669726f6e6d656e7420636f6e646974696f6e207265"
  "61636865643b206e65772072656167656e742073617665207072696d696e67206f7074696f6e3b206e6577207368"
  "616b65206d6f64657320726f756e6420616e64206f76616c3b20696d70726f76656d656e74732072656761726469"
  "6e67207465737420657865637574696f6e2028666173746572207072657061726174696f6e2074696d652c20696e"
  "6a6563742d6f6e6c792070726f746f636f6c732c2e2e293b0001"
)
RECORDING_CLARIOSTAR_1_20 = os.path.join(
  os.path.dirname(__file__), "recordings", "clariostar_1_20_temperature_controller_pumps_2.json"
)


class OriginalCLARIOstarFirmware120Tests(unittest.TestCase):
  """The replies of an original CLARIOstar on firmware 1.20 decode to its recording."""

  def decoded(self) -> DeviceConfiguration:
    configuration = DeviceConfiguration()
    configuration.update_from_init_reply(CLARIOSTAR_1_20_INIT_PAYLOAD)
    configuration.update_from_parameters(
      master_module.CLARIOstarDriver._decode_parameters(CLARIOSTAR_1_20_PARAMETERS_PAYLOAD)
    )
    return configuration

  def test_replies_decode_to_the_recording(self):
    with open(RECORDING_CLARIOSTAR_1_20) as f:
      recorded = json.load(f)["device"]
    self.assertEqual(configuration_module.configuration_to_json(self.decoded()), recorded)

  def test_recording_name(self):
    self.assertEqual(
      configuration_module.compute_recording_name(self.decoded()),
      os.path.basename(RECORDING_CLARIOSTAR_1_20),
    )

  def test_init_and_parameters_agree_on_the_firmware(self):
    parameters = master_module.CLARIOstarDriver._decode_parameters(
      CLARIOSTAR_1_20_PARAMETERS_PAYLOAD
    )
    self.assertEqual(parameters["firmware_version"], 1200)
    self.assertEqual(self.decoded().firmware_version, "1.20")

  def test_firmware_build_date(self):
    self.assertEqual(
      master_module.CLARIOstarDriver._decode_firmware_build_date(
        CLARIOSTAR_1_20_FIRMWARE_INFO_PAYLOAD
      ),
      "2017-04-27",
    )

  def test_two_xcalibur_pumps_from_a_longer_pumps_block(self):
    blocks = dict(configuration_module._blocks(CLARIOSTAR_1_20_INIT_PAYLOAD))
    self.assertEqual(len(blocks[0x0F]), 31)  # 3 on the recorded Plus, which has no pumps
    self.assertEqual(self.decoded().pumps, ("XCalibur", "XCalibur", None))

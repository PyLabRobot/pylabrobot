"""Configuration tests: the INIT reply recorded from a CLARIOstar Plus, decoded and declared."""

import json
import logging
import os
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

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
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase

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
    # Not a reader's reply: the recorded one with its firmware field set to 1.29, and to 2.11, the
    # line with native USB; no reader with either has answered.
    for firmware, version in ((1290, "1.29"), (2110, "2.11")):
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
    self.assertEqual(
      (
        configuration.spectrometer,
        configuration.temperature_controller,
        configuration.pumps,
        configuration.barcode_readers,
      ),
      (
        complete.spectrometer,
        complete.temperature_controller,
        complete.pumps,
        complete.barcode_readers,
      ),
    )
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


class TestDriver(AnyioTestBase):
  async def test_initialize_reads_the_configuration(self):
    io = FakeFTDI()
    io.replies = [INIT_REPLY, STATUS_IDLE]
    driver = make_driver(io)
    await driver.initialize()
    assert driver.configuration.model == "CLARIOstarPlus"

  async def test_parameters_agreeing_with_init_do_not_warn(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY, STATUS_IDLE, FIRMWARE_INFO_REPLY]
    driver = make_driver(io)
    driver.configuration = decoded()
    with patch.object(master_module.logger, "warning") as warning:
      await driver.discover()
    warning.assert_not_called()

  async def test_parameters_disagreeing_with_init_warn(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ):
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY, STATUS_IDLE, FIRMWARE_INFO_REPLY]
    driver = make_driver(io)
    driver.configuration.firmware_version = "1.34"
    with caplog.at_level(logging.WARNING, logger=master_module.logger.name):
      await driver.discover()
    assert (
      "firmware versions disagree: INIT reply 1.34, stored parameters 1.35"
      in caplog.records[0].getMessage()
    )


class TestParameters(AnyioTestBase):
  def make_driver(self, replies_of_parameters: int = 1):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY] * replies_of_parameters
    return make_driver(io), io

  def test_the_parameter_table_keys_and_addresses(self):
    keys = [key for key, _, _ in PARAMETER_TABLE]
    assert len(keys) == len(set(keys))
    owners: Dict[int, List[str]] = {}
    for key, address, size in PARAMETER_TABLE:
      assert address + size <= 256, key
      for byte in range(address, address + size):
        owners.setdefault(byte, []).append(key)
    for address in SERIAL_NUMBER_ADDRESSES:
      assert address not in owners
    assert [byte for byte, owner in owners.items() if len(owner) > 1] == []
    assert set(SIGNED_PARAMETERS) <= set(keys)

  async def test_request_parameters_from_real_reply(self):
    driver, io = self.make_driver()
    parameters = await driver.request_parameters()
    assert io.written == [STATUS_QUERY_FRAME, PARAMETERS_FRAME]
    assert parameters["firmware_version"] == 1350
    assert parameters["fluorescence_measurement_channel_count"] == 1
    assert parameters["fluorescence_auto_gain_level_1_photomultiplier_tube_1"] == 494
    assert parameters["top_bottom_slide_offset"] == -5
    assert parameters["x_transport_lock_position"] == -168
    assert parameters["temperature_sensor_1_offset"] == 20
    assert "serial_number" not in parameters
    assert "project_number" not in parameters

  async def test_convenience_methods(self):
    driver, _ = self.make_driver(replies_of_parameters=4)
    assert await driver.request_absorbance_available()
    assert await driver.request_time_resolved_fluorescence_channels() == 1
    assert not await driver.request_extended_temperature_range()
    assert not await driver.request_well_1536_plates_supported()

  async def test_configuration_from_parameters(self):
    driver, _ = self.make_driver()
    c = DeviceConfiguration()
    c.update_from_parameters(await driver.request_parameters())
    assert (c.fluorescence_channels, c.luminescence_channels) == (1, 1)
    assert c.time_resolved_fluorescence_channels == 1
    assert c.absorbance_available
    assert c.bottom_measurement_available
    assert not c.alpha_screen_available
    assert not c.fluorescence_polarization_available
    assert not c.fluorescence_polarization_ultraviolet_available
    assert not c.extended_temperature_range
    assert not c.high_speed_shaking_allowed
    assert not c.well_1536_plates_supported

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
        patch.object(io, "serialize", return_value={"vid": 0x0403, "pid": 0xBB68, **link}),
        patch.object(master_module.logger, "warning") as warning,
      ):
        await driver.discover()
        assert driver.configuration.serial_number == expected
        mismatch = any("serial number" in str(call.args[0]) for call in warning.call_args_list)
        assert mismatch == warns

  async def test_other_reply_is_not_decoded(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, FIRMWARE_INFO_REPLY]
    with pytest.raises(ValueError):
      await make_driver(io).request_parameters()


class TestDeclaredConfiguration(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.directory = stack.enter_context(tempfile.TemporaryDirectory())
    self.path = os.path.join(self.directory, "clariostar.json")
    driver = make_driver(FakeFTDI())
    driver.configuration = decoded()
    driver.save_configuration(self.path)

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
    with pytest.raises(RuntimeError) as ctx:
      self.declared_driver()._check_declared_configuration()
    assert "stacker_connected: declared True, reader False" in str(ctx.value)

  def test_nothing_read_cannot_be_saved(self):
    with pytest.raises(RuntimeError):
      make_driver(FakeFTDI()).save_configuration(self.path)


if __name__ == "__main__":
  unittest.main()

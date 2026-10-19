"""Configuration tests: the INIT reply recorded from a CLARIOstar Plus, decoded and declared."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from pylabrobot.bmg_labtech.clariostar.driver import master as master_module
from pylabrobot.bmg_labtech.clariostar.driver.configuration import DeviceConfiguration
from pylabrobot.bmg_labtech.clariostar.driver.framing import ftdi_extract_payload
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
    # The recorded reply with its firmware field set to 1.29, and to 2.11, the line with native USB.
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
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY]
    driver = make_driver(io)
    driver.configuration = decoded()
    with patch.object(master_module.logger, "warning") as warning:
      await driver.discover()
    warning.assert_not_called()

  async def test_parameters_disagreeing_with_init_warn(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY]
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

  async def test_request_parameters_from_real_reply(self):
    driver, io = self.make_driver()
    parameters = await driver.request_parameters()
    self.assertEqual(io.written, [STATUS_QUERY_FRAME, PARAMETERS_FRAME])
    self.assertEqual(parameters["Firmware version"], 1350)
    self.assertEqual(parameters["No. of fluorescence measurement channels"], 1)
    self.assertEqual(parameters["Fluorescence AutoGain gain value level 1 (PMT1)"], 494)
    self.assertNotIn("Serial number of the reader", parameters)
    self.assertNotIn("Project number", parameters)

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

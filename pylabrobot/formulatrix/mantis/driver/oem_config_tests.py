"""Unit configuration tests: the unit's values from its vendor files, its declared file or a
recording."""

import json
import os
import tempfile
import unittest
from typing import Dict

from pylabrobot.formulatrix.mantis.driver.configuration import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestCase, FakeLink, make_answers
from pylabrobot.formulatrix.mantis.driver.oem_config import (
  _load_unit_configuration,
  _parse_config_entries,
)

with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
  RECORDING = json.load(f)
HEAD = '﻿<?xml version="1.0" encoding="utf-8"?>\r\n<configuration>\r\n  <appSettings>\r\n'
TAIL = "  </appSettings>\r\n</configuration>\r\n"
# Lines of a Mantis v4's vendor files, as installed.
V4_DEVICE_CONFIG = (
  HEAD
  + (
    '    <add key="PlateClampType" value="Solenoid" />\r\n'
    '    <add key="PlateClampController" value="MotorDC" />\r\n'
    '    <add key="PlateClampAuxId" value="2" />\r\n'
    '    <add key="RfidMultiplexerDelay" value="0" />\r\n'
    '    <add key="PlateClampInisiatePWM" value="0.119,0.21,0.3" />\r\n'
    '    <add key="PlateClampTimer" value="2000,1000,500" />\r\n'
  )
  + TAIL
)
V4_RFID_CONFIG = (
  HEAD
  + "".join(
    f'\t<add key="RFIDAntennaId.ACC{slot}" value="{antenna}" />\r\n'
    for slot, antenna in ((1, 0), (2, 1), (3, 4), (4, 6), (5, 7), (6, 5))
  )
  + TAIL
)
V4_AIR_PUMP_CONFIG = (
  HEAD
  + (
    '    <add key ="StartAirPumpSequenceFile" value="StartAirPump.seq.txt"/>\r\n'
    '    <add key ="StopAirPumpSequenceFile" value="StopAirPump.seq.txt"/>\r\n'
  )
  + TAIL
)
# Lines of a Mantis v3's Device.config, as installed; its install has no RFID.config.
V3_DEVICE_CONFIG = (
  HEAD
  + (
    '    <add key="PlateClampType" value="Pneumatic" />\r\n'
    '    <add key="PlateClampController" value="PressureRegulator" />\r\n'
    '    <add key="PlateClampAuxId" value="2" />\r\n'
    '    <add key="RfidMultiplexerDelay" value="0" />\r\n'
  )
  + TAIL
)


def write_files(*, directory: str, files: Dict[str, str]) -> str:
  for file_name, text in files.items():
    with open(os.path.join(directory, file_name), "w", encoding="utf-8", newline="") as f:
      f.write(text)
  return directory


class LoadUnitConfigurationTests(unittest.TestCase):
  def test_a_v4_folder_gives_its_lock_levels_and_antennas(self):
    with tempfile.TemporaryDirectory() as directory:
      files = {"Device.config": V4_DEVICE_CONFIG, "RFID.config": V4_RFID_CONFIG}
      blocks = _load_unit_configuration(
        oem_config_directory=write_files(directory=directory, files=files)
      )
    self.assertEqual(blocks, {block: RECORDING[block] for block in ("plate_clamp", "rfid")})

  def test_a_v3_folder_gives_neither(self):
    with tempfile.TemporaryDirectory() as directory:
      files = {"Device.config": V3_DEVICE_CONFIG}
      blocks = _load_unit_configuration(
        oem_config_directory=write_files(directory=directory, files=files)
      )
    self.assertEqual(blocks, {})

  def test_keys_with_spaces_before_the_equals_sign_are_read(self):
    with tempfile.TemporaryDirectory() as directory:
      write_files(directory=directory, files={"AirPump.config": V4_AIR_PUMP_CONFIG})
      entries = _parse_config_entries(path=os.path.join(directory, "AirPump.config"))
    self.assertEqual(entries["StopAirPumpSequenceFile"], "StopAirPump.seq.txt")

  def test_a_value_that_is_no_number_refuses(self):
    text = V4_DEVICE_CONFIG.replace("0.119,0.21,0.3", "0.119;0.21;0.3")
    with tempfile.TemporaryDirectory() as directory:
      write_files(directory=directory, files={"Device.config": text})
      with self.assertRaisesRegex(MantisConfigurationError, "PlateClampInisiatePWM is not a"):
        _load_unit_configuration(oem_config_directory=directory)


class UnitBlockSourceTests(DriverTestCase):
  async def set_up_with(self, **kwargs):
    return await self.set_up_driver(link=FakeLink(answers=make_answers()), **kwargs)

  def saved_blocks(self, *, driver, directory: str) -> Dict:
    path = os.path.join(directory, "saved.json")
    driver.save_configuration(path=path)
    with open(path, encoding="utf-8") as f:
      return {block: values for block, values in json.load(f).items() if block != "device"}

  async def test_the_folder_alone_gives_the_blocks_and_they_are_saved(self):
    with tempfile.TemporaryDirectory() as directory:
      files = {"Device.config": V4_DEVICE_CONFIG, "RFID.config": V4_RFID_CONFIG}
      driver = await self.set_up_with(
        oem_config_directory=write_files(directory=directory, files=files)
      )
      saved = self.saved_blocks(driver=driver, directory=directory)
    self.assertEqual(saved, {block: RECORDING[block] for block in ("plate_clamp", "rfid")})

  async def test_a_folder_and_declared_file_that_agree_set_up(self):
    with tempfile.TemporaryDirectory() as directory:
      files = {"Device.config": V4_DEVICE_CONFIG, "RFID.config": V4_RFID_CONFIG}
      driver = await self.set_up_with(
        oem_config_directory=write_files(directory=directory, files=files),
        declared_configuration_json=RECORDING_MANTIS_V4,
      )
    assert driver.plate_clamp is not None
    self.assertEqual(driver.plate_clamp.levels, {1: (0.119, 2000), 2: (0.21, 1000), 3: (0.3, 500)})

  async def test_a_folder_and_declared_file_that_disagree_refuse_setup(self):
    with tempfile.TemporaryDirectory() as directory:
      text = V4_DEVICE_CONFIG.replace("0.119,0.21,0.3", "0.12,0.21,0.3")
      write_files(directory=directory, files={"Device.config": text})
      driver = self.make_driver(
        link=FakeLink(answers=make_answers()),
        oem_config_directory=directory,
        declared_configuration_json=RECORDING_MANTIS_V4,
      )
      with self.assertRaisesRegex(MantisConfigurationError, "plate_clamp: declared"):
        await driver.setup()

  async def test_neither_takes_the_recording_warns_and_saves_none_of_it(self):
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.master", level="WARNING") as logs:
      driver = await self.set_up_with()
    self.assertTrue(any("less accurate on this unit" in line for line in logs.output))
    assert driver.rfid is not None
    self.assertEqual(driver.rfid.slot_to_antenna, {1: 0, 2: 1, 3: 4, 4: 6, 5: 7, 6: 5})
    with tempfile.TemporaryDirectory() as directory:
      self.assertEqual(self.saved_blocks(driver=driver, directory=directory), {})


if __name__ == "__main__":
  unittest.main()

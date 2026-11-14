"""Unit configuration tests: the unit's values from its vendor files, its declared file or a
recording."""

import json
import os
import tempfile
import unittest
from typing import Dict

from pylabrobot.formulatrix.mantis.driver.configuration import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError
from pylabrobot.formulatrix.mantis.driver.features.arm_v3_tests import SYNTHETIC_V3_ARM_BLOCK
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestCase, FakeLink, make_answers
from pylabrobot.formulatrix.mantis.driver.oem_config import (
  ENCODER_CORRECTION_DIRECTORY,
  ENCODER_CORRECTION_FILE,
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
    '    <add key="NumberOfLed" value="20" />\r\n'
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


# RobotArm.config lines in a v4 install's layout, with synthetic offsets and references.
SYNTHETIC_V4_ROBOT_ARM_CONFIG = (
  HEAD
  + (
    '    <add key="Motor.1.Offset" value="-0.1" />\r\n'
    '    <add key="Motor.1.HomingPositionN" value="-50.0" />\r\n'
    '    <add key="Motor.1.HomingPositionA" value="150.5" />\r\n'
    '    <add key="Motor.2.Offset" value="-0.5" />\r\n'
    '    <add key="Motor.2.HomingPositionN" value="120.0" />\r\n'
    '    <add key="Motor.2.HomingPositionA" value="50.25" />\r\n'
    '    <add key="Motor.3.Offset" value="0" />\r\n'
  )
  + TAIL
)
# A v3 install's RobotArm.config has no HomingPositionA.
SYNTHETIC_V3_ROBOT_ARM_CONFIG = (
  HEAD
  + (
    '    <add key="Motor.1.Offset" value="0" />\r\n'
    '    <add key="Motor.1.HomingPositionN" value="-50.0" />\r\n'
    '    <add key="Motor.2.Offset" value="0" />\r\n'
    '    <add key="Motor.2.HomingPositionN" value="120.0" />\r\n'
    '    <add key="Arm.PreHomingMovement.RetractStep" value="-50" />\r\n'
    '    <add key="Arm.PreHomingMovement.EnforceStep" value="20" />\r\n'
    '    <add key="Motor.1.DefaultVelocity" value="10000" />\r\n'
    '    <add key="Motor.1.DefaultAcceleration" value="1500" />\r\n'
    '    <add key="MotorXYHomingDelay" value="200" />\r\n'
    '    <add key="Arm.PostHomingPosition.Count" value="3" />\r\n'
    '    <add key="Arm.PostHomingPosition.1" value="-38.403,11.677,0" />\r\n'
    '    <add key="Arm.PostHomingPosition.2" value="-45.77,47.82,0" />\r\n'
    '    <add key="Arm.PostHomingPosition.3" value="15,31.177,0" />\r\n'
  )
  + "".join(
    f'    <add key="Motor.{motor}.{key}" value="{value}" />\r\n'
    for motor in (1, 2, 3)
    for key, value in {
      "StepsPerRevolution": 20000,
      "MicroSteps": 100,
      "PositionMapper.Pitch": 5.08 if motor == 3 else 360,
      "HomingPin": 0 if motor == 3 else 3,
      "HomingPinActiveState": 1,
      "HomingDirection": 1 if motor == 2 else 0,
      "HomingVelocity": 15 if motor == 3 else 100,
      "HomingAcceleration": 400 if motor == 3 else 2500,
    }.items()
  )
  + TAIL
)
# A synthetic encoder correction in the files' layout: ideal, measured, measured.
SYNTHETIC_CORRECTION_ROWS = [(row, row - 0.25) for row in range(1, 201)]
SYNTHETIC_CORRECTION_CSV = "\ufeff" + "".join(
  f"{ideal},{measured},{measured}\r\n" for ideal, measured in SYNTHETIC_CORRECTION_ROWS
)


def write_files(*, directory: str, files: Dict[str, str]) -> str:
  for file_name, text in files.items():
    with open(os.path.join(directory, file_name), "w", encoding="utf-8", newline="") as f:
      f.write(text)
  return directory


class LoadUnitConfigurationTests(unittest.TestCase):
  def test_a_v4_folder_gives_its_lock_levels_antennas_and_led_count(self):
    with tempfile.TemporaryDirectory() as directory:
      files = {"Device.config": V4_DEVICE_CONFIG, "RFID.config": V4_RFID_CONFIG}
      blocks = _load_unit_configuration(
        oem_config_directory=write_files(directory=directory, files=files)
      )
    self.assertEqual(
      blocks, {block: RECORDING[block] for block in ("plate_clamp", "rfid", "led_bar")}
    )

  def test_a_v3_folder_gives_the_pneumatic_clamp_output(self):
    with tempfile.TemporaryDirectory() as directory:
      files = {"Device.config": V3_DEVICE_CONFIG}
      blocks = _load_unit_configuration(
        oem_config_directory=write_files(directory=directory, files=files)
      )
    self.assertEqual(
      blocks, {"plate_clamp": {"controller": "pressure_regulator", "auxiliary_output": 2}}
    )

  def test_missing_or_unsupported_pneumatic_output_refuses(self):
    """The unit must explicitly map the supported clamp output."""
    for replacement in ('key="MissingAux" value="2"', 'key="PlateClampAuxId" value="1"'):
      with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
        config = V3_DEVICE_CONFIG.replace('key="PlateClampAuxId" value="2"', replacement)
        with self.assertRaises(MantisConfigurationError):
          _load_unit_configuration(
            oem_config_directory=write_files(directory=directory, files={"Device.config": config})
          )

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


def write_install(*, directory: str, configs: Dict[str, str], corrections: Dict[int, str]) -> str:
  """Lay out a unit's Data folder and return its Device/Configs folder."""
  configs_directory = os.path.join(directory, "Data", "Device", "Configs")
  correction_directory = os.path.join(directory, "Data", ENCODER_CORRECTION_DIRECTORY)
  os.makedirs(configs_directory)
  os.makedirs(correction_directory)
  write_files(directory=configs_directory, files=configs)
  write_files(
    directory=correction_directory,
    files={
      ENCODER_CORRECTION_FILE.format(motor=motor): text for motor, text in corrections.items()
    },
  )
  return configs_directory


class ArmBlockTests(unittest.TestCase):
  def load(self, *, configs: Dict[str, str], corrections: Dict[int, str]):
    with tempfile.TemporaryDirectory() as directory:
      return _load_unit_configuration(
        oem_config_directory=write_install(
          directory=directory, configs=configs, corrections=corrections
        )
      )

  def test_a_v4_install_gives_the_offsets_references_and_corrections(self):
    blocks = self.load(
      configs={"Device.config": V4_DEVICE_CONFIG, "RobotArm.config": SYNTHETIC_V4_ROBOT_ARM_CONFIG},
      corrections={0: SYNTHETIC_CORRECTION_CSV, 1: SYNTHETIC_CORRECTION_CSV},
    )
    rows = [[float(ideal), measured] for ideal, measured in SYNTHETIC_CORRECTION_ROWS]
    self.assertEqual(
      blocks["arm"],
      {
        "offsets": {"0": -0.1, "1": -0.5},
        "homing_position_a": {"0": 150.5, "1": 50.25},
        "encoder_correction": {"0": rows, "1": rows},
      },
    )

  def test_a_v3_install_gives_its_home_frame_rates_and_route(self):
    blocks = self.load(
      configs={"Device.config": V3_DEVICE_CONFIG, "RobotArm.config": SYNTHETIC_V3_ROBOT_ARM_CONFIG},
      corrections={},
    )
    self.assertEqual(blocks["arm"], SYNTHETIC_V3_ARM_BLOCK)

  def test_incomplete_or_incompatible_v3_calibration_refuses(self):
    """Do not substitute motion settings when the unit's file cannot be used."""
    for original, replacement in (
      ('key="Motor.1.HomingPin"', 'key="MissingPin"'),
      (
        'key="Motor.3.PositionMapper.Pitch" value="5.08"',
        'key="Motor.3.PositionMapper.Pitch" value="10"',
      ),
      ('value="15,31.177,0"', 'value="15,31.177,5"'),
    ):
      with self.subTest(original=original), self.assertRaises(MantisConfigurationError):
        self.load(
          configs={
            "Device.config": V3_DEVICE_CONFIG,
            "RobotArm.config": SYNTHETIC_V3_ROBOT_ARM_CONFIG.replace(original, replacement),
          },
          corrections={},
        )

  def test_a_missing_correction_refuses(self):
    with self.assertRaisesRegex(
      MantisConfigurationError, "motor 1's encoder correction is missing"
    ):
      self.load(
        configs={
          "Device.config": V4_DEVICE_CONFIG,
          "RobotArm.config": SYNTHETIC_V4_ROBOT_ARM_CONFIG,
        },
        corrections={0: SYNTHETIC_CORRECTION_CSV},
      )

  def test_a_correction_that_does_not_ascend_refuses(self):
    descending = "".join(f"{row},{200 - row},{200 - row}\r\n" for row in range(1, 201))
    with self.assertRaisesRegex(MantisConfigurationError, "measured column ascending"):
      self.load(
        configs={
          "Device.config": V4_DEVICE_CONFIG,
          "RobotArm.config": SYNTHETIC_V4_ROBOT_ARM_CONFIG,
        },
        corrections={0: descending, 1: SYNTHETIC_CORRECTION_CSV},
      )


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
    self.assertEqual(
      saved, {block: RECORDING[block] for block in ("plate_clamp", "rfid", "led_bar")}
    )

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

  async def test_a_folder_and_declared_file_that_disagree_on_the_led_count_refuse_setup(self):
    with tempfile.TemporaryDirectory() as directory:
      text = V4_DEVICE_CONFIG.replace('"NumberOfLed" value="20"', '"NumberOfLed" value="24"')
      write_files(directory=directory, files={"Device.config": text})
      driver = self.make_driver(
        link=FakeLink(answers=make_answers()),
        oem_config_directory=directory,
        declared_configuration_json=RECORDING_MANTIS_V4,
      )
      with self.assertRaisesRegex(MantisConfigurationError, "led_bar: declared"):
        await driver.setup()
    self.assertIsNone(driver.led_bar)

  async def test_an_older_declared_file_takes_what_it_lacks_from_the_folder(self):
    self.default_read_timeout = 0.25  # the 24 LEDs' colour writes are never answered
    older = {block: values for block, values in RECORDING.items() if block != "led_bar"}
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "older.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(older, f)
      text = V4_DEVICE_CONFIG.replace('"NumberOfLed" value="20"', '"NumberOfLed" value="24"')
      folder = write_files(directory=directory, files={"Device.config": text})
      with self.assertLogs("pylabrobot.formulatrix.mantis.driver.master", level="WARNING") as logs:
        driver = await self.set_up_with(
          oem_config_directory=folder, declared_configuration_json=path
        )
    self.assertEqual(len(logs.output), 1)
    self.assertIn(f"not in {path}: led_bar from {folder}", logs.output[0])
    self.assertNotIn("less accurate", logs.output[0])
    assert driver.led_bar is not None
    self.assertEqual(driver.led_bar.number_of_leds, 24)

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

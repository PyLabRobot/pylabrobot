"""Unit configuration tests: the unit's values from its vendor files, its declared file or a
recording."""

import json
import os
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.formulatrix.mantis.driver.configuration import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisConfigurationError
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestCase, FakeLink, make_answers
from pylabrobot.formulatrix.mantis.driver.oem_config import (
  COMMON_SEQUENCE_DIRECTORY,
  ENCODER_CORRECTION_DIRECTORY,
  ENCODER_CORRECTION_FILE,
  SBS_ADAPTER_FILE,
  UNKNOWN_ZONE,
  WASTE_STATION_ADAPTER_FILE,
  WASTE_STATION_FILE,
  ZONES,
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


# A synthetic stage: the plate holder's origin, a projective matrix and a tilt.
SYNTHETIC_STAGE_BLOCK: Dict[str, Any] = {
  "origin": [-50.0, 40.0, 51.0],
  "transformation_matrix": [[1.001, 0.001, -0.08], [-0.004, 0.9995, 0.09], [-2e-05, -3e-05, 1.004]],
  "offset_x": [0.0, 0.0, -0.0015],
  "offset_y": [0.0, 0.0, 0.0025],
  "arm_type_rotation_degree": 0.0,
  "arm_type_translation": [0.0, 0.0, 0.0],
}
# The synthetic stage in the layout of a v4 install's Device.config and SBS Adapter.ad.txt.
SYNTHETIC_STAGE_DEVICE_CONFIG_LINES = (
  '    <add key="ArmType" value="Standard" />\r\n'
  '    <add key="ArmTypeTranslation" value="X 0.000 Y 0.000 Z 0.000" />\r\n'
  '    <add key="ArmTypeRotationDegree" value="0" />\r\n'
  '    <add key="MicroplateOffsetX" value="X 0.000 Y 0.000 Z -0.0015" />\r\n'
  '    <add key="MicroplateOffsetY" value="X 0.000 Y 0.000 Z 0.0025" />\r\n'
  '    <add key="MicroplateTransformationMatrix" '
  'value="[[1.001,0.001,-0.08],[-0.004,0.9995,0.09],[-2E-05,-3E-05,1.004]]" />\r\n'
)
SYNTHETIC_SBS_ADAPTER = "[ Version: 1 ]\r\nX -50.000 Y 40.000 Z 51.000\r\n"


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
  )
  + TAIL
)
# A synthetic encoder correction in the files' layout: ideal, measured, measured.
SYNTHETIC_CORRECTION_ROWS = [(row, row - 0.25) for row in range(1, 201)]
SYNTHETIC_CORRECTION_CSV = "\ufeff" + "".join(
  f"{ideal},{measured},{measured}\r\n" for ideal, measured in SYNTHETIC_CORRECTION_ROWS
)


# Synthetic zones in DeviceZone.config's layout: (minimum x, maximum x, minimum y, maximum y), mm.
SYNTHETIC_ZONE_RECTANGLES = {
  "Front": [-10.0, 40.0, 30.0, 150.0],
  "RightBack": [40.0, 190.0, -90.0, -30.0],
  "RightMiddle": [40.0, 190.0, -30.0, 60.0],
  "RightFront": [40.0, 190.0, 60.0, 150.0],
  "LeftBack": [-160.0, -10.0, -90.0, -30.0],
  "LeftMiddle": [-160.0, -10.0, -30.0, 60.0],
  "LeftFront": [-160.0, -10.0, 60.0, 150.0],
}
SYNTHETIC_IDLE = [15.0, 31.177]
# Synthetic routes: each zone's safe XY, then to idle through it; the Front zone's and the Unknown
# zone's in the shape every install has, the Unknown zone's straight to idle.
SYNTHETIC_SAFE_XY = {
  "Front": SYNTHETIC_IDLE,
  "RightBack": [90.0, -50.0],
  "RightMiddle": [80.0, 0.0],
  "RightFront": [60.0, 70.0],
  "LeftBack": [-80.0, -50.0],
  "LeftMiddle": [-60.0, -10.0],
  "LeftFront": [-35.0, 70.0],
  UNKNOWN_ZONE: SYNTHETIC_IDLE,
}
SYNTHETIC_SAFE_XY_ROUTES = {zone: [[*xy, None]] for zone, xy in SYNTHETIC_SAFE_XY.items()}
SYNTHETIC_IDLE_ROUTES: Dict[str, List[List[Optional[float]]]] = {
  zone: [[*xy, None], [*xy, 0.0], [*SYNTHETIC_IDLE, 0.0]] for zone, xy in SYNTHETIC_SAFE_XY.items()
}
SYNTHETIC_IDLE_ROUTES["Front"] = [
  [None, None, 0.0],
  [*SYNTHETIC_IDLE, None],
  [*SYNTHETIC_IDLE, 0.0],
]
SYNTHETIC_IDLE_ROUTES[UNKNOWN_ZONE] = [[*SYNTHETIC_IDLE, 0.0]]
SYNTHETIC_ZONES_BLOCK = {
  "rectangles": SYNTHETIC_ZONE_RECTANGLES,
  "safe_xy_routes": SYNTHETIC_SAFE_XY_ROUTES,
  "idle_routes": SYNTHETIC_IDLE_ROUTES,
}
# Synthetic station routes from idle, in the shape of a v4 install's: the waste station's starts
# with a line naming no axis, the ready position and the safe height, Z -1.570.
SYNTHETIC_WASTE_ROUTE: List[List[Optional[float]]] = [
  [None, None, None],
  [15.0, 30.0, None],
  [None, None, -1.57],
  [15.0, 30.0, -1.57],
  [None, None, -1.57],
  [-50.0, 20.0, -1.57],
  [None, None, 30.0],
  [-60.0, 10.0, 30.0],
  [-60.0, 10.0, 35.0],
]
SYNTHETIC_WASH_ROUTE: List[List[Optional[float]]] = [
  [None, None, -1.57],
  [60.0, 40.0, -1.57],
  [85.0, 0.0, -1.57],
  [None, None, 40.0],
  [75.0, -50.0, 45.0],
]
SYNTHETIC_STATIONS_BLOCK = {"waste": SYNTHETIC_WASTE_ROUTE, "wash": {"1": SYNTHETIC_WASH_ROUTE}}


def get_route_text(*, route: List[List[Optional[float]]]) -> str:
  """A route as a sequence file writes it."""
  lines = []
  for waypoint in route:
    axes = "".join(
      f"{axis} {value:.3f} " for axis, value in zip("XYZ", waypoint) if value is not None
    )
    lines.append(f"MoveSequenceItem %00 True {axes} 0\r\n")
  return "".join(lines)


def get_zone_config(*, rectangles: Dict[str, List[float]]) -> str:
  """A DeviceZone.config naming each zone's route files."""
  entries = [
    f'\t<add key="{zone}Zone{bound}" value="{value:g}" />\r\n'
    for zone, rectangle in rectangles.items()
    for bound, value in zip(("MinimumX", "MaximumX", "MinimumY", "MaximumY"), rectangle)
  ]
  for family in ("SafeMoveToIdleFrom", "SafeMoveToSafeXYPositionFrom"):
    for zone in (*ZONES, UNKNOWN_ZONE):
      entries.append(
        f'\t<add key="{family}{zone}ZoneSequenceFile" value="{family}{zone}ZoneSequence.seq.txt" />'
        "\r\n"
      )
  return HEAD + "".join(entries) + TAIL


def get_route_files(*, zones: Dict[str, Any], stations: Dict[str, Any]) -> Dict[str, str]:
  """The route files of an install's System/Sequences/Common folder, by file name."""
  files = {}
  for family, field in (
    ("SafeMoveToIdleFrom", "idle_routes"),
    ("SafeMoveToSafeXYPositionFrom", "safe_xy_routes"),
  ):
    for zone, route in zones[field].items():
      files[f"{family}{zone}ZoneSequence.seq.txt"] = get_route_text(route=route)
  if "waste" in stations:
    files[WASTE_STATION_FILE] = (
      "RemarkSequenceItem Move%32To%32Waste%32Station \r\n"
      + get_route_text(route=stations["waste"])
    )
  for station, route in stations.get("wash", {}).items():
    files[f"MoveToWashStation{station}.seq.txt"] = get_route_text(route=route)
  return files


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

  def test_a_v3_install_gives_no_arm_block(self):
    blocks = self.load(
      configs={"Device.config": V3_DEVICE_CONFIG, "RobotArm.config": SYNTHETIC_V3_ROBOT_ARM_CONFIG},
      corrections={},
    )
    self.assertNotIn("arm", blocks)

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


def write_zone_install(
  *, directory: str, zones: Dict[str, Any], stations: Dict[str, Any], zone_config: bool = True
) -> str:
  """Lay out a v4 install's Data folder with zones and stations; return its Device/Configs."""
  configs = {"Device.config": V4_DEVICE_CONFIG}
  if zone_config:
    configs["DeviceZone.config"] = get_zone_config(rectangles=zones["rectangles"])
  configs_directory = write_install(directory=directory, configs=configs, corrections={})
  sequence_directory = os.path.join(directory, "Data", COMMON_SEQUENCE_DIRECTORY)
  os.makedirs(sequence_directory)
  write_files(directory=sequence_directory, files=get_route_files(zones=zones, stations=stations))
  return configs_directory


class RouteBlockTests(unittest.TestCase):
  def load(self, *, zones=SYNTHETIC_ZONES_BLOCK, stations=SYNTHETIC_STATIONS_BLOCK, **kwargs):
    with tempfile.TemporaryDirectory() as directory:
      return _load_unit_configuration(
        oem_config_directory=write_zone_install(
          directory=directory, zones=zones, stations=stations, **kwargs
        )
      )

  def test_a_v4_install_gives_its_zones_and_stations(self):
    blocks = self.load()
    self.assertEqual(blocks["zones"], SYNTHETIC_ZONES_BLOCK)
    self.assertEqual(blocks["stations"], SYNTHETIC_STATIONS_BLOCK)

  def test_without_device_zone_config_there_are_no_zones(self):
    blocks = self.load(zone_config=False)
    self.assertNotIn("zones", blocks)
    # A v3 does not read the v4's waste station route.
    self.assertEqual(blocks["stations"], {"wash": SYNTHETIC_STATIONS_BLOCK["wash"]})

  def test_each_generation_reads_its_own_waste_station_route(self):
    # Every unit seen has both files: a v4 (with DeviceZone.config) reads one, a v3 the other.
    for zone_config, expected in ((True, SYNTHETIC_WASTE_ROUTE), (False, SYNTHETIC_WASH_ROUTE)):
      with self.subTest(zone_config=zone_config):
        with tempfile.TemporaryDirectory() as directory:
          configs_directory = write_zone_install(
            directory=directory,
            zones=SYNTHETIC_ZONES_BLOCK,
            stations={"waste": SYNTHETIC_WASTE_ROUTE},
            zone_config=zone_config,
          )
          write_files(
            directory=os.path.join(directory, "Data", COMMON_SEQUENCE_DIRECTORY),
            files={WASTE_STATION_ADAPTER_FILE: get_route_text(route=SYNTHETIC_WASH_ROUTE)},
          )
          blocks = _load_unit_configuration(oem_config_directory=configs_directory)
        self.assertEqual(blocks["stations"], {"waste": expected})

  def test_a_missing_route_refuses(self):
    zones = {
      **SYNTHETIC_ZONES_BLOCK,
      "idle_routes": {
        zone: route for zone, route in SYNTHETIC_IDLE_ROUTES.items() if zone != "LeftBack"
      },
    }
    with self.assertRaisesRegex(MantisConfigurationError, "a route is missing"):
      self.load(zones=zones)

  def test_a_route_to_safe_xy_that_moves_z_refuses(self):
    zones = {
      **SYNTHETIC_ZONES_BLOCK,
      "safe_xy_routes": {**SYNTHETIC_SAFE_XY_ROUTES, "LeftBack": [[-80.0, -50.0, 0.0]]},
    }
    with self.assertRaisesRegex(MantisConfigurationError, "moves the arms only"):
      self.load(zones=zones)

  def test_a_rectangle_that_is_missing_or_inverted_refuses(self):
    rectangles = {**SYNTHETIC_ZONE_RECTANGLES, "Front": [40.0, -10.0, 30.0, 150.0]}
    with self.assertRaisesRegex(MantisConfigurationError, "minimums are not below"):
      self.load(zones={**SYNTHETIC_ZONES_BLOCK, "rectangles": rectangles})
    rectangles = {zone: values for zone, values in SYNTHETIC_ZONE_RECTANGLES.items()}
    del rectangles["LeftFront"]
    with self.assertRaisesRegex(MantisConfigurationError, "has no LeftFrontZoneMinimumX"):
      self.load(zones={**SYNTHETIC_ZONES_BLOCK, "rectangles": rectangles})


def write_stage_install(*, directory: str, device_lines: str, adapter: Optional[str]) -> str:
  """Lay out an install's Device.config and SBS Adapter.ad.txt; return its Device/Configs."""
  configs_directory = write_install(
    directory=directory,
    configs={"Device.config": V4_DEVICE_CONFIG.replace(TAIL, device_lines + TAIL)},
    corrections={},
  )
  if adapter is not None:
    adapter_path = os.path.join(os.path.dirname(configs_directory), SBS_ADAPTER_FILE)
    os.makedirs(os.path.dirname(adapter_path))
    with open(adapter_path, "w", encoding="utf-8", newline="") as f:
      f.write(adapter)
  return configs_directory


class StageBlockTests(unittest.TestCase):
  def load(
    self, *, device_lines=SYNTHETIC_STAGE_DEVICE_CONFIG_LINES, adapter=SYNTHETIC_SBS_ADAPTER
  ):
    with tempfile.TemporaryDirectory() as directory:
      return _load_unit_configuration(
        oem_config_directory=write_stage_install(
          directory=directory, device_lines=device_lines, adapter=adapter
        )
      )

  def test_an_install_gives_the_holders_origin_and_the_stage_transform(self):
    self.assertEqual(self.load()["stage"], SYNTHETIC_STAGE_BLOCK)

  def test_without_the_matrix_there_is_no_stage(self):
    lines = SYNTHETIC_STAGE_DEVICE_CONFIG_LINES.split("\r\n")
    self.assertNotIn("stage", self.load(device_lines="\r\n".join(lines[:5]) + "\r\n"))

  def test_a_missing_or_malformed_part_refuses(self):
    cases: List[Tuple[Dict[str, Any], str]] = [
      ({"adapter": None}, "the plate holder's origin is missing"),
      ({"adapter": SYNTHETIC_SBS_ADAPTER + "X 1 Y 2 Z 3\r\n"}, "one origin expected, found 2"),
      ({"adapter": "[ Version: 2 ]\r\nX 1 Y 2 Z 3\r\n"}, "version 2; only version 1 is known"),
      ({"adapter": "Origin 1 2 3\r\n"}, "is not 'X <x> Y <y> Z <z>'"),
      (
        {"device_lines": SYNTHETIC_STAGE_DEVICE_CONFIG_LINES.replace("Standard", "Other")},
        "ArmType 'Other'; only 'Standard'",
      ),
      (
        {"device_lines": SYNTHETIC_STAGE_DEVICE_CONFIG_LINES.replace(",1.004]", "]")},
        "is not 3 by 3",
      ),
      (
        {"device_lines": SYNTHETIC_STAGE_DEVICE_CONFIG_LINES.replace("Z -0.0015", "-0.0015")},
        "MicroplateOffsetX is not 'X <x> Y <y> Z <z>'",
      ),
      (
        {
          "device_lines": SYNTHETIC_STAGE_DEVICE_CONFIG_LINES.replace(
            '    <add key="ArmTypeRotationDegree" value="0" />\r\n', ""
          )
        },
        "has no ArmTypeRotationDegree",
      ),
    ]
    for kwargs, message in cases:
      with self.subTest(message=message):
        with self.assertRaisesRegex(MantisConfigurationError, message):
          self.load(**kwargs)


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

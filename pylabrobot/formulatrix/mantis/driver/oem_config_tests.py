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
  CHIP_CHANGER_SEQUENCE_DIRECTORY,
  CHIP_RACK_TYPE,
  COMMON_SEQUENCE_DIRECTORY,
  ENCODER_CORRECTION_DIRECTORY,
  ENCODER_CORRECTION_FILE,
  SBS_ADAPTER_FILE,
  UNKNOWN_ZONE,
  WASTE_APPROACH_PATH_ADAPTER_FILE,
  WASTE_APPROACH_PATH_FILE,
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
  "reference_height": 9.665,
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
# The arm block the synthetic v3 RobotArm.config gives.
SYNTHETIC_V3_ARM_BLOCK = {
  "offsets": {"0": 0.0, "1": 0.0},
  "homing_position_n": {"0": -90.0, "1": 216.0},
  "motors": {
    "0": {
      "method": 3,
      "positive_edge": True,
      "positive_direction": False,
      "speed": 100.0,
      "acceleration": 2500.0,
    },
    "1": {
      "method": 3,
      "positive_edge": True,
      "positive_direction": True,
      "speed": 100.0,
      "acceleration": 2500.0,
    },
    "2": {
      "method": 0,
      "positive_edge": True,
      "positive_direction": False,
      "speed": 15.0,
      "acceleration": 400.0,
    },
  },
  "retract": -50.0,
  "backoff": 20.0,
  "jog_speed": 10000.0,
  "jog_acceleration": 1500.0,
  "motor_xy_homing_delay": 0.2,
  "post_homing_positions": [[-38.403, 11.677], [-45.77, 47.82], [15.0, 31.177]],
}
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
# Synthetic approach paths from idle, in the shape of a v4 install's: the waste's starts with a line
# naming no axis, the ready position and the safe height, Z -1.570; the washes share all but the end.
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
  [None, None, 30.0],
  [None, None, 40.0],
  [75.0, -50.0, 40.0],
  [75.0, -50.0, 45.0],
]
# The other wash: the same path to 30 mm down, then its own safe Z, safe-Z line and bottom.
SYNTHETIC_WASH_2_ROUTE: List[List[Optional[float]]] = [
  *SYNTHETIC_WASH_ROUTE[:4],
  [None, None, 40.5],
  [90.0, -50.0, 40.5],
  [90.0, -50.0, 45.5],
]
SYNTHETIC_APPROACH_PATHS_BLOCK = {
  "waste": SYNTHETIC_WASTE_ROUTE,
  "wash_1": SYNTHETIC_WASH_ROUTE,
  "wash_2": SYNTHETIC_WASH_2_ROUTE,
}


def get_route_text(*, route: List[List[Optional[float]]]) -> str:
  """A route as a sequence file writes it."""
  lines = []
  for route_line in route:
    axes = "".join(
      f"{axis} {value:.3f} " for axis, value in zip("XYZ", route_line) if value is not None
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


def get_route_files(*, zones: Dict[str, Any], approach_paths: Dict[str, Any]) -> Dict[str, str]:
  """The route files of an install's System/Sequences/Common folder, by file name."""
  files = {}
  for family, field in (
    ("SafeMoveToIdleFrom", "idle_routes"),
    ("SafeMoveToSafeXYPositionFrom", "safe_xy_routes"),
  ):
    for zone, route in zones[field].items():
      files[f"{family}{zone}ZoneSequence.seq.txt"] = get_route_text(route=route)
  for name, route in approach_paths.items():
    if name == "waste":
      files[WASTE_APPROACH_PATH_FILE] = (
        "RemarkSequenceItem Move%32To%32Waste%32Station \r\n" + get_route_text(route=route)
      )
    else:
      position = name.split("_")[1]
      files[f"MoveToWashStation{position}.seq.txt"] = get_route_text(route=route)
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


def write_zone_install(
  *,
  directory: str,
  zones: Dict[str, Any],
  approach_paths: Dict[str, Any],
  zone_config: bool = True,
) -> str:
  """Lay out a v4 install's Data folder with zones and approach paths; return its Device/Configs."""
  configs = {"Device.config": V4_DEVICE_CONFIG}
  if zone_config:
    configs["DeviceZone.config"] = get_zone_config(rectangles=zones["rectangles"])
  configs_directory = write_install(directory=directory, configs=configs, corrections={})
  sequence_directory = os.path.join(directory, "Data", COMMON_SEQUENCE_DIRECTORY)
  os.makedirs(sequence_directory)
  write_files(
    directory=sequence_directory,
    files=get_route_files(zones=zones, approach_paths=approach_paths),
  )
  return configs_directory


class RouteBlockTests(unittest.TestCase):
  def load(
    self, *, zones=SYNTHETIC_ZONES_BLOCK, approach_paths=SYNTHETIC_APPROACH_PATHS_BLOCK, **kwargs
  ):
    with tempfile.TemporaryDirectory() as directory:
      return _load_unit_configuration(
        oem_config_directory=write_zone_install(
          directory=directory, zones=zones, approach_paths=approach_paths, **kwargs
        )
      )

  def test_a_v4_install_gives_its_zones_and_approach_paths(self):
    blocks = self.load()
    self.assertEqual(blocks["zones"], SYNTHETIC_ZONES_BLOCK)
    self.assertEqual(blocks["approach_paths"], SYNTHETIC_APPROACH_PATHS_BLOCK)

  def test_without_device_zone_config_there_are_no_zones(self):
    blocks = self.load(zone_config=False)
    self.assertNotIn("zones", blocks)
    # A v3 does not read the v4's waste approach path.
    self.assertEqual(
      blocks["approach_paths"],
      {"wash_1": SYNTHETIC_WASH_ROUTE, "wash_2": SYNTHETIC_WASH_2_ROUTE},
    )

  def test_each_generation_reads_its_own_waste_approach_path(self):
    # Every unit seen has both files: a v4 (with DeviceZone.config) reads one, a v3 the other.
    for zone_config, expected in ((True, SYNTHETIC_WASTE_ROUTE), (False, SYNTHETIC_WASH_ROUTE)):
      with self.subTest(zone_config=zone_config):
        with tempfile.TemporaryDirectory() as directory:
          configs_directory = write_zone_install(
            directory=directory,
            zones=SYNTHETIC_ZONES_BLOCK,
            approach_paths={"waste": SYNTHETIC_WASTE_ROUTE},
            zone_config=zone_config,
          )
          write_files(
            directory=os.path.join(directory, "Data", COMMON_SEQUENCE_DIRECTORY),
            files={WASTE_APPROACH_PATH_ADAPTER_FILE: get_route_text(route=SYNTHETIC_WASH_ROUTE)},
          )
          blocks = _load_unit_configuration(oem_config_directory=configs_directory)
        self.assertEqual(blocks["approach_paths"], {"waste": expected})

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

  def test_each_fixed_chip_slot_gives_its_routes_joined_and_an_lc3_slot_none(self):
    routes: Dict[str, List[List[Optional[float]]]] = {
      "SafeStart": [[None, None, -1.57]],
      "MoveAttach": [[10.0, 20.0, -1.57], [10.0, 20.0, 5.0]],
      "Origin": [[11.0, 21.0, 5.0]],
      "Shift": [[11.0, 25.0, 5.0]],
      "SafeStop": [[11.0, 25.0, None]],
    }
    keys = []
    for slot, chip_changer_type in ((1, CHIP_RACK_TYPE), (2, 1)):
      keys.append(f'    <add key="ChipChanger.{slot}.Type" value="{chip_changer_type}" />\r\n')
      for name in routes:
        keys.append(
          f'    <add key="ChipChanger.{slot}.{name}Sequence" value="{name}.seq.txt" />\r\n'
        )
    chip_changer_config = HEAD + '    <add key="ChipChanger.Count" value="2" />\r\n'
    chip_changer_config += "".join(keys) + TAIL
    with tempfile.TemporaryDirectory() as directory:
      configs_directory = write_zone_install(
        directory=directory, zones=SYNTHETIC_ZONES_BLOCK, approach_paths={}
      )
      write_files(directory=configs_directory, files={"ChipChanger.config": chip_changer_config})
      sequence_directory = os.path.join(directory, "Data", CHIP_CHANGER_SEQUENCE_DIRECTORY)
      os.makedirs(sequence_directory)
      write_files(
        directory=sequence_directory,
        files={f"{name}.seq.txt": get_route_text(route=route) for name, route in routes.items()},
      )
      blocks = _load_unit_configuration(oem_config_directory=configs_directory)
    joined = [route_line for route in routes.values() for route_line in route]
    self.assertEqual(blocks["approach_paths"], {"chip_slot_1": joined})


# A unit's StageTuning.config, in the layout of a v4 install.
SYNTHETIC_STAGE_TUNING = (
  '<?xml version="1.0" encoding="utf-8"?>\r\n<configuration>\r\n  <appSettings>\r\n'
  '    <add key="ReferenceHeight" value="9.665"/>\r\n    <add key="PitchHeight" value="2.5"/>\r\n'
  "  </appSettings>\r\n</configuration>\r\n"
)


def write_stage_install(
  *,
  directory: str,
  device_lines: str,
  adapter: Optional[str],
  stage_tuning: Optional[str] = SYNTHETIC_STAGE_TUNING,
) -> str:
  """Lay out an install's Device.config, StageTuning.config and SBS Adapter.ad.txt; return its
  Device/Configs."""
  configs = {"Device.config": V4_DEVICE_CONFIG.replace(TAIL, device_lines + TAIL)}
  if stage_tuning is not None:
    configs["StageTuning.config"] = stage_tuning
  configs_directory = write_install(directory=directory, configs=configs, corrections={})
  if adapter is not None:
    adapter_path = os.path.join(os.path.dirname(configs_directory), SBS_ADAPTER_FILE)
    os.makedirs(os.path.dirname(adapter_path))
    with open(adapter_path, "w", encoding="utf-8", newline="") as f:
      f.write(adapter)
  return configs_directory


class StageBlockTests(unittest.TestCase):
  def load(
    self,
    *,
    device_lines=SYNTHETIC_STAGE_DEVICE_CONFIG_LINES,
    adapter=SYNTHETIC_SBS_ADAPTER,
    stage_tuning=SYNTHETIC_STAGE_TUNING,
  ):
    with tempfile.TemporaryDirectory() as directory:
      return _load_unit_configuration(
        oem_config_directory=write_stage_install(
          directory=directory, device_lines=device_lines, adapter=adapter, stage_tuning=stage_tuning
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
      ({"stage_tuning": None}, "StageTuning.config is missing: the stage's reference height"),
      (
        {"stage_tuning": SYNTHETIC_STAGE_TUNING.replace("ReferenceHeight", "Other")},
        "has no ReferenceHeight",
      ),
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
    # Only the source warning counts: an unanswered LED write may warn too on a slow runner.
    sources = [line for line in logs.output if f"not in {path}" in line]
    self.assertEqual(len(sources), 1)
    self.assertIn(f"not in {path}: led_bar from {folder}", sources[0])
    self.assertNotIn("less accurate", sources[0])
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

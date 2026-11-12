"""A Mantis unit's own values, read from the unit's ``.config`` and sequence files.

Only the values set per unit are read: a block is loaded only where its source applies to the unit.
"""

import json
import os
import re
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Optional

from ..resource_model.chip import MANTIS_CHIP_FACTORIES
from .errors import MantisConfigurationError
from .sequence_files import Waypoint, parse_move_sequence
from .valve_sequences import VALVE_SEQUENCE_FILES, parse_valve_sequence

# Where a v4 install keeps each arm motor's absolute encoder correction, from its ``Data`` folder.
ENCODER_CORRECTION_DIRECTORY = os.path.join("System", "Encoder Correction", "Sampling File")
ENCODER_CORRECTION_FILE = "OneRevolutionSamplingData_Motor{motor}.csv"
# Where the unit's sequences that move the arm sit, below its ``Data`` folder.
COMMON_SEQUENCE_DIRECTORY = os.path.join("System", "Sequences", "Common")
# The unit's zones, in the order it lists them; a head in none of them is in the Unknown zone.
ZONES = ("Front", "RightBack", "RightMiddle", "RightFront", "LeftBack", "LeftMiddle", "LeftFront")
UNKNOWN_ZONE = "Unknown"
# The waste station's route on a v4, and on a v3: every unit seen has both, two different routes.
WASTE_STATION_FILE = "MoveToWasteStation.seq.txt"
WASTE_STATION_ADAPTER_FILE = "MoveToWasteStation_Adapter.seq.txt"
WASH_STATION_FILE = "MoveToWashStation{station}.seq.txt"
WASH_STATIONS = (1, 2)
# Where an install keeps the plate holder's origin, from its ``Data/Device`` folder.
SBS_ADAPTER_FILE = os.path.join("Adapters", "SBS Adapter.ad.txt")
# The only arm type on every unit seen; how the stage is framed for another is unknown.
STANDARD_ARM_TYPE = "Standard"
# Where an install keeps each chip type's folder, from its ``Data`` folder; its sequences sit in
# that folder's ``Sequences``.
CHIP_DIRECTORY = os.path.join("System", "Chips")
# Where an install keeps the routes to each chip changer position, from its ``Data`` folder.
CHIP_CHANGER_SEQUENCE_DIRECTORY = os.path.join("System", "Sequences", "ChipChanger")
# A chip changer position's routes, in the order a pick-up plays them, by ChipChanger.config key.
CHIP_CHANGER_ROUTES = {
  "safe_start": "SafeStartSequence",
  "move_attach": "MoveAttachSequence",
  "origin": "OriginSequence",
  "shift": "ShiftSequence",
  "safe_stop": "SafeStopSequence",
}
# The chip changer type of a position in the fixed chip racks; type 1 is an LC3's, not supported.
CHIP_RACK_TYPE = 0


def _parse_config_entries(*, path: str) -> Dict[str, str]:
  """The ``<add key="..." value="..." />`` settings of one ``.config`` file, by key.

  Raises:
    MantisConfigurationError: if the file is not well-formed XML.
  """
  try:
    root = ElementTree.parse(path).getroot()
  except ElementTree.ParseError as error:
    raise MantisConfigurationError(f"{path} is not a readable .config file: {error}") from error
  settings = root.find("appSettings")
  if settings is None:
    return {}
  return {
    entry.attrib["key"]: entry.attrib.get("value", "")
    for entry in settings.iter("add")
    if "key" in entry.attrib
  }


def _get_number(*, path: str, key: str, text: str) -> float:
  """A setting's value as a number.

  Raises:
    MantisConfigurationError: if it is not one.
  """
  try:
    return float(text)
  except ValueError as error:
    raise MantisConfigurationError(f"{path}: {key} is not a number: {text!r}") from error


def _get_setting(*, path: str, settings: Dict[str, str], key: str) -> str:
  """A setting's text.

  Raises:
    MantisConfigurationError: if the file has no such key.
  """
  if key not in settings:
    raise MantisConfigurationError(f"{path} has no {key}")
  return settings[key]


def _parse_vector(*, path: str, key: str, text: str) -> List[float]:
  """A vector written ``X <x> Y <y> Z <z>``, as the unit's files write one.

  Raises:
    MantisConfigurationError: if the text is not one, or a value is not a number.
  """
  match = re.fullmatch(r"\s*X\s+(\S+)\s+Y\s+(\S+)\s+Z\s+(\S+)\s*", text)
  if match is None:
    raise MantisConfigurationError(f"{path}: {key} is not 'X <x> Y <y> Z <z>': {text!r}")
  return [_get_number(path=path, key=key, text=value) for value in match.groups()]


def _parse_adapter_origin(*, path: str) -> List[float]:
  """The plate holder's origin, (x, y, z) in mm, from an adapter file read whole.

  Raises:
    MantisConfigurationError: if the file is missing, of a version other than 1, a line is not
      understood, or it does not hold one origin.
  """
  if not os.path.isfile(path):
    raise MantisConfigurationError(f"the plate holder's origin is missing: {path}")
  origins = []
  with open(path, encoding="utf-8-sig") as f:
    for number, line in enumerate(f, start=1):
      text = line.strip()
      if not text:
        continue
      version = re.fullmatch(r"\[\s*Version:\s*(\S+)\s*\]", text)
      if version is not None:
        if version.group(1) != "1":
          raise MantisConfigurationError(
            f"{path}: adapter file version {version.group(1)}; only version 1 is known"
          )
        continue
      origins.append(_parse_vector(path=path, key=f"line {number}", text=text))
  if len(origins) != 1:
    raise MantisConfigurationError(f"{path}: one origin expected, found {len(origins)}")
  return origins[0]


def _parse_transformation_matrix(*, path: str, text: str) -> List[List[float]]:
  """``MicroplateTransformationMatrix``: three rows of three numbers, written as JSON.

  Raises:
    MantisConfigurationError: if the text is not one.
  """
  try:
    matrix = json.loads(text)
  except ValueError as error:
    raise MantisConfigurationError(f"{path}: MicroplateTransformationMatrix is not JSON") from error
  if (
    not isinstance(matrix, list)
    or len(matrix) != 3
    or any(not isinstance(row, list) or len(row) != 3 for row in matrix)
    or any(
      isinstance(value, bool) or not isinstance(value, (int, float))
      for row in matrix
      for value in row
    )
  ):
    raise MantisConfigurationError(f"{path}: MicroplateTransformationMatrix is not 3 by 3: {text}")
  return [[float(value) for value in row] for row in matrix]


def _load_stage(*, path: str, values: Dict[str, str], adapter_path: str) -> Dict[str, Any]:
  """The ``stage`` block: the plate holder's origin and ``Device.config``'s stage transform.

  Raises:
    MantisConfigurationError: if a key or the adapter file is missing or malformed, or the arm
      type is not ``STANDARD_ARM_TYPE``.
  """
  arm_type = _get_setting(path=path, settings=values, key="ArmType")
  if arm_type != STANDARD_ARM_TYPE:
    raise MantisConfigurationError(
      f"{path}: ArmType {arm_type!r}; only {STANDARD_ARM_TYPE!r} is supported"
    )

  def get_vector(key: str) -> List[float]:
    return _parse_vector(path=path, key=key, text=_get_setting(path=path, settings=values, key=key))

  return {
    "origin": _parse_adapter_origin(path=adapter_path),
    "transformation_matrix": _parse_transformation_matrix(
      path=path,
      text=_get_setting(path=path, settings=values, key="MicroplateTransformationMatrix"),
    ),
    "offset_x": get_vector(key="MicroplateOffsetX"),
    "offset_y": get_vector(key="MicroplateOffsetY"),
    "arm_type_rotation_degree": _get_number(
      path=path,
      key="ArmTypeRotationDegree",
      text=_get_setting(path=path, settings=values, key="ArmTypeRotationDegree"),
    ),
    "arm_type_translation": get_vector(key="ArmTypeTranslation"),
  }


def _parse_correction_table(*, path: str) -> List[List[float]]:
  """The (ideal, measured) rows of one encoder correction file, its third column dropped.

  Raises:
    MantisConfigurationError: if a row is not numbers, or the measured column does not ascend.
  """
  rows: List[List[float]] = []
  with open(path, encoding="utf-8-sig") as f:
    for line in f:
      fields = [field for field in line.strip().split(",") if field]
      if not fields:
        continue
      try:
        rows.append([float(fields[0]), float(fields[1])])
      except (IndexError, ValueError) as error:
        raise MantisConfigurationError(f"{path}: not an (ideal, measured) row: {line!r}") from error
  if len(rows) < 2 or any(later[1] <= earlier[1] for earlier, later in zip(rows, rows[1:])):
    raise MantisConfigurationError(f"{path}: two rows or more, the measured column ascending")
  return rows


def _parse_route(*, path: str) -> List[List[Optional[float]]]:
  """A route's waypoints, as a declared configuration holds them.

  Raises:
    MantisConfigurationError: if the file is missing, malformed or moves nothing.
  """
  if not os.path.isfile(path):
    raise MantisConfigurationError(f"a route is missing: {path}")
  route: List[Waypoint] = parse_move_sequence(path=path)
  if all(waypoint == (None, None, None) for waypoint in route):
    raise MantisConfigurationError(f"{path} moves nothing")
  return [list(waypoint) for waypoint in route]


def _load_zones(*, path: str, sequence_directory: str) -> Dict[str, Any]:
  """The ``zones`` block: each zone's rectangle in the arm frame, and its routes to safe XY and
  to idle; the Unknown zone has routes and no rectangle.

  Raises:
    MantisConfigurationError: if a rectangle or a route is missing or malformed, or a route to
      safe XY moves anything but the arms.
  """
  values = _parse_config_entries(path=path)
  rectangles: Dict[str, List[float]] = {}
  for zone in ZONES:
    rectangle = [
      _get_number(
        path=path,
        key=f"{zone}Zone{bound}",
        text=_get_setting(path=path, settings=values, key=f"{zone}Zone{bound}"),
      )
      for bound in ("MinimumX", "MaximumX", "MinimumY", "MaximumY")
    ]
    if not (rectangle[0] < rectangle[1] and rectangle[2] < rectangle[3]):
      raise MantisConfigurationError(
        f"{path}: the {zone} zone's minimums are not below its maximums"
      )
    rectangles[zone] = rectangle
  routes: Dict[str, Dict[str, List[List[Optional[float]]]]] = {}
  for field, key in (
    ("safe_xy_routes", "SafeMoveToSafeXYPositionFrom{zone}ZoneSequenceFile"),
    ("idle_routes", "SafeMoveToIdleFrom{zone}ZoneSequenceFile"),
  ):
    routes[field] = {}
    for zone in (*ZONES, UNKNOWN_ZONE):
      file_name = _get_setting(path=path, settings=values, key=key.format(zone=zone))
      route = _parse_route(path=os.path.join(sequence_directory, file_name))
      if field == "safe_xy_routes" and any(x is None or z is not None for x, _, z in route):
        raise MantisConfigurationError(f"{file_name}: a route to safe XY moves the arms only")
      routes[field][zone] = route
  return {"rectangles": rectangles, **routes}


def _load_stations(*, sequence_directory: str, waste_station_file: str) -> Dict[str, Any]:
  """The ``stations`` block: the routes to the waste station and to each wash station, from idle;
  only those whose file exists.

  Args:
    sequence_directory: the unit's ``System/Sequences/Common``.
    waste_station_file: ``WASTE_STATION_FILE`` on a v4, ``WASTE_STATION_ADAPTER_FILE`` on a v3.

  Raises:
    MantisConfigurationError: if a route is malformed or moves nothing.
  """
  stations: Dict[str, Any] = {}
  path = os.path.join(sequence_directory, waste_station_file)
  if os.path.isfile(path):
    stations["waste"] = _parse_route(path=path)
  wash = {}
  for station in WASH_STATIONS:
    path = os.path.join(sequence_directory, WASH_STATION_FILE.format(station=station))
    if os.path.isfile(path):
      wash[str(station)] = _parse_route(path=path)
  if wash:
    stations["wash"] = wash
  return stations


def _load_chip_sequences(*, chip_directory: str) -> Dict[str, Any]:
  """The ``chip_sequences`` block: each chip type's valve sequences (``VALVE_SEQUENCE_FILES``), as
  (state, duration) pairs, by name; only those whose file exists and holds valve states only.

  A sequence whose file holds another line, such as a move, is left out, as is a chip type with no
  folder.

  Args:
    chip_directory: the install's ``System/Chips``.

  Raises:
    MantisConfigurationError: if a valve state line is malformed or opens a valve it cannot set.
  """
  block: Dict[str, Any] = {}
  for chip_type, factory in MANTIS_CHIP_FACTORIES.items():
    folder = os.path.join(chip_directory, factory(name=chip_type).sequence_folder, "Sequences")
    if not os.path.isdir(folder):
      continue
    files = {file_name.lower(): file_name for file_name in os.listdir(folder)}
    sequences = {}
    for name, stem in VALVE_SEQUENCE_FILES.items():
      file_name = files.get(f"{stem}.seq.txt".lower())
      if file_name is None:
        continue
      states = parse_valve_sequence(path=os.path.join(folder, file_name))
      if states is not None:
        sequences[name] = [list(state) for state in states]
    if sequences:
      block[chip_type] = sequences
  return block


def _load_chip_changer(*, path: str, sequence_directory: str) -> Dict[str, Any]:
  """The ``chip_changer`` block: each position in the chip racks (``CHIP_RACK_TYPE``), by number,
  with its routes (``CHIP_CHANGER_ROUTES``) in the arm frame; an LC3's positions are left out.

  Raises:
    MantisConfigurationError: if a key is missing or not a number, or a position's route is missing
      or malformed.
  """
  values = _parse_config_entries(path=path)

  def get_value(key: str) -> str:
    return _get_setting(path=path, settings=values, key=key)

  positions: Dict[str, Any] = {}
  count = int(
    _get_number(path=path, key="ChipChanger.Count", text=get_value(key="ChipChanger.Count"))
  )
  for position in range(1, count + 1):
    key = f"ChipChanger.{position}.Type"
    if _get_number(path=path, key=key, text=get_value(key=key)) != CHIP_RACK_TYPE:
      continue
    positions[str(position)] = {
      field: _parse_route(
        path=os.path.join(sequence_directory, get_value(key=f"ChipChanger.{position}.{key_name}"))
      )
      for field, key_name in CHIP_CHANGER_ROUTES.items()
    }
  return positions


def _load_unit_configuration(*, oem_config_directory: str) -> Dict[str, Any]:
  """The unit's blocks, as declared configurations hold them, from its vendor ``.config`` files.

  ``plate_clamp``: the lock levels, from ``Device.config`` (``PlateClampInisiatePWM``,
  ``PlateClampTimer``), only for a clamp driven as a DC motor (``PlateClampController``).
  ``rfid``: each input slot's antenna, from every ``RFIDAntennaId.ACC<n>`` of ``RFID.config``, only
  when it exists; with ``Device.config``'s ``RfidMultiplexerDelay`` as written.
  ``led_bar``: the LED count, from ``Device.config``'s ``NumberOfLed``, only where it is set.
  ``arm``: each arm motor's ``Offset`` and ``HomingPositionA`` from ``RobotArm.config`` and its
  encoder correction file from the ``Data`` folder two levels up, only where ``HomingPositionA``
  is set (a v4). Motor 0 is ``Motor.1`` there.
  ``zones``: ``DeviceZone.config``'s rectangles and each zone's routes to safe XY and to idle,
  from the unit's ``System/Sequences/Common``, only where that file exists (a v4).
  ``stations``: the routes from idle to the waste station and to wash stations 1 and 2, from the
  same folder, those that exist; the waste station's ``WASTE_STATION_FILE`` where
  ``DeviceZone.config`` exists (a v4), else ``WASTE_STATION_ADAPTER_FILE`` (a v3).
  ``stage``: ``Device.config``'s ``MicroplateTransformationMatrix``, ``MicroplateOffsetX/Y`` and
  ``ArmType*``, and the plate holder's origin from ``Data/Device/Adapters/SBS Adapter.ad.txt``,
  only where ``Device.config`` sets the matrix.
  ``chip_sequences``: each chip type's valve sequences, from the install's ``System/Chips``, where
  it holds any.
  ``chip_changer``: each chip rack position's routes, from ``ChipChanger.config`` and the install's
  ``System/Sequences/ChipChanger``, only where ``ChipChanger.config`` exists.

  Args:
    oem_config_directory: the unit's ``Data/Device/Configs`` folder.

  Raises:
    MantisConfigurationError: if ``Device.config`` is missing, a block's key is missing or not a
      number, or an arm motor's encoder correction file, a route or the adapter file is missing
      or malformed.
  """
  device_config = os.path.join(oem_config_directory, "Device.config")
  if not os.path.isfile(device_config):
    raise MantisConfigurationError(f"{oem_config_directory} has no Device.config")
  values = _parse_config_entries(path=device_config)

  blocks: Dict[str, Any] = {}
  if values.get("PlateClampController") == "MotorDC":
    pwms = [
      _get_number(path=device_config, key="PlateClampInisiatePWM", text=text)
      for text in _get_setting(
        path=device_config, settings=values, key="PlateClampInisiatePWM"
      ).split(",")
    ]
    timeouts_ms = [
      int(_get_number(path=device_config, key="PlateClampTimer", text=text))
      for text in _get_setting(path=device_config, settings=values, key="PlateClampTimer").split(
        ","
      )
    ]
    if len(pwms) != len(timeouts_ms):
      raise MantisConfigurationError(
        f"{device_config} lists {len(pwms)} clamp PWM duties but {len(timeouts_ms)} timeouts"
      )
    blocks["plate_clamp"] = {
      "levels": {
        str(level): [pwm, timeout_ms]
        for level, (pwm, timeout_ms) in enumerate(zip(pwms, timeouts_ms), start=1)
      }
    }

  if "NumberOfLed" in values:
    blocks["led_bar"] = {
      "number_of_leds": int(
        _get_number(path=device_config, key="NumberOfLed", text=values["NumberOfLed"])
      )
    }

  rfid_config = os.path.join(oem_config_directory, "RFID.config")
  if os.path.isfile(rfid_config):
    rfid_values = _parse_config_entries(path=rfid_config)
    slot_to_antenna = {}
    for key in rfid_values:
      match = re.fullmatch(r"RFIDAntennaId\.ACC(\d+)", key)
      if match is not None:
        slot = int(match.group(1))
        slot_to_antenna[str(slot)] = int(
          _get_number(path=rfid_config, key=key, text=rfid_values[key])
        )
    if not slot_to_antenna:
      raise MantisConfigurationError(f"{rfid_config} has no RFIDAntennaId.ACC<n>")
    blocks["rfid"] = {
      "slot_to_antenna": dict(sorted(slot_to_antenna.items(), key=lambda item: int(item[0]))),
      "multiplexer_delay_raw": _get_number(
        path=device_config,
        key="RfidMultiplexerDelay",
        text=_get_setting(path=device_config, settings=values, key="RfidMultiplexerDelay"),
      ),
    }

  if "MicroplateTransformationMatrix" in values:
    blocks["stage"] = _load_stage(
      path=device_config,
      values=values,
      adapter_path=os.path.join(
        os.path.dirname(os.path.normpath(oem_config_directory)), SBS_ADAPTER_FILE
      ),
    )

  robot_arm_config = os.path.join(oem_config_directory, "RobotArm.config")
  arm_values = (
    _parse_config_entries(path=robot_arm_config) if os.path.isfile(robot_arm_config) else {}
  )
  # The arm motors are 0 and 1 here, Motor.1 and Motor.2 in the file.
  if any(f"Motor.{motor + 1}.HomingPositionA" in arm_values for motor in (0, 1)):
    data_directory = os.path.dirname(os.path.dirname(os.path.normpath(oem_config_directory)))
    correction: Dict[str, List[List[float]]] = {}
    for motor in (0, 1):
      path = os.path.join(
        data_directory, ENCODER_CORRECTION_DIRECTORY, ENCODER_CORRECTION_FILE.format(motor=motor)
      )
      if not os.path.isfile(path):
        raise MantisConfigurationError(f"motor {motor}'s encoder correction is missing: {path}")
      correction[str(motor)] = _parse_correction_table(path=path)
    blocks["arm"] = {
      field: {
        str(motor): _get_number(
          path=robot_arm_config,
          key=f"Motor.{motor + 1}.{key}",
          text=_get_setting(
            path=robot_arm_config, settings=arm_values, key=f"Motor.{motor + 1}.{key}"
          ),
        )
        for motor in (0, 1)
      }
      for field, key in (("offsets", "Offset"), ("homing_position_a", "HomingPositionA"))
    }
    blocks["arm"]["encoder_correction"] = correction

  # The routes, from the unit's sequences: the zones only where DeviceZone.config exists (a v4).
  sequence_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.normpath(oem_config_directory))),
    COMMON_SEQUENCE_DIRECTORY,
  )
  zone_config = os.path.join(oem_config_directory, "DeviceZone.config")
  is_v4 = os.path.isfile(zone_config)
  if is_v4:
    blocks["zones"] = _load_zones(path=zone_config, sequence_directory=sequence_directory)
  stations = _load_stations(
    sequence_directory=sequence_directory,
    waste_station_file=WASTE_STATION_FILE if is_v4 else WASTE_STATION_ADAPTER_FILE,
  )
  if stations:
    blocks["stations"] = stations

  data_directory = os.path.dirname(os.path.dirname(os.path.normpath(oem_config_directory)))
  chip_sequences = _load_chip_sequences(chip_directory=os.path.join(data_directory, CHIP_DIRECTORY))
  if chip_sequences:
    blocks["chip_sequences"] = chip_sequences
  chip_changer_config = os.path.join(oem_config_directory, "ChipChanger.config")
  if os.path.isfile(chip_changer_config):
    blocks["chip_changer"] = _load_chip_changer(
      path=chip_changer_config,
      sequence_directory=os.path.join(data_directory, CHIP_CHANGER_SEQUENCE_DIRECTORY),
    )
  return blocks

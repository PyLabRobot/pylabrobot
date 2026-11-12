"""A Mantis unit's own values, read from the unit's ``.config`` and sequence files.

Only the values set per unit are read: a block is loaded only where its source applies to the unit.
"""

import os
import re
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Optional

from .errors import MantisConfigurationError
from .sequence_files import Waypoint, parse_move_sequence

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

  Args:
    oem_config_directory: the unit's ``Data/Device/Configs`` folder.

  Raises:
    MantisConfigurationError: if ``Device.config`` is missing, a block's key is missing or not a
      number, or an arm motor's encoder correction file or a route is missing or malformed.
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
  return blocks

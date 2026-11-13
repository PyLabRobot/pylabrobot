"""A Mantis unit's own values, read from the ``.config`` files of its vendor install.

Only the values set per unit are read: a block is loaded only where its source applies to the unit.
"""

import os
import re
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List

from .errors import MantisConfigurationError

# Where a v4 install keeps each arm motor's absolute encoder correction, from its ``Data`` folder.
ENCODER_CORRECTION_DIRECTORY = os.path.join("System", "Encoder Correction", "Sampling File")
ENCODER_CORRECTION_FILE = "OneRevolutionSamplingData_Motor{motor}.csv"


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

  Args:
    oem_config_directory: the unit's ``Data/Device/Configs`` folder.

  Raises:
    MantisConfigurationError: if ``Device.config`` is missing, a block's key is missing or not a
      number, or an arm motor's encoder correction file is missing or malformed.
  """
  device_config = os.path.join(oem_config_directory, "Device.config")
  if not os.path.isfile(device_config):
    raise MantisConfigurationError(f"{oem_config_directory} has no Device.config")
  values = _parse_config_entries(path=device_config)

  def number(*, path: str, key: str, text: str) -> float:
    try:
      return float(text)
    except ValueError as error:
      raise MantisConfigurationError(f"{path}: {key} is not a number: {text!r}") from error

  def setting(*, path: str, settings: Dict[str, str], key: str) -> str:
    if key not in settings:
      raise MantisConfigurationError(f"{path} has no {key}")
    return settings[key]

  blocks: Dict[str, Any] = {}
  if values.get("PlateClampController") == "MotorDC":
    pwms = [
      number(path=device_config, key="PlateClampInisiatePWM", text=text)
      for text in setting(path=device_config, settings=values, key="PlateClampInisiatePWM").split(
        ","
      )
    ]
    timeouts_ms = [
      int(number(path=device_config, key="PlateClampTimer", text=text))
      for text in setting(path=device_config, settings=values, key="PlateClampTimer").split(",")
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
        number(path=device_config, key="NumberOfLed", text=values["NumberOfLed"])
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
        slot_to_antenna[str(slot)] = int(number(path=rfid_config, key=key, text=rfid_values[key]))
    if not slot_to_antenna:
      raise MantisConfigurationError(f"{rfid_config} has no RFIDAntennaId.ACC<n>")
    blocks["rfid"] = {
      "slot_to_antenna": dict(sorted(slot_to_antenna.items(), key=lambda item: int(item[0]))),
      "multiplexer_delay_raw": number(
        path=device_config,
        key="RfidMultiplexerDelay",
        text=setting(path=device_config, settings=values, key="RfidMultiplexerDelay"),
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
        str(motor): number(
          path=robot_arm_config,
          key=f"Motor.{motor + 1}.{key}",
          text=setting(path=robot_arm_config, settings=arm_values, key=f"Motor.{motor + 1}.{key}"),
        )
        for motor in (0, 1)
      }
      for field, key in (("offsets", "Offset"), ("homing_position_a", "HomingPositionA"))
    }
    blocks["arm"]["encoder_correction"] = correction
  return blocks

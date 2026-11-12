"""A Mantis unit's own values, read from the ``.config`` files of its vendor install.

Only the values set per unit are read: a block is loaded only where its source applies to the unit.
"""

import os
import re
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict

from .errors import MantisConfigurationError


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


def _load_unit_configuration(*, oem_config_directory: str) -> Dict[str, Any]:
  """The unit's blocks, as declared configurations hold them, from its vendor ``.config`` files.

  ``plate_clamp``: the lock levels, from ``Device.config`` (``PlateClampInisiatePWM``,
  ``PlateClampTimer``), only for a clamp driven as a DC motor (``PlateClampController``).
  ``rfid``: each input slot's antenna, from every ``RFIDAntennaId.ACC<n>`` of ``RFID.config``, only
  when it exists; with ``Device.config``'s ``RfidMultiplexerDelay`` as written.

  Args:
    oem_config_directory: the unit's ``Data/Device/Configs`` folder.

  Raises:
    MantisConfigurationError: if ``Device.config`` is missing, or a block's key is missing or not a
      number.
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
  return blocks

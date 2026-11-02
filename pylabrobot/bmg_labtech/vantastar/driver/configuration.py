"""VANTAstar device configuration, parsed from the HardwareInfoResponse (INIT reply, id 0x13).

The HardwareInfoResponse payload is a sequence of modules, each a module number (u8), the module's
length (u16) and its fields.  Module 2 describes the mainboard and its firmware; the others describe
the fitted hardware (incubator, monochromator, controllers, ...).  The module 2 offsets below hold
for firmware 1.11 and 1.12 (version 1110 and 1120, firmware package 8 and 11).
"""

from dataclasses import dataclass
from typing import Dict, Optional

from .responses import split_reply

HARDWARE_INFO_ID = 0x13

_MAINBOARD_MODULE = 2
# Module 2 field offsets, from the start of the module's fields.
_OFF_FW_VERSION = 0  # u16, e.g. 1120 for firmware 1.12
_OFF_FW_BUILD = 2  # u16
_OFF_PROTOCOL_VERSION = 4  # u8
_OFF_MAINBOARD_VERSION = 5  # u8
_OFF_CPU_BOARD_VERSION = 6  # u8
_OFF_SERIAL_STRING = 12  # null-terminated ASCII; the firmware package number (u16) follows a byte
# after the terminator


@dataclass
class VANTAstarConfiguration:
  """What the reader reported about itself.

  Attributes:
    firmware_version: the firmware version number, e.g. 1120 for firmware 1.12.
    firmware_build: the firmware build number.
    firmware_package: the firmware package number (Voyager shows it as "FWP").
    protocol_version: the communication protocol version the reader supports.
    mainboard_version: the mainboard version.
    cpu_board_version: the CPU board version.
    serial_number: the reader's serial number.
    modules: every module's fields, by module number.
  """

  firmware_version: Optional[int] = None
  firmware_build: Optional[int] = None
  firmware_package: Optional[int] = None
  protocol_version: Optional[int] = None
  mainboard_version: Optional[int] = None
  cpu_board_version: Optional[int] = None
  serial_number: Optional[str] = None
  modules: Optional[Dict[int, bytes]] = None


def get_modules(payload: bytes) -> Dict[int, bytes]:
  """The modules of a HardwareInfoResponse payload, by module number.

  Raises:
    ValueError: if a module runs past the end of the payload.
  """
  modules: Dict[int, bytes] = {}
  i = 0
  while i + 3 <= len(payload):
    number = payload[i]
    length = int.from_bytes(payload[i + 1 : i + 3], "big")
    if i + 3 + length > len(payload):
      raise ValueError(f"module {number} runs past the end of the HardwareInfoResponse")
    modules[number] = payload[i + 3 : i + 3 + length]
    i += 3 + length
  return modules


def parse_hardware_info(reply: bytes) -> VANTAstarConfiguration:
  """Parse a HardwareInfoResponse into the reader's configuration.

  Raises:
    ValueError: if ``reply`` is not a HardwareInfoResponse, is malformed, or has no module 2.
  """
  if not reply or reply[0] != HARDWARE_INFO_ID:
    raise ValueError(f"not a HardwareInfoResponse: {reply[:16].hex()}")
  _, _, payload = split_reply(reply)
  modules = get_modules(payload)
  if _MAINBOARD_MODULE not in modules:
    raise ValueError("HardwareInfoResponse has no mainboard module")
  m = modules[_MAINBOARD_MODULE]
  if len(m) <= _OFF_SERIAL_STRING:
    raise ValueError("HardwareInfoResponse mainboard module too short")
  end = m.find(b"\x00", _OFF_SERIAL_STRING)
  if end == -1:
    end = len(m)
  serial = m[_OFF_SERIAL_STRING:end].decode("ascii", errors="replace").strip()
  package_offset = end + 2
  return VANTAstarConfiguration(
    firmware_version=int.from_bytes(m[_OFF_FW_VERSION : _OFF_FW_VERSION + 2], "big"),
    firmware_build=int.from_bytes(m[_OFF_FW_BUILD : _OFF_FW_BUILD + 2], "big"),
    firmware_package=(
      int.from_bytes(m[package_offset : package_offset + 2], "big")
      if len(m) >= package_offset + 2
      else None
    ),
    protocol_version=m[_OFF_PROTOCOL_VERSION],
    mainboard_version=m[_OFF_MAINBOARD_VERSION],
    cpu_board_version=m[_OFF_CPU_BOARD_VERSION],
    serial_number=serial or None,
    modules=modules,
  )

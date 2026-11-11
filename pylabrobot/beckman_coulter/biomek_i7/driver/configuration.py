"""What the driver knows about an instrument: facts only (STAR design.md P3).

What the boards answer about themselves at setup: each module's identity, its boards with their
application images and versions, and the names of its inputs and outputs (`ModuleConfiguration`,
`BoardInfo`).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pylabrobot.beckman_coulter.biomek_i7.driver.protocol import HardwareVersion, ModuleId


# The firmware this driver has been run against (one instrument, 2026-10-24 through 2026-11-10):
# every motion board and module the same image, and the other boards' application versions
# (chassis 0.2.4, SAGIAN Motion 0.3.14, LLS 0.7.8, syringe pump 5.0.9). The Master Controller
# flashes the boards when it brings other firmware, which would change what the boards answer.
TESTED_FIRMWARE_IMAGES = {(0x6D2DDB78, 266436)}
TESTED_APPLICATION_VERSIONS = {"0.2.4", "0.3.14", "0.7.8", "5.0.9"}


@dataclass
class BoardInfo:
  """One board inside a module, as the module reports it."""

  index: int
  hardware: HardwareVersion
  serial_number: Optional[str] = None
  application_version: Optional[str] = None
  application_crc: Optional[int] = None
  application_length: Optional[int] = None


@dataclass
class ModuleConfiguration:
  """One ACE module (the chassis, or an arm): its identity, its boards, and its inputs."""

  module_id: ModuleId
  usb_serial_number: Optional[str]
  serial_number: str
  hardware: HardwareVersion
  application_crc: int
  application_length: int
  device_counts: Dict[int, int]
  boards: List[BoardInfo] = field(default_factory=list)
  analog_inputs: Dict[int, str] = field(default_factory=dict)
  analog_units: Dict[int, str] = field(default_factory=dict)
  digital_inputs: Dict[int, str] = field(default_factory=dict)
  digital_outputs: Dict[int, str] = field(default_factory=dict)

  def untested_firmware(self) -> List[str]:
    """What this module runs that the driver has not been run against: one line per image or
    version, empty when everything is tested."""
    found = []
    if (self.application_crc, self.application_length) not in TESTED_FIRMWARE_IMAGES:
      found.append(
        f"module image CRC {self.application_crc:#010x}, {self.application_length} bytes"
      )
    for b in self.boards:
      image = (b.application_crc, b.application_length)
      if b.application_crc is not None and image not in TESTED_FIRMWARE_IMAGES:
        found.append(
          f"board {b.index} image CRC {b.application_crc:#010x}, {b.application_length} bytes"
        )
      if (
        b.application_version is not None
        and b.application_version not in TESTED_APPLICATION_VERSIONS
      ):
        found.append(f"board {b.index} application {b.application_version}")
    return found

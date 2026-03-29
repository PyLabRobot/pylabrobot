"""MT-SICS protocol-level chatterbox for device-free testing.

Inherits from MettlerToledoWXS205SDUBackend and overrides send_command to return
mock MT-SICS responses. All high-level methods (zero, tare, read_weight, etc.)
work unchanged because they call send_command which is intercepted here.

This follows the same pattern as STARChatterboxBackend (inherits from STARBackend
and overrides the low-level command transmission).
"""

import logging
from typing import List, Optional, Set

from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.scales.mettler_toledo.backend import (
  MettlerToledoResponse,
  MettlerToledoWXS205SDUBackend,
)
from pylabrobot.scales.scale_backend import ScaleBackend

logger = logging.getLogger("pylabrobot")


class MettlerToledoChatterboxBackend(MettlerToledoWXS205SDUBackend):
  """MT-SICS protocol simulator for testing without hardware.

  Inherits all MT-SICS methods from MettlerToledoWXS205SDUBackend.
  Overrides send_command to return mock MT-SICS responses.

  Set ``platform_weight`` and ``sample_weight`` to simulate placing items
  on the scale. The total sensor reading is ``platform_weight + sample_weight``.

  Example::

    backend = MettlerToledoChatterboxBackend()
    scale = Scale(name="scale", backend=backend, size_x=0, size_y=0, size_z=0)
    await scale.setup()
    # backend.device_type == "WXS205SDU", backend.capacity == 220.0

    backend.platform_weight = 50.0       # place 50g container
    await scale.tare()
    backend.sample_weight = 10.0         # add 10g
    weight = await scale.read_weight()   # returns 10.0
  """

  def __init__(
    self,
    device_type: str = "WXS205SDU",
    serial_number: str = "SIM0000001",
    capacity: float = 220.0,
    mt_sics_levels: Optional[Set[int]] = None,
  ) -> None:
    # Skip MettlerToledoWXS205SDUBackend.__init__ (which creates a Serial object)
    ScaleBackend.__init__(self)

    # Physics state
    self.platform_weight: float = 0.0
    self.sample_weight: float = 0.0
    self.zero_offset: float = 0.0
    self.tare_weight: float = 0.0

    # Simulated device identity
    self._simulated_device_type = device_type
    self._simulated_serial_number = serial_number
    self._simulated_capacity = capacity
    self._simulated_mt_sics_levels = mt_sics_levels or {0, 1, 2, 3}

  @property
  def _sensor_reading(self) -> float:
    return self.platform_weight + self.sample_weight

  async def setup(self) -> None:
    self.serial_number = self._simulated_serial_number
    self._mt_sics_levels = self._simulated_mt_sics_levels
    self.device_type = self._simulated_device_type
    self.capacity = self._simulated_capacity
    logger.info(
      "[MT Scale] Connected to Mettler Toledo scale (simulation)\n"
      "Device type: %s\n"
      "Serial number: %s\n"
      "Capacity: %.1f g\n"
      "MT-SICS levels: %s",
      self.device_type,
      self.serial_number,
      self.capacity,
      sorted(self._mt_sics_levels),
    )

  async def stop(self) -> None:
    pass

  async def cancel(self) -> str:
    responses = await self.send_command("@")
    self._validate_response(responses[0], 3, "@")
    return responses[0].data[0].replace('"', "")

  async def send_command(self, command: str, timeout: int = 60) -> List[MettlerToledoResponse]:
    logger.log(LOG_LEVEL_IO, "[MT Scale] Sent command: %s", command)
    responses = self._build_response(command)
    for resp in responses:
      logger.log(
        LOG_LEVEL_IO,
        "[MT Scale] Received response: %s %s %s",
        resp.command,
        resp.status,
        " ".join(resp.data),
      )
      self._parse_basic_errors(resp)
    return responses

  def _build_response(self, command: str) -> List[MettlerToledoResponse]:
    R = MettlerToledoResponse
    cmd = command.split()[0]
    net = round(self._sensor_reading - self.zero_offset - self.tare_weight, 5)

    # Identification
    if cmd == "@":
      return [R("I4", "A", [f'"{self._simulated_serial_number}"'])]
    if cmd == "I1":
      levels = "".join(str(lvl) for lvl in sorted(self._simulated_mt_sics_levels))
      return [R("I1", "A", [f'"{levels}"'])]
    if cmd == "I2":
      return [
        R("I2", "A", [f'"{self._simulated_device_type}"', f"{self._simulated_capacity:.4f}", "g"])
      ]
    if cmd == "I4":
      return [R("I4", "A", [f'"{self._simulated_serial_number}"'])]

    # Zero
    if cmd in ("Z", "ZI", "ZC"):
      self.zero_offset = self._sensor_reading
      return [R(cmd, "A")]

    # Tare
    if cmd in ("T", "TI", "TC"):
      self.tare_weight = self._sensor_reading - self.zero_offset
      return [R(cmd, "S", [f"{self.tare_weight:.5f}", "g"])]
    if cmd == "TA":
      return [R("TA", "A", [f"{self.tare_weight:.5f}", "g"])]
    if cmd == "TAC":
      self.tare_weight = 0.0
      return [R("TAC", "A")]

    # Weight reading
    if cmd in ("S", "SI", "SC"):
      return [R(cmd, "S", [f"{net:.5f}", "g"])]

    # Cancel
    if cmd == "C":
      return [R("C", "B"), R("C", "A")]

    # Display
    if cmd in ("D", "DW"):
      return [R(cmd, "A")]

    # Configuration
    if cmd == "M21":
      return [R("M21", "A")]

    # Temperature
    if cmd == "M28":
      return [R("M28", "A", ["1", "22.5"])]

    # Remaining weighing range
    if cmd == "I50":
      remaining = self._simulated_capacity - self._sensor_reading
      return [
        R("I50", "B", ["0", f"{remaining:.3f}", "g"]),
        R("I50", "B", ["1", "0.000", "g"]),
        R("I50", "A", ["2", f"{remaining:.3f}", "g"]),
      ]

    # Unknown command
    return [R("ES", "")]

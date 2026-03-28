"""MT-SICS protocol-level chatterbox for device-free testing.

Unlike the generic ScaleChatterboxBackend (which simulates scale physics),
this chatterbox inherits from MettlerToledoWXS205SDUBackend and overrides
send_command to return mock MT-SICS responses. All high-level methods
(zero, tare, read_weight, request_capacity, etc.) work unchanged because
they call send_command which is intercepted here.

This follows the same pattern as STARChatterboxBackend, which inherits from
STARBackend and overrides _write_and_read_command.
"""

import logging
from typing import List, Optional, Set

from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.scales.mettler_toledo.backend import MettlerToledoWXS205SDUBackend, MettlerToledoResponse
from pylabrobot.scales.scale_backend import ScaleBackend

logger = logging.getLogger("pylabrobot")


class MettlerToledoChatterboxBackend(MettlerToledoWXS205SDUBackend):
  """MT-SICS protocol simulator for testing without hardware.

  Inherits all high-level methods from MettlerToledoWXS205SDUBackend.
  Overrides send_command to return mock MT-SICS responses, so response
  parsing and validation code is exercised during tests.

  Set ``_mock_weight`` to simulate the total load on the weighing platform.

  Example::

    backend = MettlerToledoChatterboxBackend()
    scale = Scale(name="scale", backend=backend, size_x=0, size_y=0, size_z=0)
    await scale.setup()
    # backend.device_type == "WXS205SDU", backend.capacity == 220.0

    backend._mock_weight = 50.0       # place 50g container
    await scale.tare()
    backend._mock_weight = 60.0       # add 10g
    weight = await scale.read_weight()  # returns 10.0
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
    self._mock_weight: float = 0.0
    self._mock_tare: float = 0.0
    self._mock_zero_offset: float = 0.0
    self._mock_device_type = device_type
    self._mock_serial_number = serial_number
    self._mock_capacity = capacity
    self._mock_mt_sics_levels = mt_sics_levels or {0, 1, 2, 3}

  async def setup(self) -> None:
    self.serial_number = self._mock_serial_number
    self._mt_sics_levels = self._mock_mt_sics_levels
    self.device_type = self._mock_device_type
    self.capacity = self._mock_capacity
    logger.info(
      "[scale] Connected (simulation): %s (S/N: %s, capacity: %.1f g, MT-SICS levels: %s)",
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
    return responses[0][2].replace('"', "")

  async def send_command(
    self, command: str, timeout: int = 60
  ) -> List[MettlerToledoResponse]:
    logger.log(LOG_LEVEL_IO, "[scale] Sent command: %s", command)
    cmd = command.split()[0]
    net = self._mock_weight - self._mock_zero_offset - self._mock_tare

    if cmd == "@":
      return [["I4", "A", f'"{self._mock_serial_number}"']]
    if cmd == "I1":
      levels = "".join(str(l) for l in sorted(self._mock_mt_sics_levels))
      return [["I1", "A", f'"{levels}"']]
    if cmd == "I2":
      return [["I2", "A", f'"{self._mock_device_type}"', f"{self._mock_capacity:.4f}", "g"]]
    if cmd == "I4":
      return [["I4", "A", f'"{self._mock_serial_number}"']]

    # Zero
    if cmd == "Z":
      self._mock_zero_offset = self._mock_weight
      return [["Z", "A"]]
    if cmd == "ZI":
      self._mock_zero_offset = self._mock_weight
      return [["ZI", "A"]]
    if cmd == "ZC":
      self._mock_zero_offset = self._mock_weight
      return [["ZC", "A"]]

    # Tare
    if cmd == "T":
      self._mock_tare = self._mock_weight - self._mock_zero_offset
      return [["T", "S", f"{self._mock_tare:.5f}", "g"]]
    if cmd == "TI":
      self._mock_tare = self._mock_weight - self._mock_zero_offset
      return [["TI", "S", f"{self._mock_tare:.5f}", "g"]]
    if cmd == "TC":
      self._mock_tare = self._mock_weight - self._mock_zero_offset
      return [["TC", "S", f"{self._mock_tare:.5f}", "g"]]
    if cmd == "TA":
      return [["TA", "A", f"{self._mock_tare:.5f}", "g"]]
    if cmd == "TAC":
      self._mock_tare = 0.0
      return [["TAC", "A"]]

    # Weight reading
    if cmd == "S":
      return [["S", "S", f"{net:.5f}", "g"]]
    if cmd == "SI":
      return [["SI", "S", f"{net:.5f}", "g"]]
    if cmd == "SC":
      return [["SC", "S", f"{net:.5f}", "g"]]

    # Cancel
    if cmd == "C":
      return [["C", "B"], ["C", "A"]]

    # Display
    if cmd == "D":
      return [["D", "A"]]
    if cmd == "DW":
      return [["DW", "A"]]

    # Configuration
    if cmd == "M21":
      return [["M21", "A"]]

    # Remaining weighing range
    if cmd == "I50":
      remaining = self._mock_capacity - self._mock_weight
      return [
        ["I50", "B", "0", f"{remaining:.3f}", "g"],
        ["I50", "B", "1", "0.000", "g"],
        ["I50", "A", "2", f"{remaining:.3f}", "g"],
      ]

    # Unknown command
    return [["ES"]]

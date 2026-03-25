import logging
import time
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

try:
  import serial  # type: ignore

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.capabilities.peeling import PeelerBackend, PeelingCapability
from pylabrobot.device import Device
from pylabrobot.io.serial import Serial
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.serializer import SerializableMixin


class XPeelBackend(PeelerBackend):
  """Backend for the Azenta XPeel automated plate seal remover (RS-232)."""

  BAUDRATE = 9600
  RESPONSE_TIMEOUT = 20.0

  @dataclass(frozen=True)
  class ErrorInfo:
    code: int
    description: str

  _ERROR_DEFINITIONS = {
    0: ErrorInfo(0, "No error"),
    1: ErrorInfo(1, "Conveyor motor stalled"),
    2: ErrorInfo(2, "Elevator motor stalled"),
    3: ErrorInfo(3, "Take up spool stalled"),
    4: ErrorInfo(4, "Seal not removed"),
    5: ErrorInfo(5, "Illegal command"),
    6: ErrorInfo(6, "No plate found (only when plate check is enabled)"),
    7: ErrorInfo(7, "Out of tape or tape broke"),
    8: ErrorInfo(8, "Parameters not saved"),
    9: ErrorInfo(9, "Stop button pressed while running"),
    10: ErrorInfo(10, "Seal sensor unplugged or broke"),
    20: ErrorInfo(20, "Less than 30 seals left on supply roll"),
    21: ErrorInfo(21, "Room for less than 30 seals on take-up spool"),
    51: ErrorInfo(51, "Emergency stop: cover open or hardware problem"),
    52: ErrorInfo(52, "Circuitry fault detected: remove power"),
  }

  def __init__(self, port: str, timeout: Optional[float] = None):
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    self.logger = logging.getLogger(__name__)
    self.port = port
    self.response_timeout = timeout if timeout is not None else self.RESPONSE_TIMEOUT

    self.io = Serial(
      human_readable_device_name="XPeel",
      port=self.port,
      baudrate=self.BAUDRATE,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      timeout=self.response_timeout,
      write_timeout=self.response_timeout,
      rtscts=False,
    )

  async def setup(self):
    await self.io.setup()

  async def stop(self):
    await self.io.stop()

  @classmethod
  def describe_error(cls, code: int) -> str:
    info = cls._ERROR_DEFINITIONS.get(code)
    if info:
      return info.description
    return f"Unknown error code {code}"

  @classmethod
  def parse_ready_line(cls, line: str):
    if not line.startswith("*ready:"):
      return None
    try:
      parts = line.split(":")[1].split(",")
      code = int(parts[0])
      return code, cls.describe_error(code)
    except Exception:
      return None

  async def _send_command(
    self, cmd, expect_ack=False, wait_for_ready=False, clear_buffer=True
  ) -> List[str]:
    full_cmd = cmd if cmd.endswith("\r\n") else f"{cmd}\r\n"

    self.logger.debug("Sending command: %s", full_cmd.strip())
    if clear_buffer:
      await self.io.reset_input_buffer()
    await self.io.write(full_cmd.encode("ascii"))

    responses: List[str] = []
    start = time.time()
    while time.time() - start < self.response_timeout:
      raw = await self.io.readline()
      line = raw.decode("ascii", errors="ignore").strip()
      if not line:
        continue

      display_line = line
      if line.startswith("*ready:"):
        parsed = self.parse_ready_line(line)
        if parsed:
          code, desc = parsed
          display_line = f"{line} [{desc}]"

      responses.append(display_line)
      self.logger.info("Received: %s", display_line)

      if line.startswith("*ack"):
        if not wait_for_ready:
          break
        continue

      if wait_for_ready and line.startswith("*ready"):
        break

      if not wait_for_ready and not expect_ack:
        break

    if time.time() - start >= self.response_timeout:
      self.logger.warning(
        "Timed out waiting for response to %s after %.2fs",
        full_cmd.strip(),
        self.response_timeout,
      )

    return responses

  async def get_status(self) -> Tuple[int, int, int]:
    """Request instrument status; returns three error codes."""
    resp = await self._send_command("*stat")
    return tuple([int(x) for x in resp[-1].split(":")[1].split(",")])  # type: ignore

  async def get_version(self):
    """Request firmware version."""
    return await self._send_command("*version")

  async def reset(self):
    """Request reset."""
    return await self._send_command("*reset", expect_ack=True, wait_for_ready=True)

  async def restart(self, backend_params: Optional[SerializableMixin] = None):
    """Request restart with full homing sequence."""
    return await self._send_command("*restart", expect_ack=True, wait_for_ready=True)

  @dataclass
  class PeelParams(BackendParams):
    begin_location: Literal[-2, 0, 2, 4] = 0
    fast: bool = False
    adhere_time: float = 2.5

  async def peel(
    self,
    backend_params: Optional[SerializableMixin] = None,
  ):
    """Run an automated de-seal cycle."""
    if not isinstance(backend_params, self.PeelParams):
      backend_params = XPeelBackend.PeelParams()

    adhere_time = backend_params.adhere_time
    begin_location = backend_params.begin_location
    fast = backend_params.fast

    if adhere_time not in {2.5, 5.0, 7.5, 10.0}:
      raise ValueError("adhere_time must be one of: 2.5, 5.0, 7.5, 10.0")
    if begin_location not in {-2, 0, 2, 4}:
      raise ValueError("begin_location must be one of: -2, 0, 2, 4")

    parameter_set = {
      (-2, True): 1,
      (-2, False): 2,
      (0, True): 3,
      (0, False): 4,
      (2, True): 5,
      (2, False): 6,
      (4, True): 7,
      (4, False): 8,
    }.get((begin_location, fast), 9)

    cmd = f"*xpeel:{parameter_set}{adhere_time}"
    return await self._send_command(cmd, expect_ack=True, wait_for_ready=True)

  async def seal_check(self) -> Literal["seal_detected", "no_seal", "plate_not_detected"]:
    """Check for seal presence."""
    resp = await self._send_command("*sealcheck", expect_ack=True, wait_for_ready=True)
    ready_line = resp[-1]
    parsed = self.parse_ready_line(ready_line)
    if parsed is None:
      raise RuntimeError(f"Could not parse ready line: {ready_line}")
    code, _ = parsed
    if code == 0:
      return "no_seal"
    if code == 4:
      return "seal_detected"
    if code == 6:
      return "plate_not_detected"
    raise RuntimeError(
      f"Unexpected seal check code: {code}, interpreted as: {self.describe_error(code)}"
    )

  async def get_tape_remaining(self):
    """Query remaining tape. Returns (supply_remaining, takeup_remaining) in number of deseals."""
    resp = await self._send_command("*tapeleft", expect_ack=True, wait_for_ready=True)
    tape_line = resp[-1]
    parts = tape_line.split(":")[1].split(",")
    supply_remaining = int(parts[0]) * 10
    takeup_remaining = int(parts[1]) * 10
    return supply_remaining, takeup_remaining

  async def enable_plate_check(self, enabled=True):
    """Enable or disable plate presence check."""
    flag = "y" if enabled else "n"
    return await self._send_command(f"*platecheck:{flag}", expect_ack=True, wait_for_ready=True)

  async def get_seal_sensor_status(self):
    """Get seal sensor threshold value (0-999)."""
    return await self._send_command("*sealstat", expect_ack=True, wait_for_ready=True)

  async def set_seal_threshold_upper(self, value: int):
    """Set the upper seal detected threshold (0-999)."""
    if not 0 <= value <= 999:
      raise ValueError("value must be between 0 and 999")
    return await self._send_command(
      f"*sealhigher:{value:03d}", expect_ack=True, wait_for_ready=True
    )

  async def set_seal_threshold_lower(self, value: int):
    """Set the lower seal detected threshold (0-999)."""
    if not 0 <= value <= 999:
      raise ValueError("value must be between 0 and 999")
    return await self._send_command(f"*seallower:{value:03d}", expect_ack=True, wait_for_ready=True)

  async def move_conveyor_out(self):
    """Move conveyor out."""
    return await self._send_command("*moveout", expect_ack=True, wait_for_ready=True)

  async def move_conveyor_in(self):
    """Move conveyor in."""
    return await self._send_command("*movein", expect_ack=True, wait_for_ready=True)

  async def move_elevator_down(self):
    """Move elevator down."""
    return await self._send_command("*movedown", expect_ack=True, wait_for_ready=True)

  async def move_elevator_up(self):
    """Move elevator up."""
    return await self._send_command("*moveup", expect_ack=True, wait_for_ready=True)

  async def advance_tape(self):
    """Advance tape / move spool."""
    return await self._send_command("*movespool", expect_ack=True, wait_for_ready=True)


class XPeel(Device):
  """Azenta XPeel automated plate seal remover."""

  def __init__(self, name: str, port: str, timeout: Optional[float] = None):
    backend = XPeelBackend(port=port, timeout=timeout)
    super().__init__(backend=backend)
    self._backend: XPeelBackend = backend
    self.peeler = PeelingCapability(backend=backend)
    self._capabilities = [self.peeler]

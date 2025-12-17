import logging
import time
from dataclasses import dataclass
from typing import List, Literal, Optional

import serial

from pylabrobot.io.serial import Serial
from pylabrobot.peeling.backend import PeelerBackend


class XPeelBackend(PeelerBackend):
  """
  Client for the Azenta Life Sciences Automated Plate Seal Remover (XPeel)
  RS-232 interface. All commands use lowercase ASCII, begin with '*' and end
  with <CR><LF>.
  """

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

  def __init__(self, port: str, logger=None, timeout=None):
    self.logger = logger or logging.getLogger(__name__)
    self.port = port
    self.response_timeout = timeout if timeout is not None else self.RESPONSE_TIMEOUT

    self._serial_timeout = timeout if timeout is not None else self.response_timeout
    self.io: Optional[Serial] = Serial(
      port=self.port,
      baudrate=self.BAUDRATE,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      timeout=self._serial_timeout,
      write_timeout=self._serial_timeout,
      rtscts=False,
    )

  async def setup(self):
    if self.io is None:
      raise RuntimeError("Serial interface not initialized.")
    await self.io.setup()

  async def stop(self):
    if self.io is None:
      return
    await self.io.stop()
    self.logger.info("Serial interface closed.")

  @classmethod
  def describe_error(cls, code: int) -> str:
    """
    Translate an XPeel error/status code to a human-readable message.
    """
    info = cls._ERROR_DEFINITIONS.get(code)
    if info:
      return info.description
    return f"Unknown error code {code}"

  @classmethod
  def parse_ready_line(cls, line: str):
    """
    Parse a ready line like '*ready:06,01,00' to extract the primary error code
    and its description. Returns a tuple (code: int, description: str).
    """
    if not line.startswith("*ready:"):
      return None
    try:
      # Expected format: *ready:CC,PP,TT (CC = error/condition code)
      parts = line.split(":")[1].split(",")
      code = int(parts[0])
      return code, cls.describe_error(code)
    except Exception:
      return None

  async def _send_command(
    self, cmd, expect_ack=False, wait_for_ready=False, clear_buffer=True
  ) -> List[str]:
    """
    Send a command and collect responses until *ready (optional) or timeout.

    Returns a list of response lines (strings).
    """
    full_cmd = cmd if cmd.endswith("\r\n") else f"{cmd}\r\n"

    if self.io is None:
      raise RuntimeError("Serial interface not initialized; call setup() first.")

    self.logger.debug(f"Sending command: {full_cmd.strip()}")
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
      self.logger.info(f"Received: {display_line}")
      print(f"Received: {display_line}")

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

  async def get_status(self):
    """Request instrument status; returns *ready:XX,XX,XX."""
    self.logger.debug("Requesting status...")
    return await self._send_command("*stat")

  async def get_version(self):
    """Request firmware version."""
    self.logger.debug("Requesting firmware version...")
    return await self._send_command("*version")

  async def reset(self):
    """Request reset; instrument replies with ack then ready."""
    self.logger.debug("Requesting reset...")
    return await self._send_command("*reset", expect_ack=True, wait_for_ready=True)

  async def restart(self):
    """Request restart; instrument replies with ack then poweron/homing/ready."""
    self.logger.debug("Requesting restart...")
    return await self._send_command("*restart", expect_ack=True, wait_for_ready=True)

  async def peel(
    self,
    begin_location: Literal[-2, 0, 2, 4] = 0,
    fast: bool = False,
    adhere_time: float = 2.5,
  ):
    """Run an automated de-seal cycle.

    Args:
      begin_location: Begin peel location in mm relative to default (0). Must be one of: -2, 0, 2, 4.
      fast: Use fast speed if True, slow if False.
      adhere_time: Adhere time (seconds). Must be one of: 2.5, 5.0, 7.5, 10.0.
    """

    self.logger.debug(
      f"Running peel with begin_location={begin_location}, fast={fast}, adhere_time={adhere_time}..."
    )

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

    if parameter_set not in range(1, 10):
      raise ValueError("parameter_set must be in 1-9")
    cmd = f"*xpeel:{parameter_set}{adhere_time}"
    return await self._send_command(
      cmd,
      expect_ack=True,
      wait_for_ready=True,
    )

  async def seal_check(self):
    """Check for seal presence; ready response encodes result."""
    self.logger.debug("Checking for seal presence...")
    return await self._send_command("*sealcheck", expect_ack=True, wait_for_ready=True)

  async def get_tape_remaining(self):
    """Query remaining tape."""
    self.logger.debug("Querying remaining tape...")
    return await self._send_command("*tapeleft", expect_ack=True, wait_for_ready=True)

  async def enable_plate_check(self, enabled=True):
    """Enable or disable plate presence check."""
    self.logger.debug(f"{'Enabling' if enabled else 'Disabling'} plate presence check...")
    flag = "y" if enabled else "n"
    return await self._send_command(
      f"*platecheck:{flag}",
      expect_ack=True,
      wait_for_ready=True,
    )

  async def get_seal_sensor_status(self):
    """Get seal sensor threshold status."""
    self.logger.debug("Getting seal sensor threshold status...")
    return await self._send_command("*sealstat", expect_ack=True, wait_for_ready=True)

  async def set_seal_threshold_upper(self, value: int):
    """Set upper seal sensor threshold (0-999)."""
    self.logger.debug(f"Setting upper seal sensor threshold to {value}...")
    if not 0 <= value <= 999:
      raise ValueError("value must be between 0 and 999")
    return await self._send_command(
      f"*sealhigher:{value:03d}",
      expect_ack=True,
      wait_for_ready=True,
    )

  async def set_seal_threshold_lower(self, value: int):
    """Set lower seal sensor threshold (0-999)."""
    self.logger.debug(f"Setting lower seal sensor threshold to {value}...")
    if not 0 <= value <= 999:
      raise ValueError("value must be between 0 and 999")
    return await self._send_command(
      f"*seallower:{value:03d}",
      expect_ack=True,
      wait_for_ready=True,
    )

  async def move_conveyor_out(self):
    """Move conveyor out; ack then ready expected."""
    self.logger.debug("Moving conveyor out...")
    return await self._send_command(
      "*moveout",
      expect_ack=True,
      wait_for_ready=True,
    )

  async def move_conveyor_in(self):
    """Move conveyor in; ack then ready expected."""
    self.logger.debug("Moving conveyor in...")
    return await self._send_command(
      "*movein",
      expect_ack=True,
      wait_for_ready=True,
    )

  async def move_elevator_down(self):
    """Move elevator down; ack then ready expected."""
    self.logger.debug("Moving elevator down...")
    return await self._send_command(
      "*movedown",
      expect_ack=True,
      wait_for_ready=True,
    )

  async def move_elevator_up(self):
    """Move elevator up; ack then ready expected."""
    self.logger.debug("Moving elevator up...")
    return await self._send_command(
      "*moveup",
      expect_ack=True,
      wait_for_ready=True,
    )

  async def advance_tape(self):
    """Advance tape / move spool; ack then ready expected."""
    self.logger.debug("Advancing tape/spool...")
    return await self._send_command(
      "*movespool",
      expect_ack=True,
      wait_for_ready=True,
    )

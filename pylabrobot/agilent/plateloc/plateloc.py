from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from typing import Literal, Optional, cast

from pylabrobot.io.serial import Serial

try:
  import serial as _serial  # noqa: F401

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

logger = logging.getLogger(__name__)


_ACK_RE = re.compile(r"^\s*(?P<code>[A-Z0-9]{2})(?P<status>[AN])K(?:\((?P<message>.*)\))?\s*$")


class PlateLocError(RuntimeError):
  """Raised when PlateLoc communication or protocol handling fails."""


@dataclasses.dataclass(frozen=True)
class PlateLocStatus:
  """Best-known PlateLoc state from direct serial control."""

  port: str
  connected: bool
  target_temperature: Optional[float]
  stage_position: Optional[Literal["open", "closed"]]
  cycle_complete: Optional[bool]


@dataclasses.dataclass(frozen=True)
class PlateLocSerialProfile:
  """Serial settings for a PlateLoc controller.

  The decoded low-level protocol uses carriage-return-terminated ASCII frames.
  Setpoint payloads are encoded as a decimal fraction whose fractional digits hold the integer
  setpoint, for example ``ST 0.175`` for 175 C and ``SS 0.12`` for 1.2 s.
  """

  baudrate: int = 19200
  bytesize: int = 8
  parity: str = "N"
  stopbits: int = 1
  timeout: float = 1
  write_timeout: float = 1
  rtscts: bool = False
  dsrdtr: bool = False
  xonxoff: bool = False
  read_delay: float = 0.05
  ack_timeout: float = 10
  response_timeout: float = 2
  stage_move_delay: float = 6
  cycle_poll_interval: float = 0.5
  command_terminator: str = "\r"
  response_terminator: bytes = b"\r"

  def serialize(self) -> dict:
    """Serialize this profile to JSON-compatible values."""
    return {
      "baudrate": self.baudrate,
      "bytesize": self.bytesize,
      "parity": self.parity,
      "stopbits": self.stopbits,
      "timeout": self.timeout,
      "write_timeout": self.write_timeout,
      "rtscts": self.rtscts,
      "dsrdtr": self.dsrdtr,
      "xonxoff": self.xonxoff,
      "read_delay": self.read_delay,
      "ack_timeout": self.ack_timeout,
      "response_timeout": self.response_timeout,
      "stage_move_delay": self.stage_move_delay,
      "cycle_poll_interval": self.cycle_poll_interval,
      "command_terminator": self.command_terminator,
      "response_terminator": self.response_terminator.decode("latin1"),
    }

  @classmethod
  def deserialize(cls, data: dict) -> "PlateLocSerialProfile":
    """Deserialize a profile produced by :meth:`serialize`."""
    data = data.copy()
    if "response_terminator" in data:
      data["response_terminator"] = data["response_terminator"].encode("latin1")
    return cls(**data)


class PlateLoc:
  """Direct serial driver for the Agilent PlateLoc thermal microplate sealer."""

  def __init__(
    self,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    profile: Optional[PlateLocSerialProfile | dict] = None,
    timeout: float = 30,
  ) -> None:
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    if isinstance(profile, dict):
      profile = PlateLocSerialProfile.deserialize(profile)
    self.profile = profile or PlateLocSerialProfile()
    self.timeout = timeout
    self._connected = False
    self._target_temperature: Optional[float] = None
    self._stage_position: Optional[Literal["open", "closed"]] = None
    self.io = Serial(
      human_readable_device_name="Agilent PlateLoc Sealer",
      port=port,
      vid=vid,
      pid=pid,
      baudrate=self.profile.baudrate,
      bytesize=self.profile.bytesize,
      parity=self.profile.parity,
      stopbits=self.profile.stopbits,
      write_timeout=self.profile.write_timeout,
      timeout=self.profile.timeout,
      rtscts=self.profile.rtscts,
      dsrdtr=self.profile.dsrdtr,
      xonxoff=self.profile.xonxoff,
    )

  @property
  def port(self) -> str:
    """The configured or detected serial port."""
    return cast(str, self.io.port)

  @property
  def connected(self) -> bool:
    """Whether :meth:`setup` has completed without a subsequent :meth:`stop`."""
    return self._connected

  async def setup(self) -> None:
    """Open the serial connection."""
    await self.io.setup()
    self._connected = True
    logger.info("[PlateLoc %s] connected", self.port)

  async def stop(self) -> None:
    """Close the serial connection."""
    await self.io.stop()
    self._connected = False
    logger.info("[PlateLoc %s] disconnected", self.port)

  async def _send_command(
    self,
    command: str,
    *,
    timeout: Optional[float] = None,
    required: bool = True,
  ) -> Optional[str]:
    """Send one literal PlateLoc serial frame and return the raw response."""
    command = command.removesuffix(self.profile.command_terminator)
    await self.io.reset_input_buffer()
    await self.io.write(f"{command}{self.profile.command_terminator}".encode("ascii"))

    if self.profile.read_delay > 0:
      await asyncio.sleep(self.profile.read_delay)

    return await self._read_response(timeout=timeout, required=required)

  async def _read_response(
    self,
    timeout: Optional[float] = None,
    required: bool = True,
  ) -> Optional[str]:
    """Read one carriage-return-terminated response."""
    deadline = time.monotonic() + (
      timeout if timeout is not None else self.profile.response_timeout
    )
    chunks = bytearray()
    while time.monotonic() < deadline:
      remaining = deadline - time.monotonic()
      with self.io.temporary_timeout(max(0.01, min(0.1, remaining))):
        chunk = await self.io.read(1)
      if chunk:
        chunks.extend(chunk)
        if chunks.endswith(self.profile.response_terminator):
          break
      elif len(chunks) > 0:
        break

    if len(chunks) == 0:
      if required:
        raise TimeoutError("Timeout while waiting for PlateLoc response")
      return None
    return bytes(chunks).decode("utf-8", errors="replace").strip()

  def _parse_response(self, command_code: str, response: str) -> re.Match[str]:
    match = _ACK_RE.match(response)
    if match is None:
      raise PlateLocError(f"PlateLoc returned invalid response to {command_code!r}: {response!r}")
    code = match.group("code")
    if code != command_code:
      raise PlateLocError(f"PlateLoc replied with {code!r} to {command_code!r}: {response!r}")
    return match

  def _raise_for_error(self, command_code: str, response: str) -> None:
    match = self._parse_response(command_code, response)
    if match.group("status") == "N":
      message = match.group("message") or "command rejected"
      raise PlateLocError(f"PlateLoc rejected {command_code!r}: {message}")

  async def _send(
    self,
    command: str,
    *,
    timeout: Optional[float] = None,
    raise_on_nak: bool = True,
  ) -> str:
    response = await self._send_command(
      command,
      timeout=timeout if timeout is not None else self.profile.ack_timeout,
      required=True,
    )
    assert response is not None
    if raise_on_nak:
      self._raise_for_error(command[:2], response)
    return response

  async def set_sealing_temperature(self, temperature: float) -> str:
    """Set the sealing target temperature in degrees C."""
    if not (20 <= temperature <= 235):
      raise ValueError("Temperature out of range. Please enter a value between 20 and 235 C.")
    target_temperature = round(temperature)
    logger.info("[PlateLoc %s] setting sealing temperature to %.1f C", self.port, temperature)
    response = await self._send(f"ST 0.{target_temperature:03d}")
    self._target_temperature = float(target_temperature)
    return response

  async def stop_cycle(self) -> str:
    """Stop the active sealing cycle."""
    logger.info("[PlateLoc %s] stopping sealing cycle", self.port)
    return await self._send("AC 00")

  async def _apply_seal(self) -> str:
    """Apply the current seal."""
    logger.info("[PlateLoc %s] applying seal", self.port)
    return await self._send("AS 00")

  async def clear_error(self) -> str:
    """Clear the active PlateLoc error."""
    logger.info("[PlateLoc %s] clearing error", self.port)
    return await self._send("CL 00")

  async def request_cycle_complete(self) -> bool:
    """Return whether the current sealing cycle is complete."""
    response = await self._send(
      "CC 00",
      timeout=self.profile.response_timeout,
      raise_on_nak=False,
    )
    match = self._parse_response("CC", response)
    return match.group("status") == "A"

  def status_snapshot(self, cycle_complete: Optional[bool] = None) -> PlateLocStatus:
    """Return the locally tracked state without communicating with the device."""
    return PlateLocStatus(
      port=self.port,
      connected=self.connected,
      target_temperature=self._target_temperature,
      stage_position=self._stage_position,
      cycle_complete=cycle_complete,
    )

  async def request_status(self, query_cycle_complete: bool = True) -> PlateLocStatus:
    """Return locally tracked state, optionally querying cycle completion."""
    cycle_complete = await self.request_cycle_complete() if query_cycle_complete else None
    return self.status_snapshot(cycle_complete=cycle_complete)

  async def seal(self, temperature: int, duration: float) -> str:
    """Seal a plate at the requested temperature and duration."""
    if not (0.5 <= duration <= 12.0):
      raise ValueError("Duration out of range. Please enter a value between 0.5 and 12.0 s.")
    await self.set_sealing_temperature(temperature)
    sealing_time_deciseconds = round(duration * 10)
    logger.info("[PlateLoc %s] setting sealing time to %.2f s", self.port, duration)
    await self._send(f"SS 0.{sealing_time_deciseconds:02d}")
    logger.info("[PlateLoc %s] starting sealing cycle", self.port)
    response = await self._send("GO 00")
    deadline = time.monotonic() + self.timeout
    while not await self.request_cycle_complete():
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise TimeoutError("Timeout while waiting for PlateLoc cycle to complete")
      await asyncio.sleep(min(max(self.profile.cycle_poll_interval, 0), remaining))
    return response

  async def open(self) -> str:
    """Move the plate stage to its open position."""
    logger.info("[PlateLoc %s] moving stage out", self.port)
    response = await self._send("SO 00")
    if self.profile.stage_move_delay > 0:
      await asyncio.sleep(self.profile.stage_move_delay)
    self._stage_position = "open"
    return response

  async def close(self) -> str:
    """Move the plate stage to its closed position."""
    logger.info("[PlateLoc %s] moving stage in", self.port)
    response = await self._send("SI 00")
    if self.profile.stage_move_delay > 0:
      await asyncio.sleep(self.profile.stage_move_delay)
    self._stage_position = "closed"
    return response

  def serialize(self) -> dict:
    """Serialize the connection configuration."""
    return {
      "port": self.port,
      "profile": self.profile.serialize(),
      "timeout": self.timeout,
    }

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import re
import time
from typing import Mapping, Optional, cast

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.sealing import Sealer, SealerBackend
from pylabrobot.device import Device, Driver
from pylabrobot.io.serial import Serial

try:
  import serial as _serial  # noqa: F401

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

logger = logging.getLogger(__name__)


DEFAULT_PLATELOC_COMMANDS: Mapping[str, str] = {
  "set_sealing_temperature": "ST",
  "set_sealing_time": "SS",
  "move_stage_out": "SO",
  "move_stage_in": "SI",
  "start_cycle": "GO",
  "stop_cycle": "AC",
  "apply_seal": "AS",
  "clear_error": "CL",
  "check_cycle_complete": "CC",
}

_ACK_RE = re.compile(r"^\s*(?P<code>[A-Z0-9]{2})(?P<status>[AN])K(?:\((?P<message>.*)\))?\s*$")


class PlateLocError(RuntimeError):
  """Raised when PlateLoc communication or protocol handling fails."""


@dataclasses.dataclass(frozen=True)
class PlateLocStatus:
  """Best-known PlateLoc state from direct serial control."""

  port: str
  connected: bool
  target_temperature: Optional[float]
  sealing_time: Optional[float]
  stage_position: Optional[str]
  cycle_complete: Optional[bool]
  last_command: Optional[str]
  last_response: Optional[str]


@dataclasses.dataclass(frozen=True)
class PlateLocSerialProfile:
  """Serial settings and command codes for a PlateLoc controller.

  The decoded low-level protocol uses two-letter command codes followed by a payload and a
  carriage return. Setpoint payloads are encoded as a decimal fraction whose fractional digits
  hold the integer setpoint, for example ``ST 0.175`` for 175 C and ``SS 0.12`` for 1.2 s.
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
  commands: Mapping[str, str] = dataclasses.field(
    default_factory=lambda: dict(DEFAULT_PLATELOC_COMMANDS)
  )

  def format_command(self, command: str, payload: str = "00") -> bytes:
    code = self.commands.get(command)
    if code is None:
      raise PlateLocError(f"No PlateLoc serial command configured for {command!r}.")
    return f"{code} {payload}{self.command_terminator}".encode("ascii")

  def serialize(self) -> dict:
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
      "commands": dict(self.commands),
    }

  @classmethod
  def deserialize(cls, data: dict) -> "PlateLocSerialProfile":
    data = data.copy()
    if "response_terminator" in data:
      data["response_terminator"] = data["response_terminator"].encode("latin1")
    return cls(**data)


class PlateLocDriver(Driver):
  """Direct serial driver for the Agilent PlateLoc thermal microplate sealer."""

  def __init__(
    self,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    profile: Optional[PlateLocSerialProfile | dict] = None,
    timeout: float = 30,
    serial_cls=Serial,
  ) -> None:
    super().__init__()
    if serial_cls is Serial and not HAS_SERIAL:
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
    self._sealing_time: Optional[float] = None
    self._stage_position: Optional[str] = None
    self._last_command: Optional[str] = None
    self._last_response: Optional[str] = None
    self.io = serial_cls(
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
    return cast(str, self.io.port)

  async def setup(self, backend_params: Optional[BackendParams] = None):
    await self.io.setup()
    self._connected = True
    logger.info("[PlateLoc %s] connected", self.port)

  async def stop(self):
    await self.io.stop()
    self._connected = False
    logger.info("[PlateLoc %s] disconnected", self.port)

  @contextlib.contextmanager
  def _read_timeout(self, timeout: float):
    if hasattr(self.io, "temporary_timeout"):
      with self.io.temporary_timeout(timeout):
        yield
    else:
      yield

  async def send_command(
    self,
    command: str,
    payload: str = "00",
    expect_response: bool = False,
    raise_on_nak: bool = True,
  ) -> Optional[str]:
    data = self.profile.format_command(command, payload=payload)
    if hasattr(self.io, "reset_input_buffer"):
      await self.io.reset_input_buffer()
    await self.io.write(data)
    if self.profile.read_delay > 0:
      await asyncio.sleep(self.profile.read_delay)

    response = await self.read_response(
      timeout=self.profile.response_timeout if expect_response else self.profile.ack_timeout,
      required=True,
    )
    assert response is not None
    self._last_command = command
    self._last_response = response
    if raise_on_nak:
      self._raise_for_error(command, response)
    return response

  async def read_response(self, timeout: Optional[float] = None, required: bool = True) -> Optional[str]:
    deadline = time.time() + (timeout if timeout is not None else self.profile.response_timeout)
    chunks = bytearray()
    while time.time() < deadline:
      with self._read_timeout(max(0.01, min(0.1, deadline - time.time()))):
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

  def _raise_for_error(self, command: str, response: str):
    match = _ACK_RE.match(response)
    if match is None:
      raise PlateLocError(f"PlateLoc returned invalid response to {command!r}: {response!r}")
    code = match.group("code")
    expected_code = self.profile.commands.get(command)
    if expected_code is not None and code != expected_code:
      raise PlateLocError(f"PlateLoc replied with {code!r} to {command!r}: {response!r}")
    if match.group("status") == "N":
      message = match.group("message") or "command rejected"
      raise PlateLocError(f"PlateLoc rejected {command!r}: {message}")

  async def set_sealing_temperature(self, temperature: float):
    if not (20 <= temperature <= 235):
      raise ValueError("Temperature out of range. Please enter a value between 20 and 235 C.")
    target_temperature = round(temperature)
    payload = f"0.{target_temperature:03d}"
    logger.info("[PlateLoc %s] setting sealing temperature to %.1f C", self.port, temperature)
    response = await self.send_command("set_sealing_temperature", payload=payload)
    self._target_temperature = float(target_temperature)
    return response

  async def set_sealing_time(self, duration: float):
    if not (0.5 <= duration <= 12.0):
      raise ValueError("Duration out of range. Please enter a value between 0.5 and 12.0 s.")
    sealing_time_deciseconds = round(duration * 10)
    payload = f"0.{sealing_time_deciseconds:02d}"
    logger.info("[PlateLoc %s] setting sealing time to %.2f s", self.port, duration)
    response = await self.send_command("set_sealing_time", payload=payload)
    self._sealing_time = sealing_time_deciseconds / 10
    return response

  async def move_stage_out(self):
    logger.info("[PlateLoc %s] moving stage out", self.port)
    response = await self.send_command("move_stage_out")
    if self.profile.stage_move_delay > 0:
      await asyncio.sleep(self.profile.stage_move_delay)
    self._stage_position = "open"
    return response

  async def move_stage_in(self):
    logger.info("[PlateLoc %s] moving stage in", self.port)
    response = await self.send_command("move_stage_in")
    if self.profile.stage_move_delay > 0:
      await asyncio.sleep(self.profile.stage_move_delay)
    self._stage_position = "closed"
    return response

  async def start_cycle(self):
    logger.info("[PlateLoc %s] starting sealing cycle", self.port)
    return await self.send_command("start_cycle")

  async def stop_cycle(self):
    logger.info("[PlateLoc %s] stopping sealing cycle", self.port)
    return await self.send_command("stop_cycle")

  async def apply_seal(self):
    logger.info("[PlateLoc %s] applying seal", self.port)
    return await self.send_command("apply_seal")

  async def clear_error(self):
    logger.info("[PlateLoc %s] clearing error", self.port)
    return await self.send_command("clear_error")

  async def check_cycle_complete(self) -> bool:
    response = await self.send_command(
      "check_cycle_complete",
      expect_response=True,
      raise_on_nak=False,
    )
    match = _ACK_RE.match(response or "")
    if match is None:
      raise PlateLocError(
        f"PlateLoc returned invalid response to 'check_cycle_complete': {response!r}"
      )
    expected_code = self.profile.commands.get("check_cycle_complete")
    if expected_code is not None and match.group("code") != expected_code:
      raise PlateLocError(
        f"PlateLoc replied with {match.group('code')!r} to 'check_cycle_complete': {response!r}"
      )
    return match.group("status") == "A"

  async def wait_for_cycle_complete(self, timeout: Optional[float] = None) -> bool:
    deadline = time.time() + (self.timeout if timeout is None else timeout)
    while True:
      if await self.check_cycle_complete():
        return True
      remaining = deadline - time.time()
      if remaining <= 0:
        raise TimeoutError("Timeout while waiting for PlateLoc cycle to complete")
      await asyncio.sleep(min(max(self.profile.cycle_poll_interval, 0), remaining))

  def status_snapshot(self, cycle_complete: Optional[bool] = None) -> PlateLocStatus:
    return PlateLocStatus(
      port=self.port,
      connected=self._connected,
      target_temperature=self._target_temperature,
      sealing_time=self._sealing_time,
      stage_position=self._stage_position,
      cycle_complete=cycle_complete,
      last_command=self._last_command,
      last_response=self._last_response,
    )

  async def request_status(self, query_cycle_complete: bool = True) -> PlateLocStatus:
    cycle_complete = await self.check_cycle_complete() if query_cycle_complete else None
    return self.status_snapshot(cycle_complete=cycle_complete)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "port": self.port,
      "profile": self.profile.serialize(),
      "timeout": self.timeout,
    }


class PlateLocSealerBackend(SealerBackend):
  """Translates SealerBackend operations into direct PlateLoc serial commands."""

  def __init__(self, driver: PlateLocDriver):
    self.driver = driver

  async def seal(self, temperature: int, duration: float):
    await self.driver.set_sealing_temperature(temperature)
    await self.driver.set_sealing_time(duration)
    response = await self.driver.start_cycle()
    await self.driver.wait_for_cycle_complete()
    return response

  async def open(self):
    return await self.driver.move_stage_out()

  async def close(self):
    return await self.driver.move_stage_in()


class PlateLoc(Device):
  """Agilent PlateLoc thermal microplate sealer."""

  def __init__(
    self,
    name: str,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    profile: Optional[PlateLocSerialProfile | dict] = None,
    timeout: float = 30,
    serial_cls=Serial,
  ):
    self.name = name
    driver = PlateLocDriver(
      port=port,
      vid=vid,
      pid=pid,
      profile=profile,
      timeout=timeout,
      serial_cls=serial_cls,
    )
    super().__init__(driver=driver)
    self.driver: PlateLocDriver = driver
    self.sealer = Sealer(backend=PlateLocSealerBackend(driver))
    self._capabilities = [self.sealer]

  async def set_sealing_temperature(self, temperature: float):
    return await self.driver.set_sealing_temperature(temperature)

  async def set_sealing_time(self, duration: float):
    return await self.driver.set_sealing_time(duration)

  def status_snapshot(self, cycle_complete: Optional[bool] = None) -> PlateLocStatus:
    return self.driver.status_snapshot(cycle_complete=cycle_complete)

  async def request_status(self, query_cycle_complete: bool = True) -> PlateLocStatus:
    return await self.driver.request_status(query_cycle_complete=query_cycle_complete)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "name": self.name,
      "status": dataclasses.asdict(self.status_snapshot()),
    }

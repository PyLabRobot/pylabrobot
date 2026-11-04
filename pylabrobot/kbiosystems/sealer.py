import abc
import enum
import logging
from typing import Dict, List, Optional, Type

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

_COMMAND_DELAY_SECONDS = 0.2
_SETPOINT_SETTLE_SECONDS = 3.0


class KBiosystemsError(Exception):
  """Error raised by a KBiosystems heat sealer."""

  def __init__(
    self,
    title: str,
    message: Optional[str] = None,
    status: Optional[enum.IntFlag] = None,
    error_code: Optional[int] = None,
  ) -> None:
    self.title = title
    self.message = message
    self.status = status
    self.error_code = error_code

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class KBiosystemsSealer(HasLifespan, abc.ABC):
  """Shared base for KBiosystems serial heat sealers (Ultraseal ePRO / XT Pro).

  Every model speaks the same ASCII-over-RS-232 core: a command is written with
  a CR terminator, the device echoes the command in front of its reply, and the
  echo is stripped before the reply is parsed (e.g. ``A160`` -> ``A160ok`` ->
  ``ok``; ``?`` -> ``?3f`` -> ``3f``). A reply containing ``syntax`` means the
  command was rejected. This base implements the transport, the status/error
  polling state machine, and the temperature and time commands shared by all
  models. Subclasses supply the model-specific status flags, error table,
  temperature limits, human-readable name, lifespan initialization, ``seal``,
  and any extra commands.

  Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit, no handshake,
  CR (``\\r``) terminator.
  """

  # Human-readable device name, used for the serial handle. Overridden per model.
  _HUMAN_READABLE_NAME: str = "KBiosystems Heat Sealer"

  # Status flag enum returned by ``?``. Overridden per model.
  STATUS: Type[enum.IntFlag]
  # Description for each status bit, used to explain a not-ready condition.
  STATUS_MESSAGES: Dict[enum.IntFlag, str]
  # Text for each numeric error code returned by ``E``.
  ERRORS: Dict[int, str]

  # The low status bits are identical across every model, so the state machine
  # below works against them directly rather than the per-model enum.
  _READY = 0x00
  _ERROR = 0x02
  _BUSY = 0x04
  _NOT_AT_SEAL_TEMPERATURE = 0x08

  MIN_SEALING_TEMPERATURE: int
  MAX_SEALING_TEMPERATURE: int
  MIN_SEALING_DURATION: float = 0.5
  MAX_SEALING_DURATION: float = 9.9

  def __init__(
    self,
    port: str,
    timeout: float = 5.0,
    settle_time: float = 5.0,
    preheating_temperature: int = 100,
    offline_temperature: int = 20,
  ) -> None:
    super().__init__()
    self._command_lock = anyio.Lock()
    self.timeout = timeout
    self.settle_time = settle_time
    self.preheating_temperature = preheating_temperature
    self.offline_temperature = offline_temperature
    self.io = Serial(
      human_readable_device_name=self._HUMAN_READABLE_NAME,
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
    )

  # === Subclass contract ===

  @abc.abstractmethod
  async def seal(self, temperature: int, duration: float) -> None:
    """Seal a plate at the given sealing temperature (C) and duration (s)."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own Serial and register offline-temperature cleanup before initialization."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io.lifespan())
    stack.push_shielded_async_callback(self._set_offline_temperature_for_exit)
    # The device drops characters for a few seconds after the port opens.
    await anyio.sleep(self.settle_time)
    await self.io.reset_input_buffer()

  async def _set_offline_temperature_for_exit(self) -> None:
    """Return the heater to the configured offline temperature before Serial closes."""
    try:
      await self.set_temperature(self.offline_temperature)
    except (KBiosystemsError, TimeoutError) as e:
      logger.warning("[%s] could not set offline temperature: %s", self.io.port, e)

  # === Command layer ===

  async def _read_line(self) -> str:
    """Read bytes until a CR terminator, skipping stray LF."""
    buf = bytearray()
    try:
      with anyio.fail_after(self.timeout):
        while True:
          b = await self.io.read(1)
          if b == b"\r":
            break
          if b == b"\n":
            continue
          buf += b
    except TimeoutError:
      raise TimeoutError("Timeout while waiting for response from sealer.") from None
    return buf.decode("ascii", errors="replace")

  async def _send_command(
    self, command: str, read_reply: bool = True, reply_count: int = 1
  ) -> List[str]:
    """Serialize one write and all of its expected reply lines."""
    async with self._command_lock:
      await anyio.lowlevel.checkpoint()
      await self.io.reset_input_buffer()
      with anyio.CancelScope(shield=True):
        await self.io.write((command + "\r").encode("ascii"))
        await anyio.sleep(_COMMAND_DELAY_SECONDS)
        if read_reply:
          replies = [await self._read_line() for _ in range(reply_count)]
        else:
          replies = [""]
      await anyio.lowlevel.checkpoint()
    first = replies[0]
    replies[0] = first if "syntax" in first else first.lstrip(command)
    return replies

  async def send_command(self, command: str, read_reply: bool = True) -> str:
    """Send a command and return the reply with the echoed command stripped.

    Args:
      command: command string without the CR terminator, e.g. "A160", "?".
      read_reply: if False, do not read a reply (used for commands that send none).
    """
    replies = await self._send_command(command, read_reply=read_reply)
    return replies[0].strip() if not command else replies[0]

  def _check(self, reply: str, allowed: set, command: str) -> str:
    if reply not in allowed or "syntax" in reply:
      raise KBiosystemsError(
        title="Unexpected response", message=f"command {command!r} returned {reply!r}"
      )
    return reply

  # === State ===

  async def request_status(self) -> enum.IntFlag:
    """Read the status byte (``?``)."""
    reply = await self.send_command("?")
    try:
      return self.STATUS(int(reply, 16))
    except ValueError as e:
      raise KBiosystemsError(title="Unexpected status reply", message=repr(reply)) from e

  async def request_error_code(self) -> int:
    """Read the current error code (``E``)."""
    reply = await self.send_command("E")
    try:
      return int(reply)
    except ValueError as e:
      raise KBiosystemsError(title="Unexpected error reply", message=repr(reply)) from e

  async def wait_for_idle(self, ignore_mask: Optional[enum.IntFlag] = None) -> enum.IntFlag:
    """Wait until the device is Ready, tolerating the bits set in ``ignore_mask``.

    Raises KBiosystemsError if any bit outside ``ignore_mask`` is set; if the
    Error bit is set, the error code and text are attached.
    """
    mask = self._READY if ignore_mask is None else int(ignore_mask)

    status = await self.request_status()
    while status != self._READY:
      if status & self._BUSY:
        await anyio.sleep(0.5)
        status = await self._wait_for_busy_cleared()
        continue

      # While heating, the device also reports Busy; ignore it alongside
      # NotAtSealTemperature so waiting for temperature does not raise.
      if mask & self._NOT_AT_SEAL_TEMPERATURE and status & self._NOT_AT_SEAL_TEMPERATURE:
        mask = mask | self._BUSY

      remaining = status & ~mask
      if remaining == self._READY:
        break

      description = ", ".join(m for bit, m in self.STATUS_MESSAGES.items() if remaining & bit)
      if remaining & self._ERROR:
        code = await self.request_error_code()
        raise KBiosystemsError(
          title=f"Sealer error: {description}",
          message=self.ERRORS.get(code),
          status=remaining,
          error_code=code,
        )
      raise KBiosystemsError(title=f"Sealer not ready: {description}", status=remaining)
    return status

  async def _wait_for_busy_cleared(self, timeout: float = 60.0) -> enum.IntFlag:
    try:
      with anyio.fail_after(timeout) as timeout_scope:
        status = await self.request_status()
        while status & self._BUSY:
          await anyio.sleep(0.5)
          status = await self.request_status()
        return status
    except TimeoutError:
      if not timeout_scope.cancel_called:
        raise
      raise KBiosystemsError(title="Timeout while waiting for busy flag to clear") from None

  async def wait_for_sealing_temperature(self, timeout: float = 300.0) -> None:
    """Block until the heater reaches the setpoint (NotAtSealTemperature clears)."""
    try:
      with anyio.fail_after(timeout) as timeout_scope:
        while await self.request_status() & self._NOT_AT_SEAL_TEMPERATURE:
          await anyio.sleep(5.0)
    except TimeoutError:
      if not timeout_scope.cancel_called:
        raise
      raise TimeoutError("Timeout while waiting for sealing temperature.") from None

  # === Parameters ===

  async def set_temperature(self, temperature: int) -> None:
    """Set the sealing temperature in degrees C (``A``)."""
    if not self.MIN_SEALING_TEMPERATURE <= temperature <= self.MAX_SEALING_TEMPERATURE:
      raise ValueError(
        f"temperature must be {self.MIN_SEALING_TEMPERATURE}..{self.MAX_SEALING_TEMPERATURE} C"
      )
    command = f"A{temperature:03d}"
    self._check(await self.send_command(command), {"ok"}, command)
    # The firmware needs a moment to accept the new setpoint.
    await anyio.sleep(_SETPOINT_SETTLE_SECONDS)

  async def set_sealing_time(self, seconds: float) -> None:
    """Set the sealing time in seconds (``B``)."""
    if not self.MIN_SEALING_DURATION <= seconds <= self.MAX_SEALING_DURATION:
      raise ValueError(f"time must be {self.MIN_SEALING_DURATION}..{self.MAX_SEALING_DURATION} s")
    command = f"B{int(seconds * 10):02d}"
    self._check(await self.send_command(command), {"ok"}, command)

  async def request_sealing_temperature(self) -> int:
    """Read the sealing temperature setpoint in degrees C (``C``)."""
    reply = await self.send_command("C")
    try:
      return int(reply)
    except ValueError as e:
      raise KBiosystemsError(title="Unexpected temperature reply", message=repr(reply)) from e

  async def request_sealing_time(self) -> float:
    """Read the sealing time setpoint in seconds (``D``)."""
    reply = await self.send_command("D")
    try:
      return int(reply) / 10.0
    except ValueError as e:
      raise KBiosystemsError(title="Unexpected time reply", message=repr(reply)) from e

  async def request_temperature(self) -> int:
    """Read the current heater temperature in degrees C (``F``)."""
    reply = await self.send_command("F")
    try:
      return int(reply)
    except ValueError as e:
      raise KBiosystemsError(title="Unexpected temperature reply", message=repr(reply)) from e

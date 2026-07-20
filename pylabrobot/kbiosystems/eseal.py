import asyncio
import enum
import logging
import time
from typing import Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)


class ESealStatus(enum.IntFlag):
  """Status byte returned by the ``?`` command (two hex digits).

  ``Ready`` (0x00) is the all-clear value; any other bit is a condition that
  must be cleared, or masked when ignorable, before an operation runs.
  """

  Ready = 0x00
  NoFoil = 0x01
  Error = 0x02
  Busy = 0x04
  NotAtSealTemperature = 0x08
  PlateNotPresent = 0x10
  NotInitialised = 0x20
  ForceSensorActivated = 0x40
  ParkMode = 0x80


# Text for each status bit, used to describe a not-ready condition.
STATUS_MESSAGES = {
  ESealStatus.NoFoil: "No foil detected.",
  ESealStatus.Error: "Error",
  ESealStatus.Busy: (
    "Device not ready. Make sure instrument is initialized, film loaded, door "
    "closed, tray out, and control software is on main menu"
  ),
  ESealStatus.NotAtSealTemperature: "Waiting for seal temperature",
  ESealStatus.PlateNotPresent: "No plate detected.",
  ESealStatus.NotInitialised: "Sealer not initialized.",
  ESealStatus.ForceSensorActivated: "Force sensor activated.",
  ESealStatus.ParkMode: "Park mode.",
}

# Error codes returned by the ``E`` command (two decimal digits).
DEVICE_ERRORS = {
  1: "Vertical shuttle down.",
  2: "Heater up.",
  3: "Shuttle in.",
  4: "Cutter error.",
  5: "Thermocouple error - ambient temperature may be too low.",
  6: "The sealer is overheating.",
  7: "No foil detected.",
  8: "No plate detected.",
  9: "Force sensor activated.",
}

MIN_SEALING_TEMPERATURE = 5
MAX_SEALING_TEMPERATURE = 199
MIN_SEALING_DURATION = 0.5
MAX_SEALING_DURATION = 9.9
MIN_SEALING_FORCE = 10
MAX_SEALING_FORCE = 50
MIN_SEALING_DISTANCE = 10
MAX_SEALING_DISTANCE = 50
MIN_FOIL_LENGTH = 117
MAX_FOIL_LENGTH = 128


class KBiosystemsError(Exception):
  """Exceptions raised by a KBiosystems eSeal heat sealer."""

  def __init__(
    self,
    title: str,
    message: Optional[str] = None,
    status: Optional[ESealStatus] = None,
    error_code: Optional[int] = None,
  ) -> None:
    self.title = title
    self.message = message
    self.status = status
    self.error_code = error_code

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class KBiosystemsESeal:
  """KBiosystems eSeal heat sealer.

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, no handshake, "\\r"
    terminator.

  Commands (ASCII, terminated with CR). The device echoes the command in front
  of its reply, so the leading command characters are stripped before the reply
  is read (e.g. ``A160`` -> ``A160ok`` -> ``ok``; ``?`` -> ``?3f`` -> ``3f``). A
  reply containing ``syntax`` means the command was rejected.
    ?            read status byte, two hex digits (see ESealStatus)
    E            read error code, two decimal digits (see DEVICE_ERRORS)
    S            seal the plate; replies ok or err
    I            initialize/home; replies ok or err
    A{t:03d}     set sealing temperature, degrees C (5..199)
    B{t:02d}     set sealing time, deciseconds (05..99 = 0.5..9.9 s)
    L={l:03d}    set foil length (117..128)
    DO={d:02d}   set sealing distance (10..50), distance mode
    PS={f:02d}   set sealing force (10..50), force mode; no reply
    FS={0|1}     set force mode off/on
    ECO_{ON|OFF} set eco mode
    C            read sealing temperature setpoint, three digits
    D            read sealing time setpoint, two digits (deciseconds)
    F            read current heater temperature, three digits
    V            read firmware version (two lines)

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning
  is emitted at setup.
  """

  def __init__(
    self,
    port: str,
    timeout: float = 5.0,
    settle_time: float = 5.0,
    preheating_temperature: int = 100,
    offline_temperature: int = 20,
  ) -> None:
    self.timeout = timeout
    self.settle_time = settle_time
    self.preheating_temperature = preheating_temperature
    self.offline_temperature = offline_temperature
    self.firmware_version: Optional[str] = None
    self.io = Serial(
      human_readable_device_name="KBiosystems eSeal Heat Sealer",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=0.2,
    )

  async def setup(self) -> None:
    logger.warning(
      "KBiosystemsESeal has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self.io.setup()
    # The device drops characters for a few seconds after the port opens.
    await asyncio.sleep(self.settle_time)
    await self.io.reset_input_buffer()
    await self.wait_for_idle(
      ESealStatus.NoFoil
      | ESealStatus.NotAtSealTemperature
      | ESealStatus.ForceSensorActivated
      | ESealStatus.ParkMode
    )
    self.firmware_version = await self.request_firmware_version()
    await self.set_temperature(self.preheating_temperature)
    logger.info("[eSeal %s] connected: firmware=%s", self.io.port, self.firmware_version)

  async def stop(self) -> None:
    try:
      await self.set_temperature(self.offline_temperature)
    except (KBiosystemsError, TimeoutError) as e:
      logger.warning("[eSeal %s] could not set offline temperature: %s", self.io.port, e)
    await self.io.stop()

  # === Command layer ===

  async def _read_line(self) -> str:
    """Read bytes until a CR terminator, skipping stray LF."""
    start = time.time()
    buf = bytearray()
    while True:
      b = await self.io.read(1)
      if b == b"":
        if time.time() - start > self.timeout:
          raise TimeoutError("Timeout while waiting for response from sealer.")
        continue
      if b == b"\r":
        break
      if b == b"\n":
        continue
      buf += b
    return buf.decode("ascii", errors="replace")

  async def send_command(self, command: str, read_reply: bool = True) -> str:
    """Send a command and return the reply with the echoed command stripped.

    Args:
      command: command string without the CR terminator, e.g. "A160", "?".
      read_reply: if False, do not read a reply (used for PS=, which sends none).
    """
    await self.io.reset_input_buffer()
    await self.io.write((command + "\r").encode("ascii"))
    await asyncio.sleep(0.2)
    if not read_reply:
      return ""
    text = await self._read_line()
    if command == "V":
      text = text + " | " + await self._read_line()
    if "syntax" in text:
      return text
    # The device echoes the command; an empty command trims whitespace only.
    return text.lstrip(command) if command else text.strip()

  def _check(self, reply: str, allowed: set, command: str) -> str:
    if reply not in allowed or "syntax" in reply:
      raise KBiosystemsError(
        title="Unexpected response", message=f"command {command!r} returned {reply!r}"
      )
    return reply

  # === State ===

  async def request_status(self) -> ESealStatus:
    """Read the status byte (``?``)."""
    reply = await self.send_command("?")
    try:
      return ESealStatus(int(reply, 16))
    except ValueError as e:
      raise KBiosystemsError(title="Unexpected status reply", message=repr(reply)) from e

  async def request_error_code(self) -> int:
    """Read the current error code (``E``)."""
    reply = await self.send_command("E")
    try:
      return int(reply)
    except ValueError as e:
      raise KBiosystemsError(title="Unexpected error reply", message=repr(reply)) from e

  async def wait_for_idle(self, ignore_mask: ESealStatus = ESealStatus.Ready) -> ESealStatus:
    """Wait until the device is Ready, tolerating the bits set in ``ignore_mask``.

    Raises KBiosystemsError if any bit outside ``ignore_mask`` is set; if the
    Error bit is set, the error code and text are attached.
    """
    status = await self.request_status()
    while status != ESealStatus.Ready:
      if status & ESealStatus.Busy:
        await asyncio.sleep(0.5)
        status = await self._wait_for_busy_cleared()
        continue

      # While heating, the device also reports Busy; ignore it alongside
      # NotAtSealTemperature so waiting for temperature does not raise.
      if (
        ignore_mask & ESealStatus.NotAtSealTemperature and status & ESealStatus.NotAtSealTemperature
      ):
        ignore_mask = ignore_mask | ESealStatus.Busy

      remaining = status & ~ignore_mask
      if remaining == ESealStatus.Ready:
        break

      description = ", ".join(m for bit, m in STATUS_MESSAGES.items() if remaining & bit)
      if remaining & ESealStatus.Error:
        code = await self.request_error_code()
        raise KBiosystemsError(
          title=f"Sealer error: {description}",
          message=DEVICE_ERRORS.get(code),
          status=remaining,
          error_code=code,
        )
      raise KBiosystemsError(title=f"Sealer not ready: {description}", status=remaining)
    return status

  async def _wait_for_busy_cleared(self, timeout: float = 60.0) -> ESealStatus:
    start = time.time()
    status = await self.request_status()
    while status & ESealStatus.Busy:
      if time.time() - start > timeout:
        raise KBiosystemsError(title="Timeout while waiting for busy flag to clear")
      await asyncio.sleep(0.5)
      status = await self.request_status()
    return status

  async def wait_for_sealing_temperature(self, timeout: float = 300.0) -> None:
    """Block until the heater reaches the setpoint (NotAtSealTemperature clears)."""
    start = time.time()
    while await self.request_status() & ESealStatus.NotAtSealTemperature:
      if time.time() - start > timeout:
        raise TimeoutError("Timeout while waiting for sealing temperature.")
      await asyncio.sleep(5.0)

  # === Parameters ===

  async def set_temperature(self, temperature: int) -> None:
    """Set the sealing temperature in degrees C (``A``, 5..199)."""
    if not MIN_SEALING_TEMPERATURE <= temperature <= MAX_SEALING_TEMPERATURE:
      raise ValueError(
        f"temperature must be {MIN_SEALING_TEMPERATURE}..{MAX_SEALING_TEMPERATURE} C"
      )
    command = f"A{temperature:03d}"
    self._check(await self.send_command(command), {"ok"}, command)
    # The firmware needs a moment to accept the new setpoint.
    await asyncio.sleep(3.0)

  async def set_sealing_time(self, seconds: float) -> None:
    """Set the sealing time in seconds (``B``, 0.5..9.9)."""
    if not MIN_SEALING_DURATION <= seconds <= MAX_SEALING_DURATION:
      raise ValueError(f"time must be {MIN_SEALING_DURATION}..{MAX_SEALING_DURATION} s")
    command = f"B{int(seconds * 10):02d}"
    self._check(await self.send_command(command), {"ok"}, command)

  async def set_foil_length(self, length: int) -> None:
    """Set the foil length (``L=``, 117..128)."""
    if not MIN_FOIL_LENGTH <= length <= MAX_FOIL_LENGTH:
      raise ValueError(f"foil length must be {MIN_FOIL_LENGTH}..{MAX_FOIL_LENGTH}")
    command = f"L={length:03d}"
    self._check(await self.send_command(command), {"ok"}, command)

  async def set_sealing_distance(self, distance: int) -> None:
    """Set the sealing distance (``DO=``, 10..50). Selects distance mode."""
    if not MIN_SEALING_DISTANCE <= distance <= MAX_SEALING_DISTANCE:
      raise ValueError(f"distance must be {MIN_SEALING_DISTANCE}..{MAX_SEALING_DISTANCE}")
    command = f"DO={distance:02d}"
    reply = await self.send_command(command)
    # The firmware acknowledges with "ok" or an empty reply.
    if reply not in ("ok", "") or "syntax" in reply:
      raise KBiosystemsError(title="Setting distance failed", message=reply)

  async def set_sealing_force(self, force: int) -> None:
    """Set the sealing force (``PS=``, 10..50). Selects force mode. Sends no reply."""
    if not MIN_SEALING_FORCE <= force <= MAX_SEALING_FORCE:
      raise ValueError(f"force must be {MIN_SEALING_FORCE}..{MAX_SEALING_FORCE}")
    await self.send_command(f"PS={force:02d}", read_reply=False)

  async def set_force_mode(self, on: bool) -> None:
    """Select force mode (``FS=1``) or distance mode (``FS=0``)."""
    command = f"FS={1 if on else 0}"
    self._check(await self.send_command(command), {"ok"}, command)

  async def set_eco_mode(self, on: bool) -> None:
    """Enable or disable eco mode (``ECO_ON`` / ``ECO_OFF``)."""
    command = f"ECO_{'ON' if on else 'OFF'}"
    reply = await self.send_command(command)
    if reply == "err" or "syntax" in reply:
      raise KBiosystemsError(title="Setting eco mode failed", message=reply)

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

  async def request_firmware_version(self) -> str:
    """Read the firmware version (``V``)."""
    return await self.send_command("V")

  async def initialize(self) -> None:
    """Initialize/home the sealer (``I``)."""
    command = "I"
    if self._check(await self.send_command(command), {"ok", "err"}, command) != "ok":
      raise KBiosystemsError(title="Initializing the eSeal failed")

  # === Operations ===

  async def seal(
    self,
    temperature: int,
    duration: float,
    foil_length: int = 120,
    force_mode: bool = False,
    sealing_force: int = 50,
    sealing_distance: int = 25,
    idle_temperature: int = 100,
    eco_mode: bool = False,
  ) -> None:
    """Seal a plate.

    Waits for the device to be ready, applies the time/temperature/foil and
    force-or-distance parameters, waits for the heater to reach the setpoint,
    seals, then returns to ``idle_temperature``.

    Args:
      temperature: sealing temperature in degrees C (5..199).
      duration: sealing time in seconds (0.5..9.9).
      foil_length: foil length (117..128).
      force_mode: seal by force (True) or by distance (False).
      sealing_force: force in force mode (10..50).
      sealing_distance: distance in distance mode (10..50).
      idle_temperature: temperature to hold after sealing (5..199).
      eco_mode: enable eco mode after sealing.
    """
    logger.info("[eSeal %s] sealing at %d C for %.1fs", self.io.port, temperature, duration)
    ignore = (
      ESealStatus.NotAtSealTemperature | ESealStatus.ForceSensorActivated | ESealStatus.ParkMode
    )

    # Eco mode is disabled while sealing and restored afterwards if requested.
    await self.set_eco_mode(False)
    await self.wait_for_idle(ignore)

    await self.set_sealing_time(duration)
    await self.set_temperature(temperature)
    await self.set_foil_length(foil_length)
    if force_mode:
      await self.set_force_mode(True)
      await self.set_sealing_force(sealing_force)
    else:
      await self.set_force_mode(False)
      await self.set_sealing_distance(sealing_distance)

    await self.wait_for_sealing_temperature()
    if self._check(await self.send_command("S"), {"ok", "err"}, "S") != "ok":
      raise KBiosystemsError(title="Seal command returned 'err'")
    await self.wait_for_idle(ignore)

    await self.set_temperature(idle_temperature)
    if eco_mode:
      await self.set_eco_mode(True)

import asyncio
import enum
import logging
import time
from typing import Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)


class KUBEStatus(enum.IntFlag):
  """Status byte returned by the ``?`` command (two hex digits).

  ``Ready`` (0x00) is the all-clear value; any other bit is a condition that
  must be cleared, or masked when ignorable, before an operation runs. Note the
  bit layout is specific to the KUBE firmware and differs from other sealers:
  ``Busy`` and ``Sealing`` are distinct bits.
  """

  Ready = 0x00
  Busy = 0x01
  Error = 0x02
  Sealing = 0x04
  NotAtSealTemperature = 0x08
  SystemShutdown = 0x10
  FoilLow = 0x20
  DoorOpen = 0x40
  LowAirPressure = 0x80


# Text for each status bit, used to describe a not-ready condition.
STATUS_MESSAGES = {
  KUBEStatus.Busy: (
    "Device not ready. Make sure instrument is initialized, film loaded, door "
    "closed, tray out, and control software is on main menu"
  ),
  KUBEStatus.Error: "Error",
  KUBEStatus.Sealing: "Sealing",
  KUBEStatus.NotAtSealTemperature: "Waiting for seal temperature",
  KUBEStatus.SystemShutdown: "System shutting down",
  KUBEStatus.FoilLow: "No foil detected or foil low.",
  KUBEStatus.DoorOpen: "Door open",
  KUBEStatus.LowAirPressure: "Low air pressure detected.",
}

# Error codes returned by the ``E`` command (two decimal digits). The firmware
# leaves gaps in the numbering (there is no code 2).
DEVICE_ERRORS = {
  1: "Low air pressure detected. Check air pressure.",
  3: "Shuttle not out.",
  4: "No foil or low foil error. Check foil and foil sensor.",
  5: "Seal transfer error.",
  6: "Placement error (can be caused of low air pressure).",
  7: "Thermocouple error - ambient temperature may be too low.",
}

MIN_SEALING_TEMPERATURE = 5
MAX_SEALING_TEMPERATURE = 199
MIN_SEALING_DURATION = 0.5
MAX_SEALING_DURATION = 9.9


class SealPreset(enum.IntEnum):
  """Sealing configuration: user-defined (time + temperature) or one of six
  presets stored on the instrument."""

  USER_DEFINED = 0
  ONE = 1
  TWO = 2
  THREE = 3
  FOUR = 4
  FIVE = 5
  SIX = 6


class KBioscienceError(Exception):
  """Exceptions raised by a KBioscience KUBE thermal plate sealer."""

  def __init__(
    self,
    title: str,
    message: Optional[str] = None,
    status: Optional[KUBEStatus] = None,
    error_code: Optional[int] = None,
  ) -> None:
    self.title = title
    self.message = message
    self.status = status
    self.error_code = error_code

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class KBioscienceKUBE:
  """KBioscience KUBE thermal plate sealer.

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, no handshake, "\\r"
    terminator.

  Commands (ASCII, terminated with CR). The device echoes the command in front
  of its reply, so the leading command characters are stripped before the reply
  is read (e.g. ``A160`` -> ``A160ok`` -> ``ok``; ``?`` -> ``?3f`` -> ``3f``). A
  reply containing ``syntax`` means the command was rejected.
    ?            read status byte, two hex digits (see KUBEStatus)
    E            read error code, two decimal digits (see DEVICE_ERRORS)
    S            seal the plate; replies ok or err
    A{t:03d}     set sealing temperature, degrees C (5..199)
    B{t:02d}     set sealing time, deciseconds (05..99 = 0.5..9.9 s)
    P{n}         select seal preset 1..6
    X{1|2}       set plate orientation: 1 portrait, 2 landscape; replies ok/err
    H            clear the error message window on the instrument display
    C            read sealing temperature setpoint, three digits
    D            read sealing time setpoint, two digits (deciseconds)
    F            read current heater temperature, three digits
    V            read firmware version
    @            read product name / model description
    <empty>      protocol probe; a unit replying ``ALPS300`` speaks the older,
                 incompatible protocol and is rejected at setup

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
    portrait: bool = False,
  ) -> None:
    self.timeout = timeout
    self.settle_time = settle_time
    self.preheating_temperature = preheating_temperature
    self.offline_temperature = offline_temperature
    self.portrait = portrait
    self.firmware_version: Optional[str] = None
    self.product_name: Optional[str] = None
    self.io = Serial(
      human_readable_device_name="KBioscience KUBE Thermal Plate Sealer",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=0.2,
    )

  async def setup(self) -> None:
    logger.warning(
      "KBioscienceKUBE has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self.io.setup()
    # The device drops characters for a few seconds after the port opens.
    await asyncio.sleep(self.settle_time)
    await self.io.reset_input_buffer()
    # A unit that answers the empty-command probe with "ALPS300" speaks the
    # older protocol this driver does not support.
    if await self._is_alps300():
      raise KBioscienceError(
        title="Incompatible communication protocol",
        message=(
          "Please uncheck ALPS Compatibility from instrument software menu "
          "[Menu->Supervisor options->Remote control settings->ALPS Compatibility]"
        ),
      )
    await self.wait_for_idle(
      KUBEStatus.NotAtSealTemperature | KUBEStatus.FoilLow | KUBEStatus.LowAirPressure
    )
    self.firmware_version = await self.request_firmware_version()
    self.product_name = await self.request_product_name()
    await self.set_plate_orientation(self.portrait)
    await self.set_temperature(self.preheating_temperature)
    logger.info(
      "[KUBE %s] connected: model=%s firmware=%s",
      self.io.port,
      self.product_name,
      self.firmware_version,
    )

  async def stop(self) -> None:
    try:
      await self.set_temperature(self.offline_temperature)
    except (KBioscienceError, TimeoutError) as e:
      logger.warning("[KUBE %s] could not set offline temperature: %s", self.io.port, e)
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

  async def send_command(self, command: str) -> str:
    """Send a command and return the reply with the echoed command stripped.

    Args:
      command: command string without the CR terminator, e.g. "A160", "?".
    """
    await self.io.reset_input_buffer()
    await self.io.write((command + "\r").encode("ascii"))
    await asyncio.sleep(0.2)
    text = await self._read_line()
    if "syntax" in text:
      return text
    # The device echoes the command; an empty command trims whitespace only.
    return text.lstrip(command) if command else text.strip()

  def _check(self, reply: str, allowed: set, command: str) -> str:
    if reply not in allowed or "syntax" in reply:
      raise KBioscienceError(
        title="Unexpected response", message=f"command {command!r} returned {reply!r}"
      )
    return reply

  # === State ===

  async def request_status(self) -> KUBEStatus:
    """Read the status byte (``?``)."""
    reply = await self.send_command("?")
    try:
      return KUBEStatus(int(reply, 16))
    except ValueError as e:
      raise KBioscienceError(title="Unexpected status reply", message=repr(reply)) from e

  async def request_error_code(self) -> int:
    """Read the current error code (``E``)."""
    reply = await self.send_command("E")
    try:
      return int(reply)
    except ValueError as e:
      raise KBioscienceError(title="Unexpected error reply", message=repr(reply)) from e

  async def _is_alps300(self) -> bool:
    """Probe the protocol with the empty command; True if the unit is ALPS300."""
    try:
      return await self.send_command("") == "ALPS300"
    except (KBioscienceError, TimeoutError):
      return False

  async def wait_for_idle(self, ignore_mask: KUBEStatus = KUBEStatus.Ready) -> KUBEStatus:
    """Wait until the device is Ready, tolerating the bits set in ``ignore_mask``.

    Raises KBioscienceError if any bit outside ``ignore_mask`` is set; if the
    Error bit is set, the error code and text are attached.
    """
    status = await self.request_status()
    while status != KUBEStatus.Ready:
      if status & KUBEStatus.Sealing:
        await asyncio.sleep(0.5)
        status = await self._wait_for_seal_cleared()
        continue

      # While heating, the device also reports Busy; ignore it alongside
      # NotAtSealTemperature so waiting for temperature does not raise.
      if ignore_mask & KUBEStatus.NotAtSealTemperature and status & KUBEStatus.NotAtSealTemperature:
        ignore_mask = ignore_mask | KUBEStatus.Busy

      remaining = status & ~ignore_mask
      if remaining == KUBEStatus.Ready:
        break

      description = ", ".join(m for bit, m in STATUS_MESSAGES.items() if remaining & bit)
      if remaining & KUBEStatus.Error:
        code = await self.request_error_code()
        raise KBioscienceError(
          title=f"Sealer error: {description}",
          message=DEVICE_ERRORS.get(code),
          status=remaining,
          error_code=code,
        )
      raise KBioscienceError(title=f"Sealer not ready: {description}", status=remaining)
    return status

  async def _wait_for_seal_cleared(self, timeout: float = 60.0) -> KUBEStatus:
    start = time.time()
    status = await self.request_status()
    while status & KUBEStatus.Sealing:
      if time.time() - start > timeout:
        raise KBioscienceError(title="Timeout while waiting for the seal to complete")
      await asyncio.sleep(0.5)
      status = await self.request_status()
    return status

  async def wait_for_sealing_temperature(self, timeout: float = 300.0) -> None:
    """Block until the heater reaches the setpoint (NotAtSealTemperature clears)."""
    start = time.time()
    while await self.request_status() & KUBEStatus.NotAtSealTemperature:
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
    reply = await self.send_command(command)
    if reply != "ok":
      raise KBioscienceError(
        title="Setting sealing temperature failed",
        message=f"temperature {temperature} rejected with {reply!r}",
      )
    # The firmware needs a moment to accept the new setpoint.
    await asyncio.sleep(3.0)

  async def set_sealing_time(self, seconds: float) -> None:
    """Set the sealing time in seconds (``B``, 0.5..9.9)."""
    if not MIN_SEALING_DURATION <= seconds <= MAX_SEALING_DURATION:
      raise ValueError(f"time must be {MIN_SEALING_DURATION}..{MAX_SEALING_DURATION} s")
    command = f"B{int(seconds * 10):02d}"
    self._check(await self.send_command(command), {"ok"}, command)

  async def set_seal_preset(self, preset: SealPreset) -> None:
    """Select one of the instrument's stored presets (``P``, 1..6)."""
    if preset == SealPreset.USER_DEFINED:
      raise ValueError("USER_DEFINED is not a stored preset; set time and temperature instead")
    command = f"P{int(preset)}"
    reply = await self.send_command(command)
    if reply != "ok":
      raise KBioscienceError(title="Setting sealing preset failed", message=f"{command}:{reply}")

  async def set_plate_orientation(self, portrait: bool) -> None:
    """Set the plate orientation (``X``): portrait (``X1``) or landscape (``X2``)."""
    command = f"X{'1' if portrait else '2'}"
    reply = await self.send_command(command)
    if reply != "ok":
      raise KBioscienceError(title="Setting plate orientation failed", message=f"{command}:{reply}")

  async def clear_error_window(self) -> None:
    """Clear the error message window on the instrument display (``H``)."""
    try:
      await self.send_command("H")
    except (KBioscienceError, TimeoutError):
      pass

  async def request_sealing_temperature(self) -> int:
    """Read the sealing temperature setpoint in degrees C (``C``)."""
    reply = await self.send_command("C")
    try:
      return int(reply)
    except ValueError as e:
      raise KBioscienceError(title="Unexpected temperature reply", message=repr(reply)) from e

  async def request_sealing_time(self) -> float:
    """Read the sealing time setpoint in seconds (``D``)."""
    reply = await self.send_command("D")
    try:
      return int(reply) / 10.0
    except ValueError as e:
      raise KBioscienceError(title="Unexpected time reply", message=repr(reply)) from e

  async def request_temperature(self) -> int:
    """Read the current heater temperature in degrees C (``F``)."""
    reply = await self.send_command("F")
    try:
      return int(reply)
    except ValueError as e:
      raise KBioscienceError(title="Unexpected temperature reply", message=repr(reply)) from e

  async def request_firmware_version(self) -> str:
    """Read the firmware version (``V``)."""
    return await self.send_command("V")

  async def request_product_name(self) -> str:
    """Read the product name / model description (``@``)."""
    return await self.send_command("@")

  # === Operations ===

  async def seal(
    self,
    temperature: int = 160,
    duration: float = 2.5,
    preset: SealPreset = SealPreset.USER_DEFINED,
    idle_temperature: int = 100,
  ) -> None:
    """Seal a plate.

    Waits for the device to be ready, applies either the given preset or the
    user-defined time/temperature, waits for the heater to reach the setpoint,
    seals, then returns to ``idle_temperature``.

    Args:
      temperature: sealing temperature in degrees C (5..199); used when
        ``preset`` is USER_DEFINED.
      duration: sealing time in seconds (0.5..9.9); used when ``preset`` is
        USER_DEFINED.
      preset: a stored preset (ONE..SIX) or USER_DEFINED to use time/temperature.
      idle_temperature: temperature to hold after sealing (5..199).
    """
    logger.info(
      "[KUBE %s] sealing (preset=%s) at %d C for %.1fs",
      self.io.port,
      preset.name,
      temperature,
      duration,
    )
    ignore = KUBEStatus.NotAtSealTemperature | KUBEStatus.FoilLow | KUBEStatus.LowAirPressure

    await self.wait_for_idle(ignore)

    if preset == SealPreset.USER_DEFINED:
      await self.set_sealing_time(duration)
      await self.set_temperature(temperature)
    else:
      await self.set_seal_preset(preset)
      await asyncio.sleep(3.0)

    await self.wait_for_sealing_temperature()
    if self._check(await self.send_command("S"), {"ok", "err"}, "S") != "ok":
      raise KBioscienceError(title="Seal command returned 'err'")
    await self.wait_for_idle(KUBEStatus.NotAtSealTemperature | KUBEStatus.LowAirPressure)

    await self.set_temperature(idle_temperature)

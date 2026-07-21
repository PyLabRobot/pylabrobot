import enum
import logging
from typing import Dict, Optional

from pylabrobot.kbiosystems.sealer import KBiosystemsError, KBiosystemsSealer

logger = logging.getLogger(__name__)

__all__ = ["KBiosystemsUltrasealEPRO", "UltrasealEPROStatus", "KBiosystemsError"]


class UltrasealEPROStatus(enum.IntFlag):
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
STATUS_MESSAGES: Dict[enum.IntFlag, str] = {
  UltrasealEPROStatus.NoFoil: "No foil detected.",
  UltrasealEPROStatus.Error: "Error",
  UltrasealEPROStatus.Busy: (
    "Device not ready. Make sure instrument is initialized, film loaded, door "
    "closed, tray out, and control software is on main menu"
  ),
  UltrasealEPROStatus.NotAtSealTemperature: "Waiting for seal temperature",
  UltrasealEPROStatus.PlateNotPresent: "No plate detected.",
  UltrasealEPROStatus.NotInitialised: "Sealer not initialized.",
  UltrasealEPROStatus.ForceSensorActivated: "Force sensor activated.",
  UltrasealEPROStatus.ParkMode: "Park mode.",
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


class KBiosystemsUltrasealEPRO(KBiosystemsSealer):
  """KBiosystems Ultraseal ePRO heat sealer (formerly the eSeal).

  A benchtop heat sealer that presses a heated head onto foil over a plate. In
  addition to the shared temperature/time commands it exposes foil length,
  distance- vs force-controlled sealing, eco mode, and an initialize/home step.

  Commands (in addition to the shared ``?``/``E``/``S``/``A``/``B``/``C``/
  ``D``/``F``, see :class:`KBiosystemsSealer`):
    I            initialize/home; replies ok or err
    L={l:03d}    set foil length (117..128)
    DO={d:02d}   set sealing distance (10..50), distance mode
    PS={f:02d}   set sealing force (10..50), force mode; no reply
    FS={0|1}     set force mode off/on
    ECO_{ON|OFF} set eco mode
    V            read firmware version (two lines)

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning is
  emitted at setup.
  """

  _HUMAN_READABLE_NAME = "KBiosystems Ultraseal ePRO Heat Sealer"

  STATUS = UltrasealEPROStatus
  STATUS_MESSAGES = STATUS_MESSAGES
  ERRORS = DEVICE_ERRORS
  MIN_SEALING_TEMPERATURE = MIN_SEALING_TEMPERATURE
  MAX_SEALING_TEMPERATURE = MAX_SEALING_TEMPERATURE

  def __init__(
    self,
    port: str,
    timeout: float = 5.0,
    settle_time: float = 5.0,
    preheating_temperature: int = 100,
    offline_temperature: int = 20,
  ) -> None:
    super().__init__(
      port,
      timeout=timeout,
      settle_time=settle_time,
      preheating_temperature=preheating_temperature,
      offline_temperature=offline_temperature,
    )
    self.firmware_version: Optional[str] = None

  async def setup(self) -> None:
    logger.warning(
      "KBiosystemsUltrasealEPRO has NOT been tested against hardware in PyLabRobot. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    await self._open()
    # Initialize/home the sealer (``I``).
    if self._check(await self.send_command("I"), {"ok", "err"}, "I") != "ok":
      raise KBiosystemsError(title="Initializing the Ultraseal ePRO failed")
    await self.wait_for_idle(
      UltrasealEPROStatus.NoFoil
      | UltrasealEPROStatus.NotAtSealTemperature
      | UltrasealEPROStatus.ForceSensorActivated
      | UltrasealEPROStatus.ParkMode
    )
    self.firmware_version = await self.request_firmware_version()
    await self.set_temperature(self.preheating_temperature)
    logger.info("[Ultraseal ePRO %s] connected: firmware=%s", self.io.port, self.firmware_version)

  # === Parameters ===

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

  async def request_firmware_version(self) -> str:
    """Read the firmware version (``V``). This device replies with two lines."""
    line1 = await self.send_command("V")
    line2 = await self._read_line()
    return f"{line1} | {line2}"

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
    logger.info(
      "[Ultraseal ePRO %s] sealing at %d C for %.1fs", self.io.port, temperature, duration
    )
    ignore = (
      UltrasealEPROStatus.NotAtSealTemperature
      | UltrasealEPROStatus.ForceSensorActivated
      | UltrasealEPROStatus.ParkMode
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

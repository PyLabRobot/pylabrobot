import asyncio
import enum
import logging
from typing import Dict

from pylabrobot.thermo_fisher.alps.sealer import (
  ThermoScientificALPSError,
  ThermoScientificALPSSealer,
)

logger = logging.getLogger(__name__)


class ALPS5000Status(enum.IntFlag):
  """Status byte returned by the ``?`` command (two hex digits).

  ``Ready`` (0x00) is the all-clear value; any other bit is a condition that
  must be cleared, or masked when ignorable, before an operation runs.
  """

  Ready = 0x00
  NoFoil = 0x01
  Error = 0x02
  Busy = 0x04
  WaitingForSealTemperature = 0x08
  PlateNotPresent = 0x10
  NotInitialised = 0x20
  ForceSensorActivated = 0x40
  PlatePresent = 0x80


STATUS_MESSAGES: Dict[enum.IntFlag, str] = {
  ALPS5000Status.NoFoil: "No foil detected",
  ALPS5000Status.Error: "Error",
  ALPS5000Status.Busy: "Busy",
  ALPS5000Status.WaitingForSealTemperature: "Waiting for seal temperature",
  ALPS5000Status.PlateNotPresent: "Plate not present in shuttle",
  ALPS5000Status.NotInitialised: "Not initialised",
  ALPS5000Status.ForceSensorActivated: "Force sensor is activated",
  ALPS5000Status.PlatePresent: "Plate present in shuttle",
}

# Error codes returned by the ``E`` command.
ERRORS = {
  1: "Down error",
  2: "Up error",
  3: "Shuttle not in",
  4: "Cutter Error",
  5: "Thermocouple error - ambient temperature may be too low",
  6: "Sealer overheating",
  7: "No foil detected",
  8: "No plate detected",
  9: "Force sensor error",
  10: "Engager Error",
  11: "Gripper Error",
  12: "Foil Clamp",
  13: "Dropped Foil",
}

MIN_SEALING_FORCE = 5
MAX_SEALING_FORCE = 50
MIN_FOIL_LENGTH = 119
MAX_FOIL_LENGTH = 128
MIN_SEALING_DISTANCE = 10
MAX_SEALING_DISTANCE = 50

# Status bits tolerated while waiting for the sealer to accept an operation.
_SETUP_MASK = (
  ALPS5000Status.WaitingForSealTemperature
  | ALPS5000Status.PlateNotPresent
  | ALPS5000Status.PlatePresent
)
_READY_MASK = ALPS5000Status.WaitingForSealTemperature | ALPS5000Status.PlatePresent

# Time the instrument needs to complete its initialisation routine.
_INIT_SECONDS = 10.0


class ThermoScientificALPS5000(ThermoScientificALPSSealer):
  """Thermo Scientific ALPS 5000 heat sealer.

  Extends the shared ALPS protocol with initialisation, force-sensor control,
  foil length, sealing distance, and shuttle movement:
    I            initialise the instrument; replies ok or err
    PS={f:02d}   set sealing force, 5..50; replies ok
    FS=1 / FS=0  enable / disable the force sensor; replies ok or err
    L={n:03d}    set foil length, 119..128; replies ok
    DO={n:02d}   set sealing distance, 10..50; replies ok
    SI / SO      move the shuttle in / out; replies ok or err

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning
  is emitted at setup.
  """

  _HUMAN_READABLE_NAME = "Thermo Scientific ALPS 5000 Heat Sealer"
  STATUS = ALPS5000Status
  STATUS_MESSAGES = STATUS_MESSAGES
  ERRORS = ERRORS

  async def setup(self) -> None:
    await self._open()
    await self.wait_for_idle(_SETUP_MASK)
    await self.initialise()
    await asyncio.sleep(_INIT_SECONDS)
    await self.wait_for_idle(_SETUP_MASK)
    await self.set_temperature(self.preheating_temperature)

  async def seal(
    self,
    temperature: int = 160,
    duration: float = 2.5,
    use_force_sensor: bool = False,
  ) -> None:
    """Seal a plate.

    Args:
      temperature: sealing temperature in degrees C (5..199).
      duration: sealing time in seconds (0.5..9.9).
      use_force_sensor: enable the force sensor for this seal.
    """
    logger.info(
      "[ALPS5000 %s] sealing at %d C for %.1fs (force sensor %s)",
      self.io.port,
      temperature,
      duration,
      "on" if use_force_sensor else "off",
    )
    await self.wait_for_idle(_READY_MASK)
    await self.set_sealing_time(duration)
    await self.set_temperature(temperature)
    await self.enable_force_sensor(use_force_sensor)
    await self.wait_for_sealing_temperature()
    reply = await self.send_command("S")
    if reply != "ok":
      raise ThermoScientificALPSError(title="Seal command failed", message=f"S returned {reply!r}")
    await self.wait_for_idle(_READY_MASK)

  # === Extra commands ===

  async def initialise(self) -> None:
    """Run the instrument initialisation routine (``I``)."""
    reply = await self.send_command("I")
    if reply != "ok":
      raise ThermoScientificALPSError(
        title="Initialisation failed", message=f"I returned {reply!r}"
      )

  async def set_force(self, force: int) -> None:
    """Set the sealing force (``PS``, 5..50)."""
    if not MIN_SEALING_FORCE <= force <= MAX_SEALING_FORCE:
      raise ValueError(f"force must be {MIN_SEALING_FORCE}..{MAX_SEALING_FORCE}")
    command = f"PS={force:02d}"
    reply = await self.send_command(command)
    if reply != "ok":
      raise ThermoScientificALPSError(
        title="Setting sealing force failed", message=f"force {force} rejected with {reply!r}"
      )

  async def enable_force_sensor(self, enable: bool) -> None:
    """Enable or disable the force sensor (``FS=1`` / ``FS=0``)."""
    command = "FS=1" if enable else "FS=0"
    reply = await self.send_command(command)
    if reply != "ok":
      raise ThermoScientificALPSError(
        title="Setting force sensor failed", message=f"{command} returned {reply!r}"
      )

  async def set_foil_length(self, length: int) -> None:
    """Set the foil length (``L``, 119..128)."""
    if not MIN_FOIL_LENGTH <= length <= MAX_FOIL_LENGTH:
      raise ValueError(f"foil length must be {MIN_FOIL_LENGTH}..{MAX_FOIL_LENGTH}")
    command = f"L={length:03d}"
    reply = await self.send_command(command)
    if reply != "ok":
      raise ThermoScientificALPSError(
        title="Setting foil length failed", message=f"length {length} rejected with {reply!r}"
      )

  async def set_sealing_distance(self, distance: int) -> None:
    """Set the sealing distance (``DO``, 10..50)."""
    if not MIN_SEALING_DISTANCE <= distance <= MAX_SEALING_DISTANCE:
      raise ValueError(f"distance must be {MIN_SEALING_DISTANCE}..{MAX_SEALING_DISTANCE}")
    command = f"DO={distance:02d}"
    reply = await self.send_command(command)
    if reply != "ok":
      raise ThermoScientificALPSError(
        title="Setting sealing distance failed",
        message=f"distance {distance} rejected with {reply!r}",
      )

  async def shuttle_in(self) -> None:
    """Move the shuttle in (``SI``)."""
    reply = await self.send_command("SI")
    if reply != "ok":
      raise ThermoScientificALPSError(title="Shuttle in failed", message=f"SI returned {reply!r}")

  async def shuttle_out(self) -> None:
    """Move the shuttle out (``SO``)."""
    reply = await self.send_command("SO")
    if reply != "ok":
      raise ThermoScientificALPSError(title="Shuttle out failed", message=f"SO returned {reply!r}")

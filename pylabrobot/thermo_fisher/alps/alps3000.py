import enum
from typing import Dict

from pylabrobot.thermo_fisher.alps.sealer import ThermoScientificALPSSealer


class ALPS3000Status(enum.IntFlag):
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
  LowAir = 0x20
  FoilLoadModeAfterPark = 0x40
  ParkMode = 0x80


STATUS_MESSAGES: Dict[enum.IntFlag, str] = {
  ALPS3000Status.NoFoil: "No foil detected",
  ALPS3000Status.Error: "Error",
  ALPS3000Status.Busy: "Busy",
  ALPS3000Status.WaitingForSealTemperature: "Waiting for seal temperature",
  ALPS3000Status.PlateNotPresent: "Plate not present in shuttle",
  ALPS3000Status.LowAir: "Low air pressure detected",
  ALPS3000Status.FoilLoadModeAfterPark: "Waiting for foil to be loaded",
  ALPS3000Status.ParkMode: "Park mode",
}

# Error codes returned by the ``E`` command. The firmware leaves gaps in the
# numbering.
ERRORS = {
  1: "Down error",
  2: "Up error",
  3: "Shuttle not in",
  4: "Shuttle not out",
  7: "Thermocouple error - ambient temperature may be too low",
  9: "Sealer overheating",
  10: "No foil detected",
  13: "Mains Air error",
}

# Status bits tolerated while waiting for the sealer to accept an operation.
_READY_MASK = ALPS3000Status.WaitingForSealTemperature | ALPS3000Status.PlateNotPresent


class ThermoScientificALPS3000(ThermoScientificALPSSealer):
  """Thermo Scientific ALPS 3000 heat sealer.

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning
  is emitted at setup.
  """

  _HUMAN_READABLE_NAME = "Thermo Scientific ALPS 3000 Heat Sealer"
  STATUS = ALPS3000Status
  STATUS_MESSAGES = STATUS_MESSAGES
  ERRORS = ERRORS

  async def setup(self) -> None:
    await self._open()
    await self.wait_for_idle(_READY_MASK)
    await self.set_temperature(self.preheating_temperature)

  async def seal(self, temperature: int = 160, duration: float = 2.5) -> None:
    """Seal a plate.

    Args:
      temperature: sealing temperature in degrees C (5..199).
      duration: sealing time in seconds (0.5..9.9).
    """
    await self._seal(temperature, duration, _READY_MASK)

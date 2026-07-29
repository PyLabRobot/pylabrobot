import enum
from typing import Dict

from pylabrobot.thermo_fisher.alps.sealer import ThermoScientificALPSSealer


class ALPS300Status(enum.IntFlag):
  """Status byte returned by the ``?`` command (two hex digits).

  ``Ready`` (0x00) is the all-clear value; any other bit is a condition that
  must be cleared, or masked when ignorable, before an operation runs.
  """

  Ready = 0x00
  Busy = 0x01
  Error = 0x02
  Sealing = 0x04
  NotAtSealTemperature = 0x08
  NotAssigned = 0x10
  FoilLow = 0x20
  DoorOpen = 0x40
  ProceedOverridden = 0x80


STATUS_MESSAGES: Dict[enum.IntFlag, str] = {
  ALPS300Status.Busy: "Busy",
  ALPS300Status.Error: "Error",
  ALPS300Status.Sealing: "Sealing",
  ALPS300Status.NotAtSealTemperature: "Waiting for seal temperature",
  ALPS300Status.NotAssigned: "Status not assigned",
  ALPS300Status.FoilLow: "No foil detected or foil low.",
  ALPS300Status.DoorOpen: "Door open",
  ALPS300Status.ProceedOverridden: "Proceed overriden",
}

# Error codes returned by the ``E`` command. The firmware leaves gaps in the
# numbering (there is no code 2).
ERRORS = {
  1: "Low air pressure detected. Check air pressure.",
  3: "Shuttle not out.",
  4: "No foil or low foil error. Check foil and foil sensor.",
  5: "Seal transfer error.",
  6: "Placement error (can be caused of low air pressure).",
  7: "Thermocouple error - ambient temperature may be too low.",
}

# Status bits tolerated while waiting for the sealer to accept an operation.
_READY_MASK = (
  ALPS300Status.NotAtSealTemperature | ALPS300Status.FoilLow | ALPS300Status.ProceedOverridden
)
_SETUP_MASK = ALPS300Status.NotAtSealTemperature | ALPS300Status.ProceedOverridden


class ThermoScientificALPS300(ThermoScientificALPSSealer):
  """Thermo Scientific ALPS 300 heat sealer.

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning
  is emitted at setup.
  """

  _HUMAN_READABLE_NAME = "Thermo Scientific ALPS 300 Heat Sealer"
  STATUS = ALPS300Status
  STATUS_MESSAGES = STATUS_MESSAGES
  ERRORS = ERRORS

  async def setup(self) -> None:
    await self._open()
    await self.wait_for_idle(_SETUP_MASK)
    await self.set_temperature(self.preheating_temperature)

  async def seal(self, temperature: int = 160, duration: float = 2.5) -> None:
    """Seal a plate.

    Args:
      temperature: sealing temperature in degrees C (5..199).
      duration: sealing time in seconds (0.5..9.9).
    """
    await self._seal(temperature, duration, _READY_MASK)

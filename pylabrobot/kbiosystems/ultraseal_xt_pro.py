import enum
import logging
from typing import Dict

from pylabrobot.kbiosystems.sealer import KBiosystemsError, KBiosystemsSealer

logger = logging.getLogger(__name__)

__all__ = ["KBiosystemsUltrasealXTPro", "UltrasealXTProStatus", "KBiosystemsError"]


class UltrasealXTProStatus(enum.IntFlag):
  """Status byte returned by the ``?`` command (two hex digits).

  ``Ready`` (0x00) is the all-clear value; any other bit is a condition that
  must be cleared, or masked when ignorable, before an operation runs. The low
  bits (Ready/Error/Busy/NotAtSealTemperature) are the shared set
  :class:`KBiosystemsSealer` acts on; ``LowAir`` and ``ParkMode`` are specific to
  this model.
  """

  Ready = 0x00
  NoFoil = 0x01
  Error = 0x02
  Busy = 0x04
  NotAtSealTemperature = 0x08
  PlateNotPresent = 0x10
  LowAir = 0x20
  ParkMode = 0x40


# Text for each status bit, used to describe a not-ready condition.
STATUS_MESSAGES: Dict[enum.IntFlag, str] = {
  UltrasealXTProStatus.NoFoil: "No foil detected.",
  UltrasealXTProStatus.Error: "Error.",
  UltrasealXTProStatus.Busy: "Device busy.",
  UltrasealXTProStatus.NotAtSealTemperature: "Waiting for seal temperature.",
  UltrasealXTProStatus.PlateNotPresent: "No plate detected.",
  UltrasealXTProStatus.LowAir: "Low air pressure - check the compressed air supply.",
  UltrasealXTProStatus.ParkMode: "Shuttle parked (inside).",
}

# Error codes returned by the ``E`` command (two decimal digits).
DEVICE_ERRORS = {
  1: "Vertical shuttle down.",
  2: "Heater up.",
  3: "Shuttle in.",
  4: "Shuttle out.",
  7: "Thermocouple error.",
  8: "Heater failure.",
  9: "The sealer is overheating.",
  10: "No foil detected.",
  11: "No plate detected.",
  12: "Low air pressure.",
  13: "Door error.",
}

MIN_SEALING_TEMPERATURE = 25
MAX_SEALING_TEMPERATURE = 199
MIN_SEALING_DURATION = 0.5
MAX_SEALING_DURATION = 9.9


class KBiosystemsUltrasealXTPro(KBiosystemsSealer):
  """KBiosystems Ultraseal XT Pro heat sealer.

  An inline, pneumatically driven heat sealer that advances and cuts its own
  film internally, so it exposes no foil-length/force/distance parameters. A
  plate is presented by moving the shuttle out (unparking); after the plate is
  loaded, the shuttle is moved in (parked) and the plate is sealed.

  Commands (in addition to the shared ``?``/``E``/``S``/``A``/``B``/``C``/
  ``D``/``F``, see :class:`KBiosystemsSealer`):
    P            park the shuttle (move in); replies ok or err
    U            unpark the shuttle (move out); replies ok or err
    R            reset the sealer; replies ok

  Requires a compressed air supply (see the ``LowAir`` status bit).

  Not verified: has NOT been tested against hardware in PyLabRobot. A warning is
  emitted at setup.
  """

  _HUMAN_READABLE_NAME = "KBiosystems Ultraseal XT Pro Heat Sealer"

  STATUS = UltrasealXTProStatus
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
    offline_temperature: int = 25,
  ) -> None:
    super().__init__(
      port,
      timeout=timeout,
      settle_time=settle_time,
      preheating_temperature=preheating_temperature,
      offline_temperature=offline_temperature,
    )

  async def setup(self) -> None:
    await self._open()
    await self.wait_for_idle(
      UltrasealXTProStatus.NoFoil
      | UltrasealXTProStatus.NotAtSealTemperature
      | UltrasealXTProStatus.ParkMode
    )
    await self.set_temperature(self.preheating_temperature)
    logger.info("[Ultraseal XT Pro %s] connected", self.io.port)

  # === Shuttle ===

  async def park(self) -> bool:
    """Park the shuttle / move it in (``P``). Returns True on ``ok``."""
    return self._check(await self.send_command("P"), {"ok", "err"}, "P") == "ok"

  async def unpark(self) -> bool:
    """Unpark the shuttle / move it out (``U``). Returns True on ``ok``."""
    return self._check(await self.send_command("U"), {"ok", "err"}, "U") == "ok"

  async def reset(self) -> bool:
    """Reset the sealer (``R``). Returns True on ``ok``."""
    return await self.send_command("R") == "ok"

  async def move_shuttle_out(self) -> None:
    """Present a plate: unpark the shuttle if needed, then wait until ready.

    ``NoFoil`` and ``NotAtSealTemperature`` are tolerated so a plate can be
    loaded while the film is being advanced or the heater is warming.
    """
    status = await self._wait_for_busy_cleared(timeout=30.0)
    if status & UltrasealXTProStatus.ParkMode and not status & UltrasealXTProStatus.Busy:
      await self.unpark()
    await self.wait_for_idle(
      UltrasealXTProStatus.NoFoil | UltrasealXTProStatus.NotAtSealTemperature
    )

  async def move_shuttle_in(self) -> None:
    """Retract a plate for sealing: park the shuttle if needed, then wait."""
    status = await self._wait_for_busy_cleared(timeout=30.0)
    if not status & UltrasealXTProStatus.ParkMode and not status & UltrasealXTProStatus.Busy:
      await self.park()
    await self.wait_for_idle(
      UltrasealXTProStatus.NoFoil
      | UltrasealXTProStatus.NotAtSealTemperature
      | UltrasealXTProStatus.ParkMode
    )

  # === Operations ===

  async def seal(
    self,
    temperature: int,
    duration: float,
    idle_temperature: int = 100,
  ) -> None:
    """Seal the plate currently in the sealer.

    Waits for the device to be ready, applies the time and temperature, waits
    for the heater to reach the setpoint, seals, then returns to
    ``idle_temperature``. The shuttle must already be in (see
    :meth:`move_shuttle_in`); film advance and cutting are handled by the device.

    Args:
      temperature: sealing temperature in degrees C (25..199).
      duration: sealing time in seconds (0.5..9.9).
      idle_temperature: temperature to hold after sealing (25..199).
    """
    logger.info(
      "[Ultraseal XT Pro %s] sealing at %d C for %.1fs", self.io.port, temperature, duration
    )
    ignore = UltrasealXTProStatus.NotAtSealTemperature | UltrasealXTProStatus.ParkMode

    await self.wait_for_idle(ignore)
    await self.set_sealing_time(duration)
    await self.set_temperature(temperature)
    await self.wait_for_sealing_temperature()
    if self._check(await self.send_command("S"), {"ok", "err"}, "S") != "ok":
      raise KBiosystemsError(title="Seal command returned 'err'")
    await self.wait_for_idle(ignore)

    await self.set_temperature(idle_temperature)

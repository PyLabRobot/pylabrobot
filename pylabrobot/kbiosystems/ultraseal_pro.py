import enum
import logging
from typing import Dict

from pylabrobot.kbiosystems.sealer import KBiosystemsError, KBiosystemsSealer

logger = logging.getLogger(__name__)

__all__ = ["KBiosystemsUltrasealPRO", "UltrasealPROStatus", "KBiosystemsError"]


class UltrasealPROStatus(enum.IntFlag):
  """Status byte returned by the ``?`` command (two hex digits).

  ``Ready`` (0x00) is the all-clear value; any other bit is a condition that
  must be cleared, or masked when ignorable, before an operation runs. The low
  bits (Ready/Error/Busy/NotAtSealTemperature) are the shared set
  :class:`KBiosystemsSealer` acts on.

  The Ultraseal PRO differs from the Ultraseal XT Pro in the top of the byte:
  ``ParkMode`` is bit 7 (0x80) with bit 6 (0x40) a documented spare, whereas the
  XT Pro reports ``ParkMode`` at 0x40. ``Spare`` is kept as a member so decoding
  a byte with that bit set does not raise.
  """

  Ready = 0x00
  NoFoil = 0x01
  Error = 0x02
  Busy = 0x04
  NotAtSealTemperature = 0x08
  PlateNotPresent = 0x10
  LowAir = 0x20
  Spare = 0x40
  ParkMode = 0x80


# Text for each status bit, used to describe a not-ready condition.
STATUS_MESSAGES: Dict[enum.IntFlag, str] = {
  UltrasealPROStatus.NoFoil: "No foil detected.",
  UltrasealPROStatus.Error: "Error.",
  UltrasealPROStatus.Busy: "Device busy.",
  UltrasealPROStatus.NotAtSealTemperature: "Waiting for seal temperature.",
  UltrasealPROStatus.PlateNotPresent: "No plate detected.",
  UltrasealPROStatus.LowAir: "Low air pressure - check the compressed air supply.",
  UltrasealPROStatus.ParkMode: "Shuttle parked (inside).",
}

# Error codes returned by the ``E`` command (two decimal digits).
DEVICE_ERRORS = {
  1: "Vertical shuttle down.",
  2: "Heater up.",
  3: "Shuttle in.",
  4: "Shuttle out.",
  7: "Thermocouple error - ambient temperature may be too low.",
  9: "The sealer is overheating.",
  10: "No foil detected.",
}

MIN_SEALING_TEMPERATURE = 25
MAX_SEALING_TEMPERATURE = 200
MIN_SEALING_DURATION = 0.5
MAX_SEALING_DURATION = 9.9


class KBiosystemsUltrasealPRO(KBiosystemsSealer):
  """KBiosystems Ultraseal PRO heat sealer (formerly the WASP).

  An inline, pneumatically driven heat sealer that advances and cuts its own
  film internally. A plate is placed on the shuttle while it is extended; the
  seal command draws the shuttle in, seals, and returns it automatically, so -
  unlike the Ultraseal XT Pro - the Ultraseal PRO exposes no shuttle
  park/unpark/reset commands over serial. The system initializes on power up
  (reporting ``Busy`` while it does), so there is no initialize command either.

  Commands (in addition to the shared ``?``/``E``/``S``/``A``/``B``/``C``/
  ``D``/``F``, see :class:`KBiosystemsSealer`): none. The Ultraseal PRO host
  protocol is exactly the shared command set.

  Requires a compressed air supply (see the ``LowAir`` status bit).

  Verified against hardware.
  """

  _HUMAN_READABLE_NAME = "KBiosystems Ultraseal PRO Heat Sealer"

  STATUS = UltrasealPROStatus
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
      UltrasealPROStatus.NoFoil
      | UltrasealPROStatus.NotAtSealTemperature
      | UltrasealPROStatus.ParkMode
    )
    await self.set_temperature(self.preheating_temperature)
    logger.info("[Ultraseal PRO %s] connected", self.io.port)

  # === Operations ===

  async def seal(
    self,
    temperature: int,
    duration: float,
    idle_temperature: int = 100,
  ) -> None:
    """Seal the plate currently on the shuttle.

    Waits for the device to be ready, applies the time and temperature, waits
    for the heater to reach the setpoint, seals, then returns to
    ``idle_temperature``. The Ultraseal PRO draws the shuttle in, seals, and
    returns it automatically; film advance and cutting are handled by the device.

    Args:
      temperature: sealing temperature in degrees C (25..200).
      duration: sealing time in seconds (0.5..9.9).
      idle_temperature: temperature to hold after sealing (25..200).
    """
    logger.info("[Ultraseal PRO %s] sealing at %d C for %.1fs", self.io.port, temperature, duration)
    ignore = UltrasealPROStatus.NotAtSealTemperature | UltrasealPROStatus.ParkMode

    await self.wait_for_idle(ignore)
    await self.set_sealing_time(duration)
    await self.set_temperature(temperature)
    await self.wait_for_sealing_temperature()
    if self._check(await self.send_command("S"), {"ok", "err"}, "S") != "ok":
      raise KBiosystemsError(title="Seal command returned 'err'")
    await self.wait_for_idle(ignore)

    await self.set_temperature(idle_temperature)

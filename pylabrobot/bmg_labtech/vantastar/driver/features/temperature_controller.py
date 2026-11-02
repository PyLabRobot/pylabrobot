"""The temperature controller: the incubator's heating and its temperature sensors."""

import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
  from ..master import VANTAstarDriver

logger = logging.getLogger(__name__)

# IncubatorCommand types: heat to the nominal temperature, or monitor only (how Voyager switches
# heating off).
_INCUBATOR_ON = 0x01
_INCUBATOR_MONITOR = 0x02


class TemperatureController:
  """The temperature controller (IncubatorCommand).

  Reached as ``driver.temperature_controller``.  The reader holds one setting at a time (heating to
  a target, or monitoring only), so the target is kept here.

  Attributes:
    target_temperature: the temperature it heats to, in degrees C; None while it does not heat.
  """

  default_temperature_tolerance: float = 0.5
  default_temperature_timeout: float = 600.0
  # Seconds to wait for the sensors to report: the reader reports no temperatures for up to about a
  # second after an IncubatorCommand.
  default_sensor_timeout: float = 5.0

  def __init__(self, driver: "VANTAstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver
    self.target_temperature: Optional[float] = None

  async def measure_temperature(self, timeout: Optional[float] = None) -> float:
    """The incubator temperature Voyager displays (the first of the reader's three sensors), in
    degrees C.

    Args:
      timeout: seconds to wait for the sensors to report; ``default_sensor_timeout`` when None.

    Raises:
      TimeoutError: if the sensors do not report within ``timeout``.
    """
    status = await self._driver._wait_until_ready(
      self.default_sensor_timeout if timeout is None else timeout,
      self._driver.default_poll_interval,
      until=lambda s: s.temperatures is not None,
    )
    assert status.temperatures is not None
    return status.temperatures[0]

  async def start_temperature_control(self, temperature: float) -> None:
    """Start heating to ``temperature``, in degrees C, and hold it; returns once the sensors report
    again.

    The reader refuses a temperature outside its range (``CommandRefusedError``, quit code 110); the
    tested reader's range is 25-45 C.

    Raises:
      TimeoutError: if the sensors do not report ``default_sensor_timeout`` seconds after the
        command.
    """
    await self._driver._set_incubator(_INCUBATOR_ON, temperature)
    self.target_temperature = temperature
    await self.measure_temperature()
    logger.info("temperature control started: %.1f C", temperature)

  async def stop_temperature_control(self) -> None:
    """Stop heating; the sensors keep reporting (as Voyager switches heating off).  Returns once
    they report again.

    Raises:
      TimeoutError: as for ``start_temperature_control``.
    """
    await self._driver._set_incubator(_INCUBATOR_MONITOR, 0)
    self.target_temperature = None
    await self.measure_temperature()
    logger.info("temperature control stopped; the sensors keep reporting")

  async def wait_for_temperature(
    self, *, tolerance: Optional[float] = None, timeout: Optional[float] = None
  ) -> float:
    """Wait until the temperature (``measure_temperature``) is within ``tolerance`` of the target;
    return it.

    Args:
      tolerance: degrees C; ``default_temperature_tolerance`` when None.
      timeout: seconds; ``default_temperature_timeout`` when None.

    Raises:
      RuntimeError: if no target temperature is set.
      TimeoutError: if it is not reached within ``timeout``.
    """
    if self.target_temperature is None:
      raise RuntimeError("no target temperature set; call `start_temperature_control` first")
    target = self.target_temperature
    tolerance = self.default_temperature_tolerance if tolerance is None else tolerance
    timeout = self.default_temperature_timeout if timeout is None else timeout
    start = time.monotonic()
    status = await self._driver._wait_until_ready(
      timeout,
      self._driver.default_poll_interval,
      until=lambda s: s.temperatures is not None and abs(s.temperatures[0] - target) <= tolerance,
    )
    assert status.temperatures is not None
    logger.info(
      "%.1f C reached (target %.1f C) after %.0f s",
      status.temperatures[0],
      target,
      time.monotonic() - start,
    )
    return status.temperatures[0]

"""The temperature controller: heats the plate chamber from below and above."""

import logging
from typing import TYPE_CHECKING, Literal, Optional, Tuple

import anyio

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver, CLARIOstarStatus

logger = logging.getLogger(__name__)

# TEMPERATURE_CONTROLLER parameters: off, and monitoring without heating.
_OFF = 0x0000
_MONITOR = 0x0001

# Setpoint range in degrees C. 20.0 is refused (quit code 110).
_TEMPERATURE_RANGE = (25.0, 45.0)
_EXTENDED_TEMPERATURE_RANGE = (10.0, 65.0)


class TemperatureController:
  """The temperature controller (TEMPERATURE_CONTROLLER command family, 0x06).

  Reached as `driver.temperature_controller`, built at setup only if the reader reports one. The
  reader holds one setting at a time (off, monitoring, or a setpoint), so the target is kept here.
  """

  default_temperature_tolerance: float = 0.5
  # Degrees C a new target may sit below the current temperature without a warning: the heater
  # overshoots its target.
  default_overshoot_tolerance: float = 0.5
  default_temperature_timeout: float = 600.0
  default_sensor_timeout: float = 3.0
  default_poll_interval: float = 1.0

  def __init__(self, driver: "CLARIOstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver
    self.target_temperature: Optional[float] = None

  def _get_temperature_range(self) -> Tuple[float, float]:
    """The setpoint range, in degrees C: wider with the extended temperature range."""
    extended = self._driver.configuration.extended_temperature_range
    return _EXTENDED_TEMPERATURE_RANGE if extended else _TEMPERATURE_RANGE

  async def _unchecked_fw_set(self, value: int) -> None:
    """Send TEMPERATURE_CONTROLLER with a setpoint in 0.1 C, or `_OFF` / `_MONITOR`."""
    await self._driver.send_command(
      self._driver.CommandFamily.TEMPERATURE_CONTROLLER, parameters=value.to_bytes(2, "big")
    )

  async def _start_monitoring_if_off(self, status: "CLARIOstarStatus") -> None:
    """Start the sensors if `status` has them off and no target is set: monitoring replaces a
    setpoint."""
    sensors_off = status.temperature_bottom is None or status.temperature_top is None
    if sensors_off and self.target_temperature is None:
      await self._unchecked_fw_set(_MONITOR)
      logger.info("temperature monitoring started")

  async def start_monitoring(self) -> None:
    """Start the sensors without heating, unless they already report or a target is set."""
    await self._start_monitoring_if_off(await self._driver.request_status())

  async def _warn_if_below_current(self, temperature: float) -> None:
    """Warn if `temperature` is below the bottom plate's: the reader cannot cool."""
    current = await self.measure_temperature(sensor="bottom")
    if temperature < current - self.default_overshoot_tolerance:
      logger.warning(
        "target %.1f C is below the bottom plate's %.1f C; the reader has no cooling and reaches "
        "it only if the room cools it",
        temperature,
        current,
      )

  async def start_temperature_control(self, temperature: float) -> None:
    """Start heating to `temperature`, in degrees C, and hold it.

    Warns if `temperature` is more than `default_overshoot_tolerance` below the bottom plate's.

    Raises:
      ValueError: if the temperature is outside the temperature controller's range.
    """
    lowest, highest = self._get_temperature_range()
    if not lowest <= temperature <= highest:
      raise ValueError(f"temperature must be within {lowest} to {highest} C, is {temperature}")
    await self._warn_if_below_current(temperature)
    # TODO(device): no accepted setpoint is recorded yet (reader tests 2 and 5).
    await self._unchecked_fw_set(round(temperature * 10))
    self.target_temperature = temperature
    logger.info("temperature control started: %.1f C", temperature)

  async def stop_temperature_control(self) -> None:
    """Stop heating; the sensors keep reporting."""
    await self._unchecked_fw_set(_MONITOR)
    self.target_temperature = None
    logger.info("temperature control stopped; the sensors keep reporting")

  async def stop_monitoring(self) -> None:
    """Stop heating and the sensors."""
    await self._unchecked_fw_set(_OFF)
    self.target_temperature = None
    logger.info("temperature control and monitoring stopped")

  async def measure_temperature(
    self, sensor: Literal["bottom", "top", "mean"] = "mean", timeout: Optional[float] = None
  ) -> float:
    """The measured temperature, in degrees C; starts monitoring if the sensors are off.

    While heating it only waits for the sensors: the reader holds one setting at a time, so
    monitoring would replace the setpoint.

    Args:
      sensor: the bottom or top heating plate's sensor, or their mean.
      timeout: seconds to wait for the sensors to report; `default_sensor_timeout` when None.

    Raises:
      TimeoutError: if the sensors do not report within `timeout`.
    """
    timeout = self.default_sensor_timeout if timeout is None else timeout
    status = await self._driver.request_status()
    await self._start_monitoring_if_off(status)
    deadline = anyio.current_time() + timeout
    while status.temperature_bottom is None or status.temperature_top is None:
      if anyio.current_time() >= deadline:
        raise TimeoutError(f"the temperature sensors did not report within {timeout} s")
      await anyio.sleep(self._driver.default_poll_interval)
      status = await self._driver.request_status()
    if sensor == "bottom":
      return status.temperature_bottom
    if sensor == "top":
      return status.temperature_top
    return (status.temperature_bottom + status.temperature_top) / 2

  async def wait_for_temperature(
    self, *, tolerance: Optional[float] = None, timeout: Optional[float] = None
  ) -> float:
    """Wait until the mean temperature is within `tolerance` of the target; return it.

    Args:
      tolerance: degrees C. `default_temperature_tolerance` when None.
      timeout: seconds. `default_temperature_timeout` when None.

    Raises:
      RuntimeError: if no target temperature is set.
      TimeoutError: if it is not reached within `timeout`.
    """
    if self.target_temperature is None:
      raise RuntimeError("no target temperature set; call `start_temperature_control` first")
    tolerance = self.default_temperature_tolerance if tolerance is None else tolerance
    timeout = self.default_temperature_timeout if timeout is None else timeout
    deadline = anyio.current_time() + timeout
    while True:
      temperature = await self.measure_temperature()
      if abs(temperature - self.target_temperature) <= tolerance:
        logger.info("%.1f C reached (target %.1f C)", temperature, self.target_temperature)
        return temperature
      if anyio.current_time() >= deadline:
        raise TimeoutError(
          f"{temperature} C after {timeout} s, target {self.target_temperature} +/- {tolerance}"
        )
      await anyio.sleep(self.default_poll_interval)

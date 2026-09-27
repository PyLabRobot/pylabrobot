"""Hamilton Prep heater shaker: heats and shakes the plate on it, and locks it in place.

TODO(device) marks what only a first run with a plate on it can settle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional, Tuple

from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.coordinate import Coordinate

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver

logger = logging.getLogger(__name__)

# The deck spot it stands in, in place of the spot's clips and pedestal.
HEATER_SHAKER_SPOT = "spot_0_3"
# Its footprint, in mm, and where on it a plate's front left corner goes: the flat bottom adapter's.
HEATER_SHAKER_SIZE = (145.5, 104.0)
HEATER_SHAKER_PLATE_XY = (9.45, 9.25)
# Where a plate on it sits: this far above the deck, in mm.
HEATER_SHAKER_PLATE_Z = 30.5
# Its front left corner from the spot's.
HEATER_SHAKER_FROM_SPOT = Coordinate(-9.0, 8.0, 0.0)

Direction = Literal["clockwise", "counter_clockwise"]

# TODO(device): which of the firmware's Forward and Reverse turns clockwise, seen from above.
_DIRECTIONS = {
  "clockwise": PrepCmd.ShakeDirection.Forward,
  "counter_clockwise": PrepCmd.ShakeDirection.Reverse,
}


@dataclass
class HeaterShakerConfiguration:
  """What the heater shaker answered about itself. None until `discover` has read it."""

  firmware_version: Optional[str] = None
  temperature_range: Optional[Tuple[float, float]] = None
  maximum_supervision_tolerance: Optional[float] = None
  speed_range: Optional[Tuple[float, float]] = None
  acceleration_range: Optional[Tuple[float, float]] = None


class PrepHamiltonHeaterShaker:
  """The heater shaker. Built at setup, as `prep.driver.hs`, if the device has one."""

  default_shaking_acceleration: float = 1000.0
  default_shaking_direction: Direction = "clockwise"
  default_leave_locked: bool = True
  # TODO(device): the unit and meaning of the supervision timeout, and sensible defaults.
  default_supervision_timeout: int = 3600
  default_supervision_tolerance: float = 5.0
  default_temperature_tolerance: float = 0.5
  default_temperature_timeout: float = 600.0
  default_poll_interval: float = 1.0

  def __init__(
    self, driver: "PrepDriver", configuration: Optional[HeaterShakerConfiguration] = None
  ) -> None:
    """
    Args:
      driver: the driver to send commands through.
      configuration: facts to start from; `discover` fills in what the device answers.
    """
    self._driver = driver
    self.configuration = configuration or HeaterShakerConfiguration()
    self.target_temperature: Optional[float] = None
    # The deck spot it stands in, as the driver models it at setup, or None without a Prep deck.
    self.resource: Optional[PlateHolder] = None

  # ----------------------------------------
  # Checks
  # ----------------------------------------

  @staticmethod
  def _check_in_range(name: str, value: float, allowed: Optional[Tuple[float, float]]) -> None:
    """Raise if `value` lies outside `allowed`.

    Raises:
      RuntimeError: If the range has not been read; call `discover` first.
      ValueError: If the value is outside it, naming both.
    """
    if allowed is None:
      raise RuntimeError(f"the {name} range has not been read; call `discover` first")
    lowest, highest = allowed
    if not lowest <= value <= highest:
      raise ValueError(f"{name} must be within {lowest} to {highest}, is {value}")

  def _check_supervision_tolerance(self, tolerance: float) -> None:
    """Raise if `tolerance` is outside 0 to the device's maximum.

    Raises:
      RuntimeError: If the maximum has not been read.
      ValueError: If it is outside.
    """
    maximum = self.configuration.maximum_supervision_tolerance
    self._check_in_range(
      "supervision tolerance", tolerance, None if maximum is None else (0.0, maximum)
    )

  # ----------------------------------------
  # Firmware
  # ----------------------------------------

  async def _unchecked_fw_initialize(self, smart: bool) -> None:
    """Send Shaker.Initialize."""
    await self._driver.send_command(PrepCmd.PrepHHSInitialize(smart=smart))

  async def _unchecked_fw_move_plate_lock(self, locked: bool) -> None:
    """Send Shaker.SetPlateLockState."""
    await self._driver.send_command(PrepCmd.PrepHHSSetPlateLockState(locked=locked))

  async def _unchecked_fw_start_shaking(
    self,
    speed: float,
    shaking_time: int,
    leave_locked: bool,
    direction: Direction,
    acceleration: float,
  ) -> None:
    """Send Shaker.EnableShaking (continuous)."""
    await self._driver.send_command(
      PrepCmd.PrepHHSEnableShaking(
        shaking_speed=speed,
        shaking_time=shaking_time,
        leave_locked=leave_locked,
        direction=_DIRECTIONS[direction],
        acceleration=acceleration,
      )
    )

  async def _unchecked_fw_start_periodic_shaking(
    self,
    speed: float,
    period: int,
    on_time: int,
    shaking_time: int,
    leave_locked: bool,
    direction: Direction,
    acceleration: float,
  ) -> None:
    """Send Shaker.EnableShaking (periodic)."""
    await self._driver.send_command(
      PrepCmd.PrepHHSEnablePeriodicShaking(
        shaking_speed=speed,
        shaking_period=period,
        shaking_on_time=on_time,
        shaking_time=shaking_time,
        leave_locked=leave_locked,
        direction=_DIRECTIONS[direction],
        acceleration=acceleration,
      )
    )

  async def _unchecked_fw_stop_shaking(self) -> None:
    """Send Shaker.DisableShaking."""
    await self._driver.send_command(PrepCmd.PrepHHSDisableShaking())

  async def _unchecked_fw_start_temperature_control(
    self, temperature: float, wait_until_reached: bool, timeout: int, tolerance: float
  ) -> None:
    """Send Heater.StartHeating."""
    await self._driver.send_command(
      PrepCmd.PrepHHSStartHeating(
        target_temperature=temperature,
        wait_for_temperature_to_be_reached=wait_until_reached,
        supervision_timeout=timeout,
        supervision_tolerance=tolerance,
      )
    )

  async def _unchecked_fw_stop_temperature_control(self) -> None:
    """Send Heater.StopHeating."""
    await self._driver.send_command(PrepCmd.PrepHHSStopHeating())

  # ----------------------------------------
  # Lifecycle
  # ----------------------------------------

  async def request_is_initialized(self) -> bool:
    """Whether the shaker has been initialized since power-up."""
    result = await self._driver.send_command(PrepCmd.PrepHHSGetIsInitialized())
    return bool(result.is_initialized)

  async def discover(self) -> None:
    """Read the firmware version and the accepted ranges into `configuration`. Read-only."""
    version = await self._driver.send_command(PrepCmd.PrepHHSGetFirmwareVersion())
    temperatures = await self._driver.send_command(PrepCmd.PrepHHSGetTemperatureRange())
    speeds = await self._driver.send_command(PrepCmd.PrepHHSGetSpeedRange())
    c = self.configuration
    c.firmware_version = version.firmware_version.strip().strip('"')
    c.temperature_range = (temperatures.minimum_temperature, temperatures.maximum_temperature)
    c.maximum_supervision_tolerance = temperatures.maximum_supervision_tolerance
    c.speed_range = (speeds.minimum_velocity, speeds.maximum_velocity)
    c.acceleration_range = (speeds.minimum_acceleration, speeds.maximum_acceleration)

  async def initialize(self, *, smart: bool = True) -> None:
    """Initialize the plate lock and the shaker drive.

    Args:
      smart: whether the device initializes only what it judges it has to.
    """
    await self._unchecked_fw_initialize(smart)

  async def _on_setup(self) -> None:
    """Called by the driver's setup, after it has built this feature."""
    await self.discover()
    if not await self.request_is_initialized():
      await self.initialize()

  async def _on_stop(self) -> None:
    """Called by the driver's stop: stop shaking and temperature control; the lock stays."""
    try:
      await self.stop_shaking()
    finally:
      await self.stop_temperature_control()

  # ----------------------------------------
  # Plate lock
  # ----------------------------------------

  async def request_plate_locked(self) -> bool:
    """Whether the plate lock is closed."""
    return bool((await self._driver.send_command(PrepCmd.PrepHHSGetPlateLockState())).locked)

  async def lock_plate(self) -> None:
    """Close the plate lock."""
    await self._unchecked_fw_move_plate_lock(True)

  async def unlock_plate(self) -> None:
    """Open the plate lock.

    Raises:
      RuntimeError: If it is shaking.
    """
    if await self.request_is_shaking():
      raise RuntimeError("cannot unlock the plate while shaking; call `stop_shaking` first")
    await self._unchecked_fw_move_plate_lock(False)

  # ----------------------------------------
  # Shaking
  # ----------------------------------------

  async def request_shaking_status(self) -> PrepCmd.PrepHHSGetShakingStatus.Response:
    """Mode, current and target speed, and remaining time, as the shaker answers them."""
    return await self._driver.send_command(PrepCmd.PrepHHSGetShakingStatus())

  async def request_is_shaking(self) -> bool:
    """Whether the shaker is running, continuously or periodically."""
    status = await self.request_shaking_status()
    return int(status.shaker_status) != PrepCmd.ShakerStatus.InactiveShaking

  def _check_shaking(self, speed: float, acceleration: float) -> None:
    """Raise if speed or acceleration is outside what the device accepts."""
    self._check_in_range("speed", speed, self.configuration.speed_range)
    self._check_in_range("acceleration", acceleration, self.configuration.acceleration_range)

  async def start_shaking(
    self,
    speed: float,
    *,
    duration: Optional[int] = None,
    direction: Optional[Direction] = None,
    acceleration: Optional[float] = None,
    leave_locked: Optional[bool] = None,
  ) -> None:
    """Lock the plate and start shaking continuously; returns once the shaker reports running.

    Args:
      speed: rpm.
      duration: seconds the device shakes before it stops by itself; None shakes until
        `stop_shaking`. TODO(device): unit, and that 0 means indefinite.
      direction: `default_shaking_direction` when None.
      acceleration: TODO(device): unit. `default_shaking_acceleration` when None.
      leave_locked: whether the plate stays locked once a `duration` ends.
        `default_leave_locked` when None.

    Raises:
      ValueError: If speed or acceleration is outside the device's range.
      RuntimeError: If the shaker does not report running after the command.
    """
    direction = self.default_shaking_direction if direction is None else direction
    acceleration = self.default_shaking_acceleration if acceleration is None else acceleration
    leave_locked = self.default_leave_locked if leave_locked is None else leave_locked
    self._check_shaking(speed, acceleration)
    await self.lock_plate()
    await self._unchecked_fw_start_shaking(
      speed, duration or 0, leave_locked, direction, acceleration
    )
    if not await self.request_is_shaking():
      raise RuntimeError("the shaker was told to start and does not report running")

  async def start_periodic_shaking(
    self,
    speed: float,
    period: int,
    on_time: int,
    *,
    duration: Optional[int] = None,
    direction: Optional[Direction] = None,
    acceleration: Optional[float] = None,
    leave_locked: Optional[bool] = None,
  ) -> None:
    """Lock the plate and shake for `on_time` of every `period`.

    Args:
      speed: rpm.
      period: TODO(device): unit. The length of one on-and-off cycle.
      on_time: TODO(device): unit. How long of each period it shakes.
      duration: as `start_shaking`.
      direction: as `start_shaking`.
      acceleration: as `start_shaking`.
      leave_locked: as `start_shaking`.

    Raises:
      ValueError: If a value is outside its range, or `on_time` exceeds `period`.
    """
    direction = self.default_shaking_direction if direction is None else direction
    acceleration = self.default_shaking_acceleration if acceleration is None else acceleration
    leave_locked = self.default_leave_locked if leave_locked is None else leave_locked
    self._check_shaking(speed, acceleration)
    if not 0 < on_time <= period:
      raise ValueError(f"on_time must be within 1 to period ({period}), is {on_time}")
    await self.lock_plate()
    await self._unchecked_fw_start_periodic_shaking(
      speed, period, on_time, duration or 0, leave_locked, direction, acceleration
    )

  async def stop_shaking(self) -> None:
    """Stop shaking. TODO(device): whether the command waits for the drive, or this must poll."""
    await self._unchecked_fw_stop_shaking()

  async def shake(
    self,
    speed: float,
    duration: float,
    *,
    direction: Optional[Direction] = None,
    acceleration: Optional[float] = None,
  ) -> None:
    """Shake for `duration` seconds, timed here, then stop.

    Args:
      speed: rpm.
      duration: seconds.
      direction: as `start_shaking`.
      acceleration: as `start_shaking`.
    """
    await self.start_shaking(speed, direction=direction, acceleration=acceleration)
    try:
      await asyncio.sleep(duration)
    finally:
      await self.stop_shaking()

  # ----------------------------------------
  # Temperature
  # ----------------------------------------

  async def request_heater_status(self) -> PrepCmd.PrepHHSGetHeaterStatus.Response:
    """Whether it is heating, and its current and target temperature, in °C."""
    return await self._driver.send_command(PrepCmd.PrepHHSGetHeaterStatus())

  async def measure_temperature(self) -> float:
    """The measured temperature, in °C."""
    return float((await self.request_heater_status()).current_temperature)

  async def start_temperature_control(
    self,
    temperature: float,
    *,
    wait_until_reached: bool = False,
    supervision_timeout: Optional[int] = None,
    supervision_tolerance: Optional[float] = None,
  ) -> None:
    """Start heating to `temperature`, in °C, and hold it.

    Args:
      temperature: °C.
      wait_until_reached: whether the command answers only once the device has reached it.
      supervision_timeout: TODO(device): unit. `default_supervision_timeout` when None.
      supervision_tolerance: °C. `default_supervision_tolerance` when None.

    Raises:
      ValueError: If a value is outside the device's range.
    """
    timeout = (
      self.default_supervision_timeout if supervision_timeout is None else supervision_timeout
    )
    tolerance = (
      self.default_supervision_tolerance if supervision_tolerance is None else supervision_tolerance
    )
    self._check_in_range("temperature", temperature, self.configuration.temperature_range)
    self._check_supervision_tolerance(tolerance)
    await self._unchecked_fw_start_temperature_control(
      temperature, wait_until_reached, timeout, tolerance
    )
    self.target_temperature = temperature

  async def wait_for_temperature(
    self, *, tolerance: Optional[float] = None, timeout: Optional[float] = None
  ) -> float:
    """Wait until the temperature is within `tolerance` of the target; return it.

    Args:
      tolerance: °C. `default_temperature_tolerance` when None.
      timeout: seconds. `default_temperature_timeout` when None.

    Raises:
      RuntimeError: If no target temperature is set.
      TimeoutError: If it is not reached within `timeout`.
    """
    if self.target_temperature is None:
      raise RuntimeError("no target temperature set; call `start_temperature_control` first")
    tolerance = self.default_temperature_tolerance if tolerance is None else tolerance
    timeout = self.default_temperature_timeout if timeout is None else timeout
    deadline = time.monotonic() + timeout
    while True:
      temperature = await self.measure_temperature()
      if abs(temperature - self.target_temperature) <= tolerance:
        return temperature
      if time.monotonic() >= deadline:
        raise TimeoutError(
          f"{temperature} °C after {timeout} s, target {self.target_temperature} ± {tolerance}"
        )
      await asyncio.sleep(self.default_poll_interval)

  async def stop_temperature_control(self) -> None:
    """Stop heating."""
    await self._unchecked_fw_stop_temperature_control()
    self.target_temperature = None

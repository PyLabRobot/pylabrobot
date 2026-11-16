"""The v3 arm: seat against the stops, home on flags and follow the unit's route to idle."""

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from pylabrobot.resources import Coordinate

from ..arm_kinematics import compute_device_position, compute_device_rate, compute_joint_angles
from ..errors import MantisConfigurationError, MantisMotionError, MantisTimeoutError
from ..v3_arm_configuration import V3ArmConfiguration
from .arm import (
  ARM_MOTORS,
  HOME_TIMEOUT,
  MOTOR_FOLLOWING_ERROR,
  MOTOR_HOMED,
  MOTOR_HOMING,
  MOTOR_MOVING,
  MOTOR_STATUS_POLL_INTERVAL,
  MOVE_TIMEOUT,
  Z_MOTOR,
  Arm,
)

if TYPE_CHECKING:
  from ..master import MantisDriver

logger = logging.getLogger(__name__)


class V3Arm(Arm):
  """An arm without absolute encoders; homing requires this unit's ``arm`` block.

  Following errors are tolerated during homing and its positioning moves. Other faults fail
  immediately. The final status check rejects remaining faults. A failed or cancelled
  initialization stops every motor; stop does not park a v3.
  """

  default_prehome_passes: int = 8

  def __init__(self, *, driver: "MantisDriver", configuration: Optional[V3ArmConfiguration]):
    """Take the unit's calibration; None allows discovery but refuses initialization."""
    super().__init__(
      driver=driver,
      offsets=configuration.offsets if configuration is not None else None,
      homing_position_a=None,
      encoder_correction=None,
    )
    self.configuration = configuration

  def _check_calibrated(self) -> None:
    """Refuse motion without the unit's homing calibration."""
    if self.configuration is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no v3 arm calibration for this unit; give its configuration folder "
        "(oem_config_directory) or a configuration saved with it, or set up with skip_arm=True"
      )

  async def _request_absolute_device_position(self, *, motor: int) -> float:
    """Refuse: this arm has no absolute encoders, even after homing."""
    raise NotImplementedError("a Mantis v3 has no absolute arm encoders")

  async def request_link_angles(self) -> Tuple[float, float]:
    """Read the links only after this connection has established the home frame."""
    if not self.is_initialized:
      raise MantisMotionError("the v3 arm must be initialized before its pose is known")
    return await super().request_link_angles()

  async def request_head_position(self) -> Coordinate:
    """Read the head's pose only after this connection has established the home frame."""
    if not self.is_initialized:
      raise MantisMotionError("the v3 arm must be initialized before its pose is known")
    return await super().request_head_position()

  def _check_target(self, *, motor: int, target: float) -> None:
    """Check a device-unit target against the discovered motor limits."""
    lower, upper = self._driver.configuration.motor_limits[motor]
    if not lower <= target <= upper:
      raise MantisConfigurationError(
        f"motor {motor}'s target {target} is outside its limits ({lower}, {upper})"
      )

  async def _wait_for_axes(
    self, *, motors: Tuple[int, ...], timeout: float, expected_following_error: bool = False
  ) -> Dict[int, int]:
    """Wait for the selected axes, optionally tolerating a homing move's following errors."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
      statuses = {}
      for motor in motors:
        status = await self._driver.request_motor_status(motor=motor)
        checked = status & ~MOTOR_FOLLOWING_ERROR if expected_following_error else status
        self._check_status(motor=motor, status=checked)
        statuses[motor] = status
      if not any(status & (MOTOR_MOVING | MOTOR_HOMING) for status in statuses.values()):
        return statuses
      if asyncio.get_running_loop().time() >= deadline:
        raise MantisTimeoutError(
          f"{self._driver.name}: motors {motors} still move after {timeout} s"
        )
      await asyncio.sleep(MOTOR_STATUS_POLL_INTERVAL)

  async def _move_axis(
    self, *, motor: int, target: float, speed: float, acceleration: float
  ) -> None:
    """Check and send one move; its caller waits for the coordinated axes."""
    self._check_target(motor=motor, target=target)
    await self._unchecked_fw_move_absolute(
      motor=motor, position=target, speed=speed, acceleration=acceleration
    )

  async def _prehome(self) -> None:
    """Seat the coupled arms twice, then back off relative to each motor's live position."""
    assert self.configuration is not None
    calibration = self.configuration
    for motor in ARM_MOTORS:
      await self._unchecked_fw_clear_motor_faults(motor=motor)
    seated_once = False
    for _ in range(self.default_prehome_passes):
      for motor in ARM_MOTORS:
        await self._unchecked_fw_set_motor_position(motor=motor, position=0.0)
      for motor in ARM_MOTORS:
        await self._move_axis(
          motor=motor,
          target=compute_device_position(motor=motor, position=calibration.retract),
          speed=compute_device_rate(motor=motor, rate=calibration.jog_speed),
          acceleration=compute_device_rate(motor=motor, rate=calibration.jog_acceleration),
        )
      statuses = await self._wait_for_axes(
        motors=ARM_MOTORS, timeout=MOVE_TIMEOUT, expected_following_error=True
      )
      if any(status & MOTOR_FOLLOWING_ERROR for status in statuses.values()):
        if seated_once:
          break
        seated_once = True
        for motor in ARM_MOTORS:
          await self._unchecked_fw_clear_motor_faults(motor=motor)
    else:
      raise MantisMotionError(
        f"{self._driver.name}: the arms did not seat in {self.default_prehome_passes} passes"
      )
    for motor in ARM_MOTORS:
      await self._unchecked_fw_clear_motor_faults(motor=motor)
    for motor in ARM_MOTORS:
      position = await self._driver.request_motor_position(motor=motor)
      await self._move_axis(
        motor=motor,
        target=position + compute_device_position(motor=motor, position=calibration.backoff),
        speed=compute_device_rate(motor=motor, rate=calibration.jog_speed),
        acceleration=compute_device_rate(motor=motor, rate=calibration.jog_acceleration),
      )
    await self._wait_for_axes(
      motors=ARM_MOTORS, timeout=MOVE_TIMEOUT, expected_following_error=True
    )
    for motor in ARM_MOTORS:
      await self._unchecked_fw_set_motor_position(motor=motor, position=0.0)

  async def _home_axes(self, *, motors: Tuple[int, ...]) -> None:
    """Start flag searches in order, then wait for all axes and require every homed bit."""
    assert self.configuration is not None
    for motor in motors:
      home = self.configuration.motors[motor]
      await self._unchecked_fw_clear_motor_faults(motor=motor)
      fast = compute_device_rate(motor=motor, rate=home.speed)
      await self._unchecked_fw_home(
        motor=motor,
        method=home.method,
        positive_edge=home.positive_edge,
        positive_direction=home.positive_direction,
        slow_speed=fast / 10,
        fast_speed=fast,
        acceleration=compute_device_rate(motor=motor, rate=home.acceleration),
      )
      if motor in ARM_MOTORS:
        await asyncio.sleep(self.configuration.motor_delay)
    await self._wait_for_axes(motors=motors, timeout=HOME_TIMEOUT, expected_following_error=True)
    for motor in motors:
      status = await self._driver.request_motor_status(motor=motor)
      self._check_status(motor=motor, status=status & ~MOTOR_FOLLOWING_ERROR)
      if not status & MOTOR_HOMED:
        raise MantisMotionError(
          f"{self._driver.name}: motor {motor} did not home (status 0x{status:04x})",
          motor=motor,
          status=status,
        )

  async def _set_home_frame(self) -> None:
    """Settle each arm motor at zero, then declare its calibrated home position."""
    assert self.configuration is not None
    for motor in ARM_MOTORS:
      # The vendor override uses these device-unit rates, independent of the jog rates.
      await self._move_axis(motor=motor, target=0.0, speed=500.0, acceleration=100.0)
      await self._wait_for_axes(
        motors=(motor,), timeout=MOVE_TIMEOUT, expected_following_error=True
      )
      await self._unchecked_fw_set_motor_position(
        motor=motor,
        position=compute_device_position(
          motor=motor, position=self.configuration.home_positions[motor]
        ),
      )

  def _walk_targets(self) -> Tuple[Dict[int, float], ...]:
    """Convert the unit's route to motor positions, subtracting its geometry offsets."""
    assert self.configuration is not None
    return tuple(
      {
        motor: compute_device_position(
          motor=motor, position=angle - self.configuration.offsets[motor]
        )
        for motor, angle in enumerate(compute_joint_angles(x=x, y=y))
      }
      for x, y in self.configuration.post_home_positions
    )

  async def _walk_to_idle(self) -> None:
    """Raise Z before each waypoint, then move the two arm motors together."""
    assert self.configuration is not None
    home = self.configuration.motors
    for targets in self._walk_targets():
      await self._move_axis(
        motor=Z_MOTOR,
        target=0.0,
        speed=compute_device_rate(motor=Z_MOTOR, rate=home[0].speed),
        acceleration=compute_device_rate(motor=Z_MOTOR, rate=home[Z_MOTOR].acceleration),
      )
      await self._wait_for_axes(
        motors=(Z_MOTOR,), timeout=MOVE_TIMEOUT, expected_following_error=True
      )
      for motor in ARM_MOTORS:
        await self._move_axis(
          motor=motor,
          target=targets[motor],
          speed=compute_device_rate(motor=motor, rate=home[0].speed),
          acceleration=compute_device_rate(motor=motor, rate=home[0].acceleration),
        )
      await self._wait_for_axes(
        motors=ARM_MOTORS, timeout=MOVE_TIMEOUT, expected_following_error=True
      )

  async def initialize(self) -> None:
    """Seat the arms and home Z twice, home the arms together, establish the frame and walk to idle.

    Missing calibration or an invalid route refuses before anything moves. A failure or
    cancellation stops all three motors and never continues to the next phase.
    """
    self._check_calibrated()
    assert self.configuration is not None
    self._initialized = False
    if type(self.default_prehome_passes) is not int or self.default_prehome_passes < 2:
      raise ValueError("default_prehome_passes must be an integer of at least 2")
    for targets in self._walk_targets():
      for motor, target in targets.items():
        self._check_target(motor=motor, target=target)
    for motor in ARM_MOTORS:
      self._check_target(
        motor=motor,
        target=compute_device_position(
          motor=motor, position=self.configuration.home_positions[motor]
        ),
      )
      self._check_target(
        motor=motor,
        target=compute_device_position(motor=motor, position=self.configuration.retract),
      )
      self._check_target(motor=motor, target=0.0)
    self._check_target(motor=Z_MOTOR, target=0.0)
    try:
      await self._wait_for_axes(
        motors=(*ARM_MOTORS, Z_MOTOR), timeout=MOVE_TIMEOUT, expected_following_error=True
      )
      for pass_index in range(2):
        logger.info("[%s] seating the arms, pass %d", self._driver.name, pass_index + 1)
        await self._prehome()
        logger.info("[%s] homing Z", self._driver.name)
        await self._home_axes(motors=(Z_MOTOR,))
      logger.info("[%s] homing arm motors together, motor 1 then motor 0", self._driver.name)
      await self._home_axes(motors=(1, 0))
      logger.info("[%s] declaring the calibrated home frame", self._driver.name)
      await self._set_home_frame()
      logger.info("[%s] following the calibrated route to idle", self._driver.name)
      await self._walk_to_idle()
      await self._wait_for_axes(motors=(*ARM_MOTORS, Z_MOTOR), timeout=MOVE_TIMEOUT)
    except BaseException:
      await self._stop_motors(motors=(*ARM_MOTORS, Z_MOTOR))
      await self._log_where_it_stopped()
      raise
    self._initialized = True

  async def _halt(self) -> None:
    """Stop all axes without a park move; a reconnect must establish the home frame again."""
    self._initialized = False
    await self._stop_motors(motors=(*ARM_MOTORS, Z_MOTOR))

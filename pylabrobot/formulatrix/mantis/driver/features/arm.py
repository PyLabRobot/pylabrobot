"""The arm: motors 0 and 1 turn a five-bar linkage that carries the head, and motor 2 drives Z.

On a v4, board 11 reads each arm motor's absolute encoder, so where the arm stands is known after
power-on: the arms are declared there, never homed. Z homes on its flag. Every bring-up step and
its order matches the recorded v4 startup.
"""

import asyncio
import logging
import struct
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from pylabrobot.resources import Coordinate

from ..arm_kinematics import (
  compute_absolute_device_position,
  compute_device_position,
  compute_device_rate,
  compute_head_position,
  compute_joint_angles,
  compute_position,
)
from ..errors import (
  MOTOR_STATUS_ERROR_BITS,
  MantisConfigurationError,
  MantisKinematicsError,
  MantisMotionError,
  MantisTimeoutError,
)
from ..events import HOME_DONE_EVENT, decode_home_done
from ..framing import MOTION_BOARD

if TYPE_CHECKING:
  from ..master import MantisDriver

logger = logging.getLogger(__name__)

ARM_MOTORS = (0, 1)
Z_MOTOR = 2
# The status word's bits for a motor that moves, homes or has homed; the faults are
# MOTOR_STATUS_ERROR_BITS.
MOTOR_MOVING = 1
MOTOR_HOMING = 2
MOTOR_HOMED = 4
# The following-error bits: a motor that slipped, idle or moving.
MOTOR_FOLLOWING_ERROR = 128 | 256
# The idle position: where every move to idle sends the head, (x, y) in the arm frame, mm; the
# same on a v3 and a v4. Each unit's offsets turn it into targets.
POST_HOMING_POSITION = (15.0, 31.177)
# The arms' largest following error, in device units, set at bring-up.
ARM_MAXIMUM_FOLLOWING_ERROR = 4.0
# How often each declaration of the arms' absolute position reads and writes them, as recorded.
DECLARE_PASSES = 3
# The Front zone's x bounds, mm: the head is moved to idle only from between them, in the Front
# zone or in the strip below it that no zone holds.
FRONT_ZONE_X_RANGE = (-15.0, 45.0)
# Where the recorded close lowers Z at idle, in mm.
SHUTDOWN_Z = 20.0
# Z's homing search: fast at 15 mm/s, slow at a tenth of it, at 400 mm/s², toward the flag's
# rising edge in the negative direction.
Z_HOMING_SPEED = 15.0
Z_HOMING_ACCELERATION = 400.0
# How far below a height Z's encoder may read after rising to it, mm: twice the largest gap read
# between Z's encoder and its demand position (2 device units, 0.05 mm), rounded up.
Z_TRAVERSE_TOLERANCE = 0.1
# PyLabRobot design choices: poll a moving motor every 20 ms; a move or a home ends within 30 s.
MOTOR_STATUS_POLL_INTERVAL = 0.02
MOVE_TIMEOUT = 30.0
HOME_TIMEOUT = 30.0


class Arm:
  """The arm and Z, built when board 11 answers (a v4).

  Bringing it up needs the unit's ``arm`` block: each arm motor's offset, encoder reference and
  encoder correction. Without it nothing is sent and ``initialize`` refuses; it never falls back to
  another unit's values.
  """

  # Where the ``default_*`` properties start, in degrees/s, degrees/s², mm/s and mm/s².
  _default_arm_speed: float = 10000.0
  _default_arm_acceleration: float = 1500.0
  _default_z_speed: float = 55.0
  _default_z_acceleration: float = 200.0

  def __init__(
    self,
    *,
    driver: "MantisDriver",
    offsets: Optional[Dict[int, float]],
    homing_position_a: Optional[Dict[int, float]],
    encoder_correction: Optional[Dict[int, List[Tuple[float, float]]]],
  ):
    """
    Args:
      driver: the driver it sends through.
      offsets: each arm motor's offset, degrees, added to its angle for the geometry's own.
      homing_position_a: each arm motor's encoder reference, in the absolute encoder's units.
      encoder_correction: each arm motor's (ideal, measured) correction rows, measured ascending.
    """
    self._driver = driver
    self.offsets = offsets
    self.homing_position_a = homing_position_a
    self.encoder_correction = encoder_correction
    self._initialized = False

  @property
  def is_initialized(self) -> bool:
    """Whether ``initialize`` brought the arm up since the link opened."""
    return self._initialized

  @staticmethod
  def _check_positive(*, name: str, value: float) -> None:
    """Raise ``ValueError`` unless ``value`` is above 0: the device reports no range of its own."""
    if not value > 0:
      raise ValueError(f"{name} must be above 0, got {value}")

  @property
  def default_arm_speed(self) -> float:
    """Each arm motor's speed for a move, in degrees/s."""
    return self._default_arm_speed

  @default_arm_speed.setter
  def default_arm_speed(self, value: float) -> None:
    self._check_positive(name="default_arm_speed", value=value)
    self._default_arm_speed = value

  @property
  def default_arm_acceleration(self) -> float:
    """Each arm motor's acceleration for a move, and its deceleration at a stop, in degrees/s²."""
    return self._default_arm_acceleration

  @default_arm_acceleration.setter
  def default_arm_acceleration(self, value: float) -> None:
    self._check_positive(name="default_arm_acceleration", value=value)
    self._default_arm_acceleration = value

  @property
  def default_z_speed(self) -> float:
    """Z's speed for a move, in mm/s."""
    return self._default_z_speed

  @default_z_speed.setter
  def default_z_speed(self, value: float) -> None:
    self._check_positive(name="default_z_speed", value=value)
    self._default_z_speed = value

  @property
  def default_z_acceleration(self) -> float:
    """Z's acceleration for a move, and its deceleration at a stop, in mm/s²."""
    return self._default_z_acceleration

  @default_z_acceleration.setter
  def default_z_acceleration(self, value: float) -> None:
    self._check_positive(name="default_z_acceleration", value=value)
    self._default_z_acceleration = value

  def _get_device_speed(self, *, motor: int) -> float:
    """A motor's default speed, in device units."""
    speed = self.default_z_speed if motor == Z_MOTOR else self.default_arm_speed
    return compute_device_rate(motor=motor, rate=speed)

  def _get_device_acceleration(self, *, motor: int) -> float:
    """A motor's default acceleration, in device units; a stop decelerates at it."""
    acceleration = (
      self.default_z_acceleration if motor == Z_MOTOR else self.default_arm_acceleration
    )
    return compute_device_rate(motor=motor, rate=acceleration)

  def _check_calibrated(self) -> None:
    """Raise ``MantisConfigurationError`` unless the unit's ``arm`` block was given."""
    if self.offsets is None or self.homing_position_a is None or self.encoder_correction is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no arm calibration for this unit; give its configuration folder "
        "(oem_config_directory) or a configuration saved with it, or set up with skip_arm=True"
      )

  # -- raw firmware calls ----------------------------------------------------

  async def _unchecked_fw_clear_motor_faults(self, *, motor: int) -> None:
    """Clear a motor's faults (board 0, command 17), sent once."""
    await self._driver._send_command(
      board=MOTION_BOARD, command=17, payload=struct.pack("<h", motor), attempts=1
    )

  async def _unchecked_fw_set_motor_open_loop(self, *, motor: int, open_loop: bool) -> None:
    """Run a motor open loop, unpowered, or closed loop, energised (command 19), sent once."""
    await self._driver._send_command(
      board=MOTION_BOARD, command=19, payload=struct.pack("<hH", motor, open_loop), attempts=1
    )

  async def _unchecked_fw_home(
    self,
    *,
    motor: int,
    method: int,
    positive_edge: bool,
    positive_direction: bool,
    slow_speed: float,
    fast_speed: float,
    acceleration: float,
  ) -> None:
    """Search a motor's home flag (command 21), sent once; speeds in device units."""
    payload = struct.pack(
      "<hhHHddd",
      motor,
      method,
      positive_edge,
      positive_direction,
      slow_speed,
      fast_speed,
      acceleration,
    )
    await self._driver._send_command(board=MOTION_BOARD, command=21, payload=payload, attempts=1)

  async def _unchecked_fw_move_absolute(
    self, *, motor: int, position: float, speed: float, acceleration: float
  ) -> None:
    """Move one motor (command 22), sent once, in device units; it answers as the move starts."""
    payload = struct.pack("<hddd", motor, position, speed, acceleration)
    await self._driver._send_command(board=MOTION_BOARD, command=22, payload=payload, attempts=1)

  async def _unchecked_fw_stop(self, *, motor: int, deceleration: float) -> None:
    """Stop a motor (command 24), sent once; deceleration in device units."""
    await self._driver._send_command(
      board=MOTION_BOARD, command=24, payload=struct.pack("<hd", motor, deceleration), attempts=1
    )

  async def _unchecked_fw_set_motor_position(self, *, motor: int, position: float) -> None:
    """Declare where a motor is (command 28), sent once, in device units."""
    await self._driver._send_command(
      board=MOTION_BOARD, command=28, payload=struct.pack("<hd", motor, position), attempts=1
    )

  async def _unchecked_fw_set_following_error_configuration(
    self, *, motor: int, enabled: bool, maximum: float
  ) -> None:
    """Set a motor's following-error check (command 83), sent once; maximum in device units."""
    await self._driver._send_command(
      board=MOTION_BOARD,
      command=83,
      payload=struct.pack("<hHd", motor, enabled, maximum),
      attempts=1,
    )

  async def _unchecked_fw_set_encoder_position(self, *, motor: int, position: float) -> None:
    """Declare a motor's servo encoder position (command 85), sent once, in device units."""
    await self._driver._send_command(
      board=MOTION_BOARD, command=85, payload=struct.pack("<hd", motor, position), attempts=1
    )

  # -- where it is ------------------------------------------------------------

  async def _request_absolute_device_position(self, *, motor: int) -> float:
    """An arm motor's position from its absolute encoder, in device units.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
    """
    self._check_calibrated()
    assert self.encoder_correction is not None and self.homing_position_a is not None
    reading = await self._driver.request_absolute_encoder_position(motor=motor)
    return compute_absolute_device_position(
      motor=motor,
      reading=reading,
      correction=self.encoder_correction[motor],
      homing_position_a=self.homing_position_a[motor],
    )

  async def request_absolute_joint_position(self) -> Dict[int, float]:
    """Each arm motor's angle from its absolute encoder, in degrees, by motor; nothing moves.

    Kept across power-off, unlike ``request_joint_position`` before the arms are declared.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
    """
    return {
      motor: compute_position(
        motor=motor, device_position=await self._request_absolute_device_position(motor=motor)
      )
      for motor in ARM_MOTORS
    }

  async def request_joint_position(self) -> Dict[int, float]:
    """Where the controller holds each motor: 0 and 1 in degrees, 2 (Z) in mm down from its flag."""
    return {
      motor: compute_position(
        motor=motor, device_position=await self._driver.request_motor_position(motor=motor)
      )
      for motor in (*ARM_MOTORS, Z_MOTOR)
    }

  async def _request_measured_position(self, *, motor: int) -> float:
    """Where a motor is by its servo encoder (command 84), measured: an arm motor in degrees, Z in
    mm down from its flag. The arms' encoders are declared at bring-up."""
    return compute_position(
      motor=motor, device_position=await self._driver.request_encoder_position(motor=motor)
    )

  def is_xy_reachable(self, *, x: float, y: float) -> bool:
    """Whether the head can reach (x, y) in the arm frame, mm. Nothing is clamped.

    Args:
      x: the head's x, in mm.
      y: the head's y, in mm.
    """
    try:
      compute_joint_angles(x=x, y=y)
    except MantisKinematicsError:
      return False
    return True

  def _get_link_angles(self, *, joint_position: Dict[int, float]) -> Tuple[float, float]:
    """The two links' angles, the geometry's own, degrees: each arm motor's angle plus its offset.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
    """
    self._check_calibrated()
    assert self.offsets is not None
    return (joint_position[0] + self.offsets[0], joint_position[1] + self.offsets[1])

  async def request_link_angles(self) -> Tuple[float, float]:
    """The two links' angles where the controller holds the arm motors, the geometry's own, degrees.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
    """
    self._check_calibrated()
    return self._get_link_angles(joint_position=await self.request_joint_position())

  def _get_head_position(self, *, joint_position: Dict[int, float]) -> Tuple[float, float]:
    """Where the head is in the arm frame, mm, for the arm motors' angles in degrees.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
      MantisKinematicsError: if the links cannot meet at those angles.
    """
    theta_1, theta_2 = self._get_link_angles(joint_position=joint_position)
    return compute_head_position(theta_1=theta_1, theta_2=theta_2)

  def _get_idle_targets(self) -> Dict[int, float]:
    """Each arm motor's target at idle, in device units: ``POST_HOMING_POSITION`` less its offset.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
    """
    self._check_calibrated()
    assert self.offsets is not None
    x, y = POST_HOMING_POSITION
    angles = compute_joint_angles(x=x, y=y)
    return {
      motor: compute_device_position(motor=motor, position=angles[motor] - self.offsets[motor])
      for motor in ARM_MOTORS
    }

  async def request_head_position(self) -> Coordinate:
    """Where the head is: x and y in the arm frame, mm; z where Z is, mm down from its flag.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
      MantisKinematicsError: if the links cannot meet at the angles read.
    """
    self._check_calibrated()
    joint_position = await self.request_joint_position()
    x, y = self._get_head_position(joint_position=joint_position)
    return Coordinate(x=x, y=y, z=joint_position[Z_MOTOR])

  # -- moves ------------------------------------------------------------------

  async def _log_where_it_stopped(self) -> None:
    """Log where each motor is by its encoder after a failure; a failed read is logged and
    dropped."""
    try:
      measured = {
        motor: await self._request_measured_position(motor=motor)
        for motor in (*ARM_MOTORS, Z_MOTOR)
      }
      logger.warning("[%s] the motors stopped at %s by their encoders", self._driver.name, measured)
    except Exception:
      logger.warning("[%s] where the motors stopped is not known", self._driver.name)

  async def _stop_motors(self, *, motors: Tuple[int, ...]) -> bool:
    """Stop each motor at its default deceleration (command 24), each in its own try; a failure is
    logged. Return whether every stop was answered."""
    stopped = True
    for motor in motors:
      try:
        await self._unchecked_fw_stop(
          motor=motor, deceleration=self._get_device_acceleration(motor=motor)
        )
      except Exception:
        logger.warning("[%s] could not stop motor %d", self._driver.name, motor, exc_info=True)
        stopped = False
    return stopped

  def _check_status(self, *, motor: int, status: int) -> None:
    """Raise ``MantisMotionError`` if a motor's status word reports a fault."""
    faults = [meaning for bit, meaning in MOTOR_STATUS_ERROR_BITS.items() if status & bit]
    if faults:
      raise MantisMotionError(
        f"{self._driver.name}: motor {motor} reports {', '.join(faults)} (status 0x{status:04x})",
        motor=motor,
        status=status,
      )

  async def _wait_for_motors_idle(self, *, timeout: float) -> None:
    """Read each motor's status until none moves or homes.

    Raises:
      MantisMotionError: if a motor reports a fault.
      MantisTimeoutError: if one still moves after ``timeout`` seconds.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
      busy = []
      for motor in (*ARM_MOTORS, Z_MOTOR):
        status = await self._driver.request_motor_status(motor=motor)
        try:
          self._check_status(motor=motor, status=status)
        except MantisMotionError:
          await self._log_where_it_stopped()
          raise
        if status & (MOTOR_MOVING | MOTOR_HOMING):
          busy.append(motor)
      if not busy:
        return
      if asyncio.get_running_loop().time() >= deadline:
        await self._log_where_it_stopped()
        raise MantisTimeoutError(f"{self._driver.name}: motors {busy} still move after {timeout} s")
      await asyncio.sleep(MOTOR_STATUS_POLL_INTERVAL)

  async def _move_motors(self, *, targets: Dict[int, float]) -> None:
    """Move each motor to its target, in device units, at its default speed, then wait.

    Each move is read back once as it starts, as recorded, then every motor until all stop. On any
    failure the motors sent a move are stopped and where they stopped is logged before it raises.

    Args:
      targets: device units, by motor, sent in motor order.
    """
    sent: List[int] = []
    try:
      for motor in sorted(targets):
        sent.append(motor)  # before the send: a send that raised may still have started it
        await self._unchecked_fw_move_absolute(
          motor=motor,
          position=targets[motor],
          speed=self._get_device_speed(motor=motor),
          acceleration=self._get_device_acceleration(motor=motor),
        )
        status = await self._driver.request_motor_status(motor=motor)
        self._check_status(motor=motor, status=status)
      await self._wait_for_motors_idle(timeout=MOVE_TIMEOUT)
    except BaseException:
      await self._stop_motors(motors=tuple(sent))
      await self._log_where_it_stopped()
      raise

  async def _declare_absolute_joint_position(self) -> None:
    """Declare each arm motor where its absolute encoder reads, ``DECLARE_PASSES`` times.

    A pass clears both arm motors' faults, reads both encoders, then sets each motor's position
    and servo encoder position there.
    """
    for _ in range(DECLARE_PASSES):
      for motor in ARM_MOTORS:
        await self._unchecked_fw_clear_motor_faults(motor=motor)
      positions = {
        motor: await self._request_absolute_device_position(motor=motor) for motor in ARM_MOTORS
      }
      for motor in ARM_MOTORS:
        await self._unchecked_fw_set_motor_position(motor=motor, position=positions[motor])
        await self._unchecked_fw_set_encoder_position(motor=motor, position=positions[motor])
      await asyncio.sleep(0.01)  # recorded passes are 10 ms apart

  def _check_near_idle(self, *, joint_position: Dict[int, float]) -> None:
    """Raise unless the head stands within ``FRONT_ZONE_X_RANGE``, where it is moved to idle from.

    From anywhere else, a move to idle could cross a chip rack.

    Args:
      joint_position: each arm motor's angle, in degrees, by motor.

    Raises:
      MantisMotionError: if the head is elsewhere, or its position is not known.
    """
    try:
      x, y = self._get_head_position(joint_position=joint_position)
    except MantisKinematicsError as error:
      raise MantisMotionError(f"{self._driver.name}: where the head is is not known") from error
    low, high = FRONT_ZONE_X_RANGE
    if not low <= x <= high:
      raise MantisMotionError(
        f"{self._driver.name}: the head is at ({x:.1f}, {y:.1f}) mm; it is moved to idle only "
        f"from x {low} to {high} mm. Park the arm at idle, then set up again"
      )

  async def _home_z(self) -> None:
    """Home Z on its flag, as recorded: check no motor slipped, clear Z's faults, search, wait.

    Raises:
      MantisMotionError: if a motor reports a following error first, or Z does not read homed.
      MantisTimeoutError: if Z does not report homing done within ``HOME_TIMEOUT``.
    """
    for motor in (*ARM_MOTORS, Z_MOTOR):
      enabled, _ = await self._driver.request_following_error_configuration(motor=motor)
      if enabled:
        status = await self._driver.request_motor_status(motor=motor)
        if status & MOTOR_FOLLOWING_ERROR:
          raise MantisMotionError(
            f"{self._driver.name}: motor {motor} slipped (status 0x{status:04x}); Z is not homed",
            motor=motor,
            status=status,
          )
    await self._unchecked_fw_clear_motor_faults(motor=Z_MOTOR)
    waiter = self._driver._expect_event(
      board=MOTION_BOARD,
      code=HOME_DONE_EVENT,
      match=lambda payload: decode_home_done(payload=payload)[0] == Z_MOTOR,
    )
    fast_speed = compute_device_rate(motor=Z_MOTOR, rate=Z_HOMING_SPEED)
    try:
      await self._unchecked_fw_home(
        motor=Z_MOTOR,
        method=0,
        positive_edge=True,
        positive_direction=False,
        slow_speed=fast_speed / 10,
        fast_speed=fast_speed,
        acceleration=compute_device_rate(motor=Z_MOTOR, rate=Z_HOMING_ACCELERATION),
      )
    except BaseException:
      waiter.cancel()
      raise
    await self._driver._wait_for_event(waiter=waiter, timeout=HOME_TIMEOUT)
    status = await self._driver.request_motor_status(motor=Z_MOTOR)
    self._check_status(motor=Z_MOTOR, status=status)
    if not status & MOTOR_HOMED:
      raise MantisMotionError(
        f"{self._driver.name}: Z does not read homed (status 0x{status:04x})",
        motor=Z_MOTOR,
        status=status,
      )

  async def _move_to_idle(self) -> None:
    """Move to idle by the recorded route from the Front zone: Z up, the arms, then both."""
    arms = self._get_idle_targets()
    await self._move_motors(targets={Z_MOTOR: 0.0})
    await self._move_motors(targets=arms)
    await self._move_motors(targets={**arms, Z_MOTOR: 0.0})

  async def initialize(self) -> None:
    """Bring the arm and Z up from power-on, as in the recorded v4 startup.

    Energise the arm motors, declare them where their absolute encoders read, set their following
    error, move them to idle, home Z, then twice declare and move to idle again, and home Z. A
    failure from energising on leaves the arm motors unpowered again.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
      MantisMotionError: if the head is not where it is moved straight to idle from, or a motor
        faults.
      MantisTimeoutError: if a move or the home does not end in time.
    """
    self._check_calibrated()
    self._initialized = False
    try:
      for motor in ARM_MOTORS:
        await self._unchecked_fw_set_motor_open_loop(motor=motor, open_loop=False)
      await self._declare_absolute_joint_position()
      for motor in ARM_MOTORS:
        await self._unchecked_fw_set_following_error_configuration(
          motor=motor, enabled=True, maximum=ARM_MAXIMUM_FOLLOWING_ERROR
        )
      await self._unchecked_fw_clear_motor_faults(motor=Z_MOTOR)
      await self._declare_absolute_joint_position()
      self._check_near_idle(joint_position=await self.request_joint_position())
      await self._move_motors(targets=self._get_idle_targets())
      await self._home_z()
      for _ in range(2):
        await self._declare_absolute_joint_position()
        self._check_near_idle(joint_position=await self.request_joint_position())
        await self._move_to_idle()
      await self._home_z()
    except BaseException:
      for motor in ARM_MOTORS:
        try:
          await self._unchecked_fw_set_motor_open_loop(motor=motor, open_loop=True)
        except Exception:
          logger.warning("[%s] could not unpower motor %d", self._driver.name, motor, exc_info=True)
      raise
    self._initialized = True

  def _get_queued_target(
    self, *, motor: int, position: Optional[float]
  ) -> Tuple[float, float, float]:
    """A motor's (position, speed, acceleration) in a queued move, device units.

    Args:
      motor: 0, 1 (arm) or 2 (Z).
      position: the target in device units, or None to hold the motor: it is sent all zeros.
    """
    if position is None:
      return (0.0, 0.0, 0.0)
    return (
      position,
      self._get_device_speed(motor=motor),
      self._get_device_acceleration(motor=motor),
    )

  async def _park(self) -> None:
    """Park at idle and lower Z to ``SHUTDOWN_Z``, as in the recorded close.

    Z up on its own (command 22), read back by its encoder; then, queued, the arms with Z held,
    both; then Z down at idle. It waits for the motors to stop, and checks where the head is from
    the absolute encoders, not the controller.

    Raises:
      MantisMotionError: if a motor reports a fault, or the head is not where it is moved to idle
        from (nothing moves), or Z does not read up within ``Z_TRAVERSE_TOLERANCE`` after rising
        (the arms are not moved).
      MantisTimeoutError: if a motor still moves, or a queued move is not reported played, in time.
    """
    await self._wait_for_motors_idle(timeout=MOVE_TIMEOUT)
    self._check_near_idle(joint_position=await self.request_absolute_joint_position())
    idle_targets = self._get_idle_targets()
    arms = [
      self._get_queued_target(motor=motor, position=idle_targets[motor]) for motor in ARM_MOTORS
    ]
    z_up = self._get_queued_target(motor=Z_MOTOR, position=0.0)
    z_down = self._get_queued_target(
      motor=Z_MOTOR, position=compute_device_position(motor=Z_MOTOR, position=SHUTDOWN_Z)
    )
    z_held = self._get_queued_target(motor=Z_MOTOR, position=None)
    await self._move_motors(targets={Z_MOTOR: 0.0})
    z = await self._request_measured_position(motor=Z_MOTOR)
    if z > Z_TRAVERSE_TOLERANCE:
      raise MantisMotionError(
        f"{self._driver.name}: Z reads {z:.3f} mm below its flag by its encoder after rising to "
        "it; the arms are not moved",
        motor=Z_MOTOR,
      )
    for batch in ([arms + [z_held], arms + [z_up]], [arms + [z_down]]):
      await self._driver._unchecked_fw_clear_sequencer()
      await self._driver._unchecked_fw_start_sequencer()
      for targets in batch:
        item = await self._driver._queue_move_item(targets=targets)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT)

  async def _halt(self) -> None:
    """Stop every motor at its default deceleration, then park the arm if it was brought up and
    every motor stopped.

    Each stop runs whether or not the one before it succeeded, its failure logged; the arm is no
    longer brought up after it.
    """
    initialized, self._initialized = self._initialized, False
    if not await self._stop_motors(motors=(*ARM_MOTORS, Z_MOTOR)):
      if initialized:
        logger.warning("[%s] a motor did not stop; the arm is not parked", self._driver.name)
      return
    if initialized:
      try:
        await self._park()
      except Exception:
        logger.warning("[%s] the arm was not parked", self._driver.name, exc_info=True)

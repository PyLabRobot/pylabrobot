"""The arm: motors 0 and 1 turn a five-bar linkage that carries the head, and motor 2 drives Z.

On a v4, board 11 reads each arm motor's absolute encoder, so where the arm stands is known after
power-on: the arms are declared there, never homed. Z homes on its flag. Every bring-up step and
its order matches the recorded v4 startup.

The head goes to idle along the unit's routes: the zone it stands in names a route to a safe XY
and one to idle. Without them, only from near idle. A wellplate in the plate holder is reached
through the unit's stage transform.
"""

import asyncio
import logging
import struct
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from pylabrobot.resources import Coordinate, Plate, Resource

from ..arm_kinematics import (
  compute_absolute_device_position,
  compute_device_position,
  compute_device_rate,
  compute_head_position,
  compute_joint_angles,
  compute_position,
)
from ..errors import (
  MOTOR_ERROR_CODE_DESCRIPTIONS,
  MOTOR_STATUS_ERROR_BITS,
  FrameError,
  MantisConfigurationError,
  MantisCoordinateError,
  MantisKinematicsError,
  MantisMotionError,
  MantisTimeoutError,
)
from ..events import HOME_DONE_EVENT, decode_home_done
from ..framing import MOTION_BOARD
from ..oem_config import UNKNOWN_ZONE, WASH_STATIONS, ZONES
from ..sequence_files import Waypoint
from ..stage_transform import PITCH_HEIGHT, REFERENCE_HEIGHT, compute_arm_frame_position

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
# The Front zone's routes to safe XY and to idle, the same on every unit seen: the head is moved
# along them from near idle when the unit's zones are not given.
FRONT_SAFE_XY_ROUTE: List[Waypoint] = [(*POST_HOMING_POSITION, None)]
FRONT_IDLE_ROUTE: List[Waypoint] = [
  (None, None, 0.0),
  (*POST_HOMING_POSITION, None),
  (*POST_HOMING_POSITION, 0.0),
]
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
# The Z speeds (mm/s) and accelerations (mm/s²) of the recorded moves; the device has dropped a Z
# move outside them without an error, so none is sent.
Z_SPEED_RANGE = (20.0, 55.0)
Z_ACCELERATION_RANGE = (200.0, 500.0)
# The speeds and accelerations of every recorded recovery, close and station route: the arms in
# degrees/s and degrees/s², Z in mm/s and mm/s². Routes keep it.
ROUTE_ARM_SPEED = 10000.0
ROUTE_ARM_ACCELERATION = 1500.0
ROUTE_Z_SPEED = 55.0
ROUTE_Z_ACCELERATION = 200.0
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
  another unit's values. The ``zones`` and ``stations`` blocks give the routes; without them the
  head is moved to idle only from near it, and the stations refuse. Without the ``stage`` block a
  move to a wellplate refuses. Routes (bring-up, park, the stations, stop) move at the ``ROUTE_*``
  profile; the ``default_*`` speeds are for the other moves.

  Cartesian heights (``z`` of a location, a traverse height) are mm above Z's home flag, up
  positive; a joint position holds Z as the motor does, mm down from its flag.
  """

  # Where the ``default_*`` properties start: the route profile, and the safe height, mm above Z's
  # home flag (the safe height, Z -1.570 down).
  _default_arm_speed: float = ROUTE_ARM_SPEED
  _default_arm_acceleration: float = ROUTE_ARM_ACCELERATION
  _default_z_speed: float = ROUTE_Z_SPEED
  _default_z_acceleration: float = ROUTE_Z_ACCELERATION
  _default_minimum_traverse_height: float = 1.57

  def __init__(
    self,
    *,
    driver: "MantisDriver",
    offsets: Optional[Dict[int, float]],
    homing_position_a: Optional[Dict[int, float]],
    encoder_correction: Optional[Dict[int, List[Tuple[float, float]]]],
    zone_rectangles: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
    safe_xy_routes: Optional[Dict[str, List[Waypoint]]] = None,
    idle_routes: Optional[Dict[str, List[Waypoint]]] = None,
    waste_station_route: Optional[List[Waypoint]] = None,
    wash_station_routes: Optional[Dict[int, List[Waypoint]]] = None,
    stage_origin: Optional[Tuple[float, float, float]] = None,
    stage_transformation_matrix: Optional[Sequence[Sequence[float]]] = None,
    stage_offset_x: Optional[Tuple[float, float, float]] = None,
    stage_offset_y: Optional[Tuple[float, float, float]] = None,
    arm_type_rotation_degree: Optional[float] = None,
    arm_type_translation: Optional[Tuple[float, float, float]] = None,
  ):
    """
    Args:
      driver: the driver it sends through.
      offsets: each arm motor's offset, degrees, added to its angle for the geometry's own.
      homing_position_a: each arm motor's encoder reference, in the absolute encoder's units.
      encoder_correction: each arm motor's (ideal, measured) correction rows, measured ascending.
      zone_rectangles: each zone's (minimum x, maximum x, minimum y, maximum y), mm, by name.
      safe_xy_routes: each zone's route to its safe XY, the arms only, by name; Unknown included.
      idle_routes: each zone's route to idle, by name; Unknown included.
      waste_station_route: the route from idle to the waste station.
      wash_station_routes: the route from idle to each wash station, by number.
      stage_origin: the plate holder's origin, (x, y, z), mm.
      stage_transformation_matrix: the stage's projective matrix, 3 by 3.
      stage_offset_x: the stage's x offset, (x, y, z); its z tilts the stage.
      stage_offset_y: the stage's y offset, (x, y, z); its z tilts the stage.
      arm_type_rotation_degree: the arm type's rotation, degrees.
      arm_type_translation: the arm type's translation, (x, y, z), mm.
    """
    self._driver = driver
    self.offsets = offsets
    self.homing_position_a = homing_position_a
    self.encoder_correction = encoder_correction
    self.zone_rectangles = zone_rectangles
    self.safe_xy_routes = safe_xy_routes
    self.idle_routes = idle_routes
    self.waste_station_route = waste_station_route
    self.wash_station_routes = wash_station_routes
    self.stage_origin = stage_origin
    self.stage_transformation_matrix = stage_transformation_matrix
    self.stage_offset_x = stage_offset_x
    self.stage_offset_y = stage_offset_y
    self.arm_type_rotation_degree = arm_type_rotation_degree
    self.arm_type_translation = arm_type_translation
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

  @staticmethod
  def _check_within(*, name: str, value: float, allowed: Tuple[float, float]) -> None:
    """Raise ``ValueError`` unless ``value`` lies in ``allowed``, ends included."""
    low, high = allowed
    if not low <= value <= high:
      raise ValueError(f"{name} must be from {low} to {high}, got {value}")

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
    """Z's speed for a move, in mm/s, within ``Z_SPEED_RANGE``."""
    return self._default_z_speed

  @default_z_speed.setter
  def default_z_speed(self, value: float) -> None:
    self._check_within(name="default_z_speed", value=value, allowed=Z_SPEED_RANGE)
    self._default_z_speed = value

  @property
  def default_z_acceleration(self) -> float:
    """Z's acceleration for a move, and its deceleration at a stop, in mm/s², within
    ``Z_ACCELERATION_RANGE``."""
    return self._default_z_acceleration

  @default_z_acceleration.setter
  def default_z_acceleration(self, value: float) -> None:
    self._check_within(name="default_z_acceleration", value=value, allowed=Z_ACCELERATION_RANGE)
    self._default_z_acceleration = value

  @staticmethod
  def _get_z_device_position(*, z: float) -> float:
    """A height, mm above Z's home flag, as Z's position in device units (down positive)."""
    return compute_device_position(motor=Z_MOTOR, position=0.0 - z)  # 0.0, never -0.0

  def _check_reachable(self, *, motor: int, position: float) -> None:
    """Raise ``ValueError`` unless a motor's target, in device units, is within its limits read.

    Raises:
      MantisConfigurationError: if the motor's limits were not read.
    """
    limits = self._driver.configuration.motor_limits.get(motor)
    if limits is None:
      raise MantisConfigurationError(f"{self._driver.name}: motor {motor}'s limits are not read")
    lower, upper = limits
    if not lower <= position <= upper:
      raise ValueError(
        f"motor {motor}'s target {position:.3f} is outside its limits {lower} to {upper} "
        "(device units)"
      )

  @property
  def default_minimum_traverse_height(self) -> float:
    """The height the head rises to before it travels, in mm above Z's home flag."""
    return self._default_minimum_traverse_height

  @default_minimum_traverse_height.setter
  def default_minimum_traverse_height(self, value: float) -> None:
    if Z_MOTOR in self._driver.configuration.motor_limits:
      self._check_reachable(motor=Z_MOTOR, position=self._get_z_device_position(z=value))
    self._default_minimum_traverse_height = value

  def _get_profile(
    self, *, motor: int, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> Tuple[float, float]:
    """A motor's (speed, acceleration) in device units; each None takes its default.

    Raises:
      ValueError: if an arm's is not above 0, or Z's is outside ``Z_SPEED_RANGE`` or
        ``Z_ACCELERATION_RANGE``.
    """
    if motor == Z_MOTOR:
      speed = self.default_z_speed if speed is None else speed
      acceleration = self.default_z_acceleration if acceleration is None else acceleration
      self._check_within(name="Z speed", value=speed, allowed=Z_SPEED_RANGE)
      self._check_within(name="Z acceleration", value=acceleration, allowed=Z_ACCELERATION_RANGE)
    else:
      speed = self.default_arm_speed if speed is None else speed
      acceleration = self.default_arm_acceleration if acceleration is None else acceleration
      self._check_positive(name="arm speed", value=speed)
      self._check_positive(name="arm acceleration", value=acceleration)
    return (
      compute_device_rate(motor=motor, rate=speed),
      compute_device_rate(motor=motor, rate=acceleration),
    )

  def _get_profiles(
    self,
    *,
    arm_speed: Optional[float] = None,
    arm_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> Dict[int, Tuple[float, float]]:
    """Each motor's (speed, acceleration) in device units, by motor, as ``_get_profile``."""
    return {
      **{
        motor: self._get_profile(motor=motor, speed=arm_speed, acceleration=arm_acceleration)
        for motor in ARM_MOTORS
      },
      Z_MOTOR: self._get_profile(motor=Z_MOTOR, speed=z_speed, acceleration=z_acceleration),
    }

  def _get_route_profiles(self) -> Dict[int, Tuple[float, float]]:
    """Each motor's (speed, acceleration) in device units along a route: the ``ROUTE_*``
    profile, whatever the defaults."""
    return self._get_profiles(
      arm_speed=ROUTE_ARM_SPEED,
      arm_acceleration=ROUTE_ARM_ACCELERATION,
      z_speed=ROUTE_Z_SPEED,
      z_acceleration=ROUTE_Z_ACCELERATION,
    )

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

  def _check_initialized(self) -> None:
    """Raise ``MantisMotionError`` unless the arm was brought up: where it is is not known."""
    if not self._initialized:
      raise MantisMotionError(f"{self._driver.name}: the arm is not brought up; call setup()")

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
  ) -> int:
    """Move one motor (command 22), sent once, in device units; it answers as the move starts.

    Returns the reply's motor error code (``MOTOR_ERROR_CODE_DESCRIPTIONS``), 0 for none.

    Raises:
      FrameError: if the reply is shorter than 2 bytes.
    """
    payload = struct.pack("<hddd", motor, position, speed, acceleration)
    reply = await self._driver._send_command(
      board=MOTION_BOARD, command=22, payload=payload, attempts=1
    )
    if len(reply) < 2:
      raise FrameError(f"{self._driver.name}: a move's reply is 2 bytes, got {reply.hex()}")
    (code,) = struct.unpack_from("<H", reply)
    return int(code)

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

  def _get_arm_targets(self, *, x: float, y: float) -> Dict[int, float]:
    """Each arm motor's target, in device units, for the head at (x, y): the links' angles less
    each motor's offset.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
      MantisKinematicsError: if (x, y) is out of the arm's reach.
    """
    self._check_calibrated()
    assert self.offsets is not None
    angles = compute_joint_angles(x=x, y=y)
    return {
      motor: compute_device_position(motor=motor, position=angles[motor] - self.offsets[motor])
      for motor in ARM_MOTORS
    }

  def _get_idle_targets(self) -> Dict[int, float]:
    """Each arm motor's target at idle, in device units: ``POST_HOMING_POSITION`` less its offset.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
    """
    x, y = POST_HOMING_POSITION
    return self._get_arm_targets(x=x, y=y)

  async def request_head_position(self) -> Coordinate:
    """Where the head is: x and y in the arm frame, mm; z in mm above Z's home flag.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
      MantisKinematicsError: if the links cannot meet at the angles read.
    """
    self._check_calibrated()
    joint_position = await self.request_joint_position()
    x, y = self._get_head_position(joint_position=joint_position)
    return Coordinate(x=x, y=y, z=0.0 - joint_position[Z_MOTOR])  # 0.0, never -0.0

  async def request_is_parked(self, *, tolerance: float = 0.5) -> bool:
    """Whether the head is at idle: each arm motor at its idle target by its absolute encoder, and
    Z at or above its home flag by its encoder (command 84).

    Args:
      tolerance: how far each arm motor may be from its target, in degrees, and Z below its flag,
        in mm.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
    """
    self._check_calibrated()
    idle_targets = self._get_idle_targets()
    absolute = await self.request_absolute_joint_position()
    z = await self._request_measured_position(motor=Z_MOTOR)
    return z <= tolerance and all(
      abs(absolute[motor] - compute_position(motor=motor, device_position=idle_targets[motor]))
      <= tolerance
      for motor in ARM_MOTORS
    )

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

  async def _move_motors(
    self, *, targets: Dict[int, float], profiles: Dict[int, Tuple[float, float]]
  ) -> None:
    """Move each motor to its target, in device units, then wait.

    Each move's reply is checked and its motor read once as it starts, as recorded, then every
    motor until all stop. On any failure the motors sent a move are stopped and where they stopped
    is logged before it raises.

    Args:
      targets: device units, by motor, sent in motor order.
      profiles: each motor's (speed, acceleration) in device units, by motor.

    Raises:
      MantisMotionError: if a motor answers its move with an error code, or reports a fault.
    """
    sent: List[int] = []
    try:
      for motor in sorted(targets):
        speed, acceleration = profiles[motor]
        sent.append(motor)  # before the send: a send that raised may still have started it
        code = await self._unchecked_fw_move_absolute(
          motor=motor, position=targets[motor], speed=speed, acceleration=acceleration
        )
        if code != 0:
          meaning = MOTOR_ERROR_CODE_DESCRIPTIONS.get(code, "a code not seen before")
          raise MantisMotionError(
            f"{self._driver.name}: motor {motor} answered its move with error {code}: {meaning}",
            motor=motor,
            code=code,
          )
        status = await self._driver.request_motor_status(motor=motor)
        self._check_status(motor=motor, status=status)
      await self._wait_for_motors_idle(timeout=MOVE_TIMEOUT)
    except BaseException:
      await self._stop_motors(motors=tuple(sent))
      await self._log_where_it_stopped()
      raise

  def _get_waypoint_targets(self, *, waypoint: Waypoint) -> Dict[int, float]:
    """The motors a waypoint moves and their targets, in device units, each within its limits.

    Raises:
      MantisKinematicsError: if its (x, y) is out of the arm's reach.
      ValueError: if a target is outside its motor's limits.
    """
    x, y, z = waypoint
    targets = self._get_arm_targets(x=x, y=y) if x is not None and y is not None else {}
    if z is not None:
      targets[Z_MOTOR] = compute_device_position(motor=Z_MOTOR, position=z)
    for motor, position in targets.items():
      self._check_reachable(motor=motor, position=position)
    return targets

  def _get_route_targets(self, *, route: List[Waypoint]) -> List[Dict[int, float]]:
    """Each waypoint's targets, as ``_get_waypoint_targets``, all checked before anything moves.

    Raises:
      MantisKinematicsError, ValueError: as ``_get_waypoint_targets``.
    """
    return [self._get_waypoint_targets(waypoint=waypoint) for waypoint in route]

  async def _move_along_route(self, *, steps: List[Dict[int, float]]) -> None:
    """Move to each step's targets in turn at the route profile, each motor on its own (command
    22), as in the recorded recoveries.

    Args:
      steps: each waypoint's targets, from ``_get_route_targets``.
    """
    profiles = self._get_route_profiles()
    for targets in steps:
      if targets:
        await self._move_motors(targets=targets, profiles=profiles)

  def _get_queued_move(
    self, *, targets: Dict[int, float], profiles: Dict[int, Tuple[float, float]]
  ) -> List[Tuple[float, float, float]]:
    """A queued move's (position, speed, acceleration) per motor, device units, motors 0 to 2; a
    motor without a target is held, sent all zeros.

    Args:
      targets: device units, by motor.
      profiles: each motor's (speed, acceleration) in device units, by motor.
    """
    return [
      (targets[motor], *profiles[motor]) if motor in targets else (0.0, 0.0, 0.0)
      for motor in (*ARM_MOTORS, Z_MOTOR)
    ]

  async def _play_queued_moves(self, *, moves: List[List[Tuple[float, float, float]]]) -> None:
    """Clear and start the sequencer, queue each move, then wait until the last is played.

    If the wait fails, the sequencer is aborted so nothing more plays, and where the motors
    stopped is logged.

    Raises:
      MantisMotionError: if a motor reports an error first.
      MantisTimeoutError: if the last move is not reported played within ``MOVE_TIMEOUT`` each.
    """
    if not moves:
      return
    await self._driver._unchecked_fw_clear_sequencer()
    await self._driver._unchecked_fw_start_sequencer()
    try:
      for targets in moves:
        item = await self._driver._queue_move_item(targets=targets)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * len(moves))
    except BaseException:
      try:
        await self._driver._unchecked_fw_abort_sequencer()
      except Exception:
        logger.warning("[%s] could not abort the sequencer", self._driver.name, exc_info=True)
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

  def _get_zone(self, *, x: float, y: float) -> str:
    """The zone whose rectangle holds (x, y), edges included, in ``ZONES`` order; else Unknown.

    Raises:
      MantisConfigurationError: without the unit's ``zones`` block.
    """
    if self.zone_rectangles is None:
      raise MantisConfigurationError(f"{self._driver.name}: no zones for this unit")
    for zone in ZONES:
      minimum_x, maximum_x, minimum_y, maximum_y = self.zone_rectangles[zone]
      if minimum_x <= x <= maximum_x and minimum_y <= y <= maximum_y:
        return zone
    return UNKNOWN_ZONE

  def _get_routes(
    self, *, joint_position: Dict[int, float]
  ) -> Tuple[List[Waypoint], List[Waypoint]]:
    """The routes to safe XY and to idle from where the arm motors' angles put the head.

    The zone's own; without the unit's zones, the Front zone's, from near idle only.

    Args:
      joint_position: each arm motor's angle, in degrees, by motor.

    Raises:
      MantisMotionError: if where the head is is not known, or without zones it is not near idle.
    """
    if self.zone_rectangles is None or self.safe_xy_routes is None or self.idle_routes is None:
      self._check_near_idle(joint_position=joint_position)
      return FRONT_SAFE_XY_ROUTE, FRONT_IDLE_ROUTE
    try:
      x, y = self._get_head_position(joint_position=joint_position)
    except MantisKinematicsError as error:
      raise MantisMotionError(f"{self._driver.name}: where the head is is not known") from error
    zone = self._get_zone(x=x, y=y)
    logger.info(
      "[%s] the head is at (%.3f, %.3f) mm, in the %s zone", self._driver.name, x, y, zone
    )
    return self.safe_xy_routes[zone], self.idle_routes[zone]

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

  async def _recover_to_idle(self) -> None:
    """Bring the head to idle from where the arms are declared, as in a recorded recovery.

    Declare the arms, move them along the zone's route to safe XY, home Z; twice declare them and
    move along the zone's route to idle; home Z. The zone is read again before each route.
    """
    await self._declare_absolute_joint_position()
    safe_xy_route, _ = self._get_routes(joint_position=await self.request_joint_position())
    await self._move_along_route(steps=self._get_route_targets(route=safe_xy_route))
    await self._home_z()
    for _ in range(2):
      await self._declare_absolute_joint_position()
      _, idle_route = self._get_routes(joint_position=await self.request_joint_position())
      await self._move_along_route(steps=self._get_route_targets(route=idle_route))
    await self._home_z()

  async def _unpower_arm_motors(self) -> None:
    """Run both arm motors open loop, unpowered; a failure is logged, each motor in its own try."""
    for motor in ARM_MOTORS:
      try:
        await self._unchecked_fw_set_motor_open_loop(motor=motor, open_loop=True)
      except Exception:
        logger.warning("[%s] could not unpower motor %d", self._driver.name, motor, exc_info=True)

  async def initialize(self) -> None:
    """Bring the arm and Z up from power-on, as in the recorded v4 startup.

    Energise the arm motors, declare them where their absolute encoders read, set their following
    error, then recover to idle: along the zone's routes, or without the unit's zones from near
    idle only. A failure from energising on leaves the arm motors unpowered again.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
      MantisMotionError: if the head is not where it is moved to idle from, or a motor faults.
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
      await self._recover_to_idle()
    except BaseException:
      await self._unpower_arm_motors()
      raise
    self._initialized = True

  async def recover_from_fault(self) -> None:
    """Recover from a motor fault as recorded: stop every motor, clear Z's faults then the arms',
    and recover to idle as ``initialize`` does. This moves the arm.

    A failure leaves the arm motors unpowered; set up again.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
      MantisMotionError: if the arm is not brought up, the head is not where it is moved to idle
        from, or a motor faults.
      MantisTimeoutError: if a move or the home does not end in time.
    """
    self._check_calibrated()
    self._check_initialized()
    self._initialized = False
    try:
      for motor in (*ARM_MOTORS, Z_MOTOR):
        await self._unchecked_fw_stop(
          motor=motor, deceleration=self._get_device_acceleration(motor=motor)
        )
      for motor in (Z_MOTOR, *ARM_MOTORS):
        await self._unchecked_fw_clear_motor_faults(motor=motor)
      await self._recover_to_idle()
    except BaseException:
      await self._unpower_arm_motors()
      raise
    self._initialized = True

  async def _rise_to_traverse_height(
    self, *, minimum_traverse_height: float, profiles: Dict[int, Tuple[float, float]]
  ) -> None:
    """Rise Z to the traverse height if its encoder reads it below it, then read Z back at or
    above it by its encoder (command 84), never the demand position.

    Args:
      minimum_traverse_height: in mm above Z's home flag, within Z's limits.
      profiles: each motor's (speed, acceleration) in device units, by motor.

    Raises:
      MantisMotionError: if Z answers the move with an error code, faults, or reads more than
        ``Z_TRAVERSE_TOLERANCE`` below the height after it.
    """
    traverse = self._get_z_device_position(z=minimum_traverse_height)
    measured = await self._request_measured_position(motor=Z_MOTOR)
    if compute_device_position(motor=Z_MOTOR, position=measured) <= traverse:
      return
    await self._move_motors(targets={Z_MOTOR: traverse}, profiles=profiles)
    z = 0.0 - await self._request_measured_position(motor=Z_MOTOR)  # 0.0, never -0.0
    if z < minimum_traverse_height - Z_TRAVERSE_TOLERANCE:
      raise MantisMotionError(
        f"{self._driver.name}: Z reads {z:.3f} mm by its encoder after rising to "
        f"{minimum_traverse_height} mm; the arms are not moved",
        motor=Z_MOTOR,
      )

  async def move_z(
    self, z: float, *, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> None:
    """Move Z alone (command 22); the arms hold. Checked against Z's limits before it is sent.

    Args:
      z: the height, in mm above Z's home flag.
      speed: in mm/s; None takes ``default_z_speed``.
      acceleration: in mm/s²; None takes ``default_z_acceleration``.

    Raises:
      MantisMotionError: if the arm is not brought up, or Z faults.
      ValueError: if ``z`` is outside Z's limits, or a speed or acceleration out of range.
    """
    self._check_initialized()
    target = self._get_z_device_position(z=z)
    self._check_reachable(motor=Z_MOTOR, position=target)
    profile = self._get_profile(motor=Z_MOTOR, speed=speed, acceleration=acceleration)
    await self._move_motors(targets={Z_MOTOR: target}, profiles={Z_MOTOR: profile})

  async def move_to_joint_position(
    self,
    position: Dict[int, float],
    *,
    arm_speed: Optional[float] = None,
    arm_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Move the motors named together in one queued move; the others hold. No traverse height.

    The arms move in one joint move, not along a route: nothing here avoids the chip racks.

    Args:
      position: each motor's target, by motor: 0 and 1 in degrees, 2 (Z) in mm down from its flag,
        as ``request_joint_position`` reads them.
      arm_speed: in degrees/s; None takes ``default_arm_speed``.
      arm_acceleration: in degrees/s²; None takes ``default_arm_acceleration``.
      z_speed: in mm/s; None takes ``default_z_speed``.
      z_acceleration: in mm/s²; None takes ``default_z_acceleration``.

    Raises:
      MantisMotionError: if the arm is not brought up, or a motor faults.
      MantisKinematicsError: if the links cannot meet at the arm motors' targets.
      ValueError: if a motor is unknown, a target outside its limits, or a speed or acceleration
        out of range.
    """
    self._check_initialized()
    unknown = set(position) - {*ARM_MOTORS, Z_MOTOR}
    if unknown:
      raise ValueError(f"the motors are {(*ARM_MOTORS, Z_MOTOR)}, got {sorted(unknown)}")
    profiles = self._get_profiles(
      arm_speed=arm_speed,
      arm_acceleration=arm_acceleration,
      z_speed=z_speed,
      z_acceleration=z_acceleration,
    )
    targets = {
      motor: compute_device_position(motor=motor, position=value)
      for motor, value in position.items()
    }
    for motor, target in targets.items():
      self._check_reachable(motor=motor, position=target)
    if any(motor in position for motor in ARM_MOTORS):
      self._get_head_position(joint_position={**await self.request_joint_position(), **position})
    await self._play_queued_moves(moves=[self._get_queued_move(targets=targets, profiles=profiles)])

  async def move_to_location(
    self,
    location: Coordinate,
    *,
    arm_speed: Optional[float] = None,
    arm_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
    minimum_traverse_height: Optional[float] = None,
  ) -> None:
    """Move the head to a location: rise to the traverse height if below it, move the arms with Z
    held (one queued move), then move Z to the location's height.

    Every target is checked before anything moves, and Z is read back at the traverse height
    before the arms move. The arms move in one joint move, not along a route: nothing here avoids
    the chip racks.

    Args:
      location: x and y in the arm frame, mm; z in mm above Z's home flag.
      arm_speed: in degrees/s; None takes ``default_arm_speed``.
      arm_acceleration: in degrees/s²; None takes ``default_arm_acceleration``.
      z_speed: in mm/s; None takes ``default_z_speed``.
      z_acceleration: in mm/s²; None takes ``default_z_acceleration``.
      minimum_traverse_height: the height the head travels at or above, in mm above Z's home flag;
        None takes ``default_minimum_traverse_height``.

    Raises:
      MantisMotionError: if the arm is not brought up, a motor faults, or Z does not read at the
        traverse height after rising to it.
      MantisKinematicsError: if (x, y) is out of the arm's reach.
      ValueError: if a target is outside its motor's limits, or a speed or acceleration out of
        range.
    """
    self._check_initialized()
    if minimum_traverse_height is None:
      minimum_traverse_height = self.default_minimum_traverse_height
    profiles = self._get_profiles(
      arm_speed=arm_speed,
      arm_acceleration=arm_acceleration,
      z_speed=z_speed,
      z_acceleration=z_acceleration,
    )
    arms = self._get_arm_targets(x=location.x, y=location.y)
    z_target = self._get_z_device_position(z=location.z)
    traverse = self._get_z_device_position(z=minimum_traverse_height)
    for motor, target in (*arms.items(), (Z_MOTOR, z_target), (Z_MOTOR, traverse)):
      self._check_reachable(motor=motor, position=target)
    await self._rise_to_traverse_height(
      minimum_traverse_height=minimum_traverse_height, profiles=profiles
    )
    await self._play_queued_moves(moves=[self._get_queued_move(targets=arms, profiles=profiles)])
    await self._move_motors(targets={Z_MOTOR: z_target}, profiles=profiles)

  def _check_stage(self) -> None:
    """Raise ``MantisConfigurationError`` unless the unit's ``stage`` block was given."""
    if any(
      value is None
      for value in (
        self.stage_origin,
        self.stage_transformation_matrix,
        self.stage_offset_x,
        self.stage_offset_y,
        self.arm_type_rotation_degree,
        self.arm_type_translation,
      )
    ):
      raise MantisConfigurationError(
        f"{self._driver.name}: no stage transform for this unit; give its configuration folder "
        "(oem_config_directory) or a configuration saved with it"
      )

  def _get_arm_frame_location(self, *, x: float, y: float, height: float) -> Coordinate:
    """Where the head goes for a point on the wellplate: x and y in the arm frame, z above Z's flag.

    Args:
      x: right of the wellplate's back-left corner, mm.
      y: in front of the wellplate's back-left corner, mm.
      height: above the wellplate's base, mm.

    Raises:
      MantisConfigurationError: without the unit's ``stage`` block.
      MantisKinematicsError: if the stage transform maps the point to infinity.
    """
    self._check_stage()
    assert self.stage_origin is not None and self.stage_transformation_matrix is not None
    assert self.stage_offset_x is not None and self.stage_offset_y is not None
    assert self.arm_type_rotation_degree is not None and self.arm_type_translation is not None
    arm_x, arm_y, z = compute_arm_frame_position(
      position=(x, y, REFERENCE_HEIGHT - height),
      origin=self.stage_origin,
      transformation_matrix=self.stage_transformation_matrix,
      offset_x=self.stage_offset_x,
      offset_y=self.stage_offset_y,
      arm_type_rotation_degree=self.arm_type_rotation_degree,
      arm_type_translation=self.arm_type_translation,
    )
    return Coordinate(x=arm_x, y=arm_y, z=0.0 - z)  # 0.0, never -0.0

  @staticmethod
  def _get_wellplate(*, resource: Resource) -> Plate:
    """The wellplate a resource is, or is on.

    Raises:
      MantisCoordinateError: if it is neither, or the wellplate is turned on its holder.
    """
    wellplate: Optional[Resource] = resource
    while wellplate is not None and not isinstance(wellplate, Plate):
      wellplate = wellplate.parent
    if wellplate is None:
      raise MantisCoordinateError(f"{resource.name} is not a wellplate or on one")
    rotation = wellplate.rotation
    if (rotation.x, rotation.y, rotation.z) != (0, 0, 0):
      raise MantisCoordinateError(
        f"{wellplate.name} is turned on its holder; the stage transform takes A1 at the back left"
      )
    return wellplate

  @staticmethod
  def _get_wellplate_top(*, wellplate: Plate) -> float:
    """The wellplate's tallest point above its base, mm: its frame, a well or its lid."""
    return max(
      [
        wellplate.get_size_z(),
        *(child.get_location_wrt(wellplate, z="t").z for child in wellplate.children),
      ]
    )

  async def move_to_resource_location(
    self,
    resource: Resource,
    *,
    offset: Optional[Coordinate] = None,
    minimum_traverse_height: Optional[float] = None,
  ) -> None:
    """Move the head over a resource on the wellplate in the plate holder, through the unit's
    stage transform, as ``move_to_location`` moves; heights are mm above the wellplate's base.

    The head goes over the resource's centre ``PITCH_HEIGHT`` above the wellplate's tallest point,
    plus ``offset``. The wellplate is taken to be clamped in the holder, A1 at the back left,
    wherever the resource model places it.

    Args:
      resource: a wellplate, or a resource on one, such as a well.
      offset: added in the wellplate's frame (x right, y back, z up), mm; None adds nothing.
      minimum_traverse_height: the height to travel at or above, mm above the wellplate's base (not
        Z's home flag), not below its tallest point; None takes its tallest point plus
        ``PITCH_HEIGHT``.

    Raises:
      MantisConfigurationError: without the unit's ``stage`` block; nothing is sent.
      MantisCoordinateError: if ``resource`` is not a wellplate or on one, or it is turned.
      MantisMotionError: if the arm is not brought up, a motor faults, or Z does not read at the
        traverse height after rising to it.
      MantisKinematicsError: if the target is out of the arm's reach.
      ValueError: if ``minimum_traverse_height`` is below the wellplate's tallest point, or a
        target is outside its motor's limits.
    """
    self._check_initialized()
    self._check_stage()
    wellplate = self._get_wellplate(resource=resource)
    top = self._get_wellplate_top(wellplate=wellplate)
    if minimum_traverse_height is None:
      minimum_traverse_height = top + PITCH_HEIGHT
    elif minimum_traverse_height < top:
      raise ValueError(
        f"minimum_traverse_height {minimum_traverse_height} mm is below {wellplate.name}'s "
        f"tallest point, {top} mm above its base"
      )
    offset = offset or Coordinate.zero()
    if resource is wellplate:
      center_x, center_y = wellplate.get_size_x() / 2, wellplate.get_size_y() / 2
    else:
      center = resource.get_location_wrt(wellplate, x="c", y="c")
      center_x, center_y = center.x, center.y
    # The wellplate frame has y to the front, from the back edge: PyLabRobot's has it to the back.
    x, y = center_x + offset.x, wellplate.get_size_y() - (center_y + offset.y)
    location = self._get_arm_frame_location(x=x, y=y, height=top + PITCH_HEIGHT + offset.z)
    traverse = self._get_arm_frame_location(x=x, y=y, height=minimum_traverse_height)
    await self.move_to_location(location, minimum_traverse_height=traverse.z)

  async def move_through_waypoints(
    self,
    waypoints: List[Coordinate],
    *,
    arm_speed: Optional[float] = None,
    arm_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Move the head straight to each location in turn, all three motors in one queued move each,
    queued together; no traverse height. Every target is checked before anything moves.

    The arms move in joint moves, not along a route: nothing here avoids the chip racks.

    Args:
      waypoints: x and y in the arm frame, mm; z in mm above Z's home flag.
      arm_speed: in degrees/s; None takes ``default_arm_speed``.
      arm_acceleration: in degrees/s²; None takes ``default_arm_acceleration``.
      z_speed: in mm/s; None takes ``default_z_speed``.
      z_acceleration: in mm/s²; None takes ``default_z_acceleration``.

    Raises:
      MantisMotionError: if the arm is not brought up, or a motor faults.
      MantisKinematicsError: if an (x, y) is out of the arm's reach.
      ValueError: if there is no waypoint, a target is outside its motor's limits, or a speed or
        acceleration out of range.
    """
    self._check_initialized()
    if not waypoints:
      raise ValueError("move_through_waypoints needs one waypoint or more")
    profiles = self._get_profiles(
      arm_speed=arm_speed,
      arm_acceleration=arm_acceleration,
      z_speed=z_speed,
      z_acceleration=z_acceleration,
    )
    moves = []
    for location in waypoints:
      targets = {
        **self._get_arm_targets(x=location.x, y=location.y),
        Z_MOTOR: self._get_z_device_position(z=location.z),
      }
      for motor, target in targets.items():
        self._check_reachable(motor=motor, position=target)
      moves.append(self._get_queued_move(targets=targets, profiles=profiles))
    await self._play_queued_moves(moves=moves)

  async def _route_to_idle(self, *, minimum_traverse_height: float) -> None:
    """Rise to the traverse height if below it, then move along the route to idle from where the
    absolute encoders put the head, at the route profile.

    Raises:
      MantisMotionError: if a motor faults, Z does not read at the traverse height after rising,
        where the head is is not known, or without the unit's zones it is not near idle.
      ValueError: if the traverse height is outside Z's limits.
    """
    traverse = self._get_z_device_position(z=minimum_traverse_height)
    self._check_reachable(motor=Z_MOTOR, position=traverse)
    await self._wait_for_motors_idle(timeout=MOVE_TIMEOUT)
    _, idle_route = self._get_routes(joint_position=await self.request_absolute_joint_position())
    steps = self._get_route_targets(route=idle_route)
    await self._rise_to_traverse_height(
      minimum_traverse_height=minimum_traverse_height, profiles=self._get_route_profiles()
    )
    await self._move_along_route(steps=steps)

  async def park(self, *, minimum_traverse_height: Optional[float] = None) -> None:
    """Move the head to idle along its zone's route, from any zone.

    The head first rises to the traverse height if it is below it. Without the unit's zones it is
    parked from near idle only.

    Args:
      minimum_traverse_height: in mm above Z's home flag; None takes
        ``default_minimum_traverse_height``.

    Raises:
      MantisMotionError: if the arm is not brought up, a motor faults, Z does not read at the
        traverse height after rising, or without the unit's zones the head is not near idle.
      ValueError: if the traverse height is outside Z's limits.
    """
    self._check_initialized()
    if minimum_traverse_height is None:
      minimum_traverse_height = self.default_minimum_traverse_height
    await self._route_to_idle(minimum_traverse_height=minimum_traverse_height)

  async def _move_to_station(self, *, name: str, route: Optional[List[Waypoint]]) -> None:
    """Play a station's route from idle, one queued move per line, the axes it leaves out held.

    Raises:
      MantisConfigurationError: without the route.
      MantisMotionError: if the arm is not brought up or not parked, or a motor faults.
    """
    self._check_initialized()
    if route is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no route to the {name} for this unit; give its configuration folder "
        "(oem_config_directory) or a configuration saved with it"
      )
    profiles = self._get_route_profiles()
    moves = [
      self._get_queued_move(targets=targets, profiles=profiles)
      for targets in self._get_route_targets(route=route)
    ]
    if not await self.request_is_parked():
      raise MantisMotionError(
        f"{self._driver.name}: the route to the {name} starts at idle; park() first"
      )
    await self._play_queued_moves(moves=moves)

  async def move_to_waste_station(self) -> None:
    """Move the head from idle into the waste station along the unit's route.

    Raises:
      MantisConfigurationError: without the unit's route; nothing moves.
      MantisMotionError: if the arm is not brought up or the head not at idle, or a motor faults.
    """
    await self._move_to_station(name="waste station", route=self.waste_station_route)

  async def move_to_wash_station(self, *, station: int) -> None:
    """Move the head from idle into a wash station along the unit's route.

    Args:
      station: 1 or 2.

    Raises:
      MantisConfigurationError: without the unit's route; nothing moves.
      MantisMotionError: if the arm is not brought up or the head not at idle, or a motor faults.
      ValueError: if ``station`` is not 1 or 2.
    """
    if station not in WASH_STATIONS:
      raise ValueError(f"station must be one of {WASH_STATIONS}, got {station}")
    routes = self.wash_station_routes or {}
    await self._move_to_station(name=f"wash station {station}", route=routes.get(station))

  async def _park(self) -> None:
    """Park at idle and lower Z to ``SHUTDOWN_Z``, as in the recorded close.

    From away from idle the head first moves to idle along its zone's route; without the unit's
    zones it is parked from near idle only. Then Z up on its own (command 22), read back by its
    encoder; then, queued, the arms with Z held, both; then Z down at idle. Where the head is comes
    from the absolute encoders. Every target is checked against its motor's limits first.

    Raises:
      MantisMotionError: if a motor reports a fault, or without zones the head is not near idle
        (nothing moves), or Z does not read up within ``Z_TRAVERSE_TOLERANCE`` after rising (the
        arms are not moved).
      ValueError: if a target is outside its motor's limits; nothing moves.
      MantisTimeoutError: if a motor still moves, or a queued move is not reported played, in time.
    """
    idle_targets = self._get_waypoint_targets(waypoint=(*POST_HOMING_POSITION, None))
    z_up = self._get_waypoint_targets(waypoint=(None, None, 0.0))
    z_down = self._get_waypoint_targets(waypoint=(None, None, SHUTDOWN_Z))
    await self._wait_for_motors_idle(timeout=MOVE_TIMEOUT)
    try:
      self._check_near_idle(joint_position=await self.request_absolute_joint_position())
    except MantisMotionError:
      if self.zone_rectangles is None:
        raise
      await self._route_to_idle(minimum_traverse_height=self.default_minimum_traverse_height)
    profiles = self._get_route_profiles()
    await self._move_motors(targets=z_up, profiles=profiles)
    z = await self._request_measured_position(motor=Z_MOTOR)
    if z > Z_TRAVERSE_TOLERANCE:
      raise MantisMotionError(
        f"{self._driver.name}: Z reads {z:.3f} mm below its flag by its encoder after rising to "
        "it; the arms are not moved",
        motor=Z_MOTOR,
      )
    await self._play_queued_moves(
      moves=[
        self._get_queued_move(targets=idle_targets, profiles=profiles),
        self._get_queued_move(targets={**idle_targets, **z_up}, profiles=profiles),
      ]
    )
    await self._play_queued_moves(
      moves=[self._get_queued_move(targets={**idle_targets, **z_down}, profiles=profiles)]
    )

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

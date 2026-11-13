"""The arm: motors 0 and 1 turn a five-bar linkage that carries the head, and motor 2 drives Z.

On a v4, board 11 reads each arm motor's absolute encoder, so where the arm stands is known after
power-on: the arms are declared there, never homed. Z homes on its flag. Every bring-up step and
its order matches the recorded v4 startup.

The head goes to idle along the unit's routes: the zone it stands in names a route to a safe XY
and one to idle. Without them, only from near idle. The nozzle moves in the deck frame, through
the unit's stage transform over the plate holder and the arm frame fitted to it elsewhere.
"""

import asyncio
import dataclasses
import logging
import math
import struct
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from pylabrobot.resources import Coordinate

from ...resource_model import (
  ARM_BASE_X,
  ARM_BASE_Y,
  ARM_Z,
  PLATE_HOLDER_LOCATION,
  PLATE_HOLDER_SIZE_X,
  PLATE_HOLDER_SIZE_Y,
  SIZE_Z,
)
from ..arm_kinematics import (
  compute_absolute_device_position,
  compute_device_position,
  compute_device_rate,
  compute_elbow_positions,
  compute_head_position,
  compute_joint_angles,
  compute_position,
)
from ..errors import (
  MOTOR_ERROR_CODE_DESCRIPTIONS,
  MOTOR_STATUS_ERROR_BITS,
  FrameError,
  MantisConfigurationError,
  MantisKinematicsError,
  MantisMotionError,
  MantisTimeoutError,
)
from ..events import HOME_DONE_EVENT, decode_home_done
from ..framing import MOTION_BOARD
from ..oem_config import (
  UNKNOWN_ZONE,
  WASH_APPROACH_PATH,
  WASH_POSITIONS,
  WASTE_APPROACH_PATH,
  ZONES,
)
from ..sequence_files import RouteLine
from ..stage_transform import compute_arm_frame_position

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
FRONT_SAFE_XY_ROUTE: List[RouteLine] = [(*POST_HOMING_POSITION, None)]
FRONT_IDLE_ROUTE: List[RouteLine] = [
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
# PyLabRobot's allow-list for Z, mm/s and mm/s²: the recorded moves' range (the jog's 1 to 20 mm/s
# at 500 mm/s², the routes' 55 mm/s at 200 mm/s²); other values have not run on a device.
Z_SPEED_RANGE = (1.0, 55.0)
Z_ACCELERATION_RANGE = (200.0, 500.0)
# The route profile, in every recorded recovery, close, route and queued move: the arms in
# degrees/s and degrees/s², Z in mm/s and mm/s². Routes keep it; the arms take it by default.
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
# The safe height, mm above Z's flag: the routes' Z -1.570.
SAFE_HEIGHT = 1.57
# PyLabRobot design choice: the plate holder is sampled every 5 mm to fit the deck frame to the arm
# frame.
DECK_FIT_SPACING = 5.0
# How far outside the plate holder, mm, a point read back through the inverted stage transform may
# land and still count as on it: rounding only.
HOLDER_EDGE_TOLERANCE = 1e-6
# PyLabRobot design choice: how close, mm, the head must stand to a route line below the safe height on
# an approach path, in x, y and Z, to count as on that path.
APPROACH_PATH_TOLERANCE = 0.5

JointState = Dict[int, float]
"""Where each motor is, by motor: 0 and 1 in degrees, 2 (Z) in mm down from its flag."""


@dataclasses.dataclass(frozen=True)
class MantisArmConfiguration:
  """The arm's facts, read at setup, and the unit's calibration, from its folder or declared file.

  Each calibration value is None without its block (``arm``, ``zones``, ``approach_paths``,
  ``stage``).
  """

  motor_limits: Dict[int, Tuple[float, float]] = dataclasses.field(default_factory=dict)
  """Each motor's (lower, upper) position limit, by motor, in device units, as read at setup."""
  offsets: Optional[Dict[int, float]] = None
  """Each arm motor's offset, degrees, added to its angle for the geometry's own."""
  homing_position_a: Optional[Dict[int, float]] = None
  """Each arm motor's encoder reference, in the absolute encoder's units."""
  encoder_correction: Optional[Dict[int, List[Tuple[float, float]]]] = None
  """Each arm motor's (ideal, measured) correction rows, measured ascending."""
  zone_rectangles: Optional[Dict[str, Tuple[float, float, float, float]]] = None
  """Each zone's (minimum x, maximum x, minimum y, maximum y) in the arm frame, mm, by name."""
  safe_xy_routes: Optional[Dict[str, List[RouteLine]]] = None
  """Each zone's route to its safe XY, the arms only, by name; Unknown included."""
  idle_routes: Optional[Dict[str, List[RouteLine]]] = None
  """Each zone's route to idle, by name; Unknown included."""
  approach_paths: Optional[Dict[str, List[RouteLine]]] = None
  """The unit's path from idle to each place it serves, by name: ``"waste"``, ``"wash_1"``,
  ``"wash_2"``; arm frame, Z mm down from its flag, an axis left out held."""
  stage_origin: Optional[Tuple[float, float, float]] = None
  """The plate holder's origin, (x, y, z), mm."""
  stage_transformation_matrix: Optional[Sequence[Sequence[float]]] = None
  """The stage's projective matrix, 3 by 3."""
  stage_offset_x: Optional[Tuple[float, float, float]] = None
  """The stage's x offset, (x, y, z); its z tilts the stage."""
  stage_offset_y: Optional[Tuple[float, float, float]] = None
  """The stage's y offset, (x, y, z); its z tilts the stage."""
  arm_type_rotation_degree: Optional[float] = None
  """The arm type's rotation, degrees."""
  arm_type_translation: Optional[Tuple[float, float, float]] = None
  """The arm type's translation, (x, y, z), mm."""
  stage_reference_height: Optional[float] = None
  """The wellplate frame's z origin above the plate holder, mm (``StageTuning.config``)."""


@dataclasses.dataclass(frozen=True)
class MantisPose:
  """Where every joint of the arm is and where the nozzle is, in the deck frame, mm.

  One answer for the whole arm rather than for its end: whether the arm clears something depends
  on its elbows as much as on the nozzle.
  """

  right_elbow_joint_location: Coordinate
  """Where motor 0's short link ends: the joint the right long link turns about."""
  left_elbow_joint_location: Coordinate
  """Where motor 1's short link ends: the joint the left long link turns about."""
  head_joint_location: Coordinate
  """Where the two long links meet, at the links' height."""
  nozzle_location: Coordinate
  """The nozzle's tip, the tool center point: under the head joint."""
  joints: JointState
  """What each motor reported, as ``request_joint_state`` returns it."""


class Arm:
  """The arm and Z, built when board 11 answers (a v4).

  Bringing it up needs the unit's ``arm`` block: each arm motor's offset, encoder reference and
  encoder correction. Without it nothing is sent and ``initialize`` refuses; it never falls back to
  another unit's values. The ``zones`` and ``approach_paths`` blocks give the routes; without them
  the head is moved to idle only from near it, and the waste and the washes refuse. Without the ``stage`` block the
  deck-frame moves and ``request_pose`` refuse. Routes (bring-up, park, the approach paths, stop) move at the
  ``ROUTE_*`` profile; the other moves take the arms at it unless an xy speed is given.

  The public moves take the nozzle in the deck frame, mm, and its speed in mm/s; a joint state
  holds each motor as it reads, Z in mm down from its flag.

  After each move, and where a failed one stopped, the measured pose goes to ``_pose_observer`` if
  one is set: the two links' angles and Z's height, as keywords ``theta_1``, ``theta_2`` and ``z``.
  """

  # Where the ``default_*`` properties start: the arms at the route profile (None), Z at the
  # route profile, and the safe height (None).
  _default_xy_speed: Optional[float] = None
  _default_xy_acceleration: Optional[float] = None
  _default_z_speed: float = ROUTE_Z_SPEED
  _default_z_acceleration: float = ROUTE_Z_ACCELERATION
  _default_minimum_traverse_height: Optional[float] = None

  def __init__(self, *, driver: "MantisDriver", configuration: MantisArmConfiguration):
    """
    Args:
      driver: the driver it sends through.
      configuration: the arm's facts and the unit's calibration, as discovery reads them.
    """
    self._driver = driver
    self.configuration = configuration
    self._initialized = False
    self._pose_observer: Optional[Callable[..., None]] = None
    self._deck_to_arm_transform: Optional[Tuple[float, float, float]] = None

  @property
  def is_initialized(self) -> bool:
    """Whether ``initialize`` brought the arm up since the link opened."""
    return self._initialized

  @staticmethod
  def _check_positive(*, name: str, value: float) -> None:
    """Raise ``ValueError`` unless ``value`` is finite and above 0: the device reports no range of
    its own."""
    if not (math.isfinite(value) and value > 0):
      raise ValueError(f"{name} must be finite and above 0, got {value}")

  @staticmethod
  def _check_within(*, name: str, value: float, allowed: Tuple[float, float]) -> None:
    """Raise ``ValueError`` unless ``value`` lies in ``allowed``, ends included."""
    low, high = allowed
    if not low <= value <= high:
      raise ValueError(f"{name} must be from {low} to {high}, got {value}")

  @property
  def default_xy_speed(self) -> Optional[float]:
    """The nozzle's speed across the deck for a move, mm/s; None takes the route profile."""
    return self._default_xy_speed

  @default_xy_speed.setter
  def default_xy_speed(self, value: Optional[float]) -> None:
    if value is not None:
      self._check_positive(name="default_xy_speed", value=value)
    self._default_xy_speed = value

  @property
  def default_xy_acceleration(self) -> Optional[float]:
    """The nozzle's acceleration across the deck for a move, mm/s²; None takes the route
    profile."""
    return self._default_xy_acceleration

  @default_xy_acceleration.setter
  def default_xy_acceleration(self, value: Optional[float]) -> None:
    if value is not None:
      self._check_positive(name="default_xy_acceleration", value=value)
    self._default_xy_acceleration = value

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
    limits = self.configuration.motor_limits.get(motor)
    if limits is None:
      raise MantisConfigurationError(f"{self._driver.name}: motor {motor}'s limits are not read")
    lower, upper = limits
    if not lower <= position <= upper:
      raise ValueError(
        f"motor {motor}'s target {position:.3f} is outside its limits {lower} to {upper} "
        "(device units)"
      )

  @property
  def default_minimum_traverse_height(self) -> Optional[float]:
    """The nozzle's height above the deck it rises to before it moves across, mm, where a move's
    ``minimum_traverse_height_start`` is None; None takes the safe height, ``SAFE_HEIGHT``
    above Z's flag."""
    return self._default_minimum_traverse_height

  @default_minimum_traverse_height.setter
  def default_minimum_traverse_height(self, value: Optional[float]) -> None:
    if value is not None and not math.isfinite(value):
      raise ValueError(f"default_minimum_traverse_height must be finite, got {value}")
    self._default_minimum_traverse_height = value

  def _get_profile(
    self, *, motor: int, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> Tuple[float, float]:
    """A motor's (speed, acceleration) in device units: an arm motor's in degrees/s and degrees/s²,
    each None the route profile's; Z's in mm/s and mm/s², each None its default.

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
      speed = ROUTE_ARM_SPEED if speed is None else speed
      acceleration = ROUTE_ARM_ACCELERATION if acceleration is None else acceleration
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
    """A motor's acceleration where no move gives one, in device units: the route profile's on the arms,
    ``default_z_acceleration`` on Z; a stop outside a move decelerates at it."""
    acceleration = self.default_z_acceleration if motor == Z_MOTOR else ROUTE_ARM_ACCELERATION
    return compute_device_rate(motor=motor, rate=acceleration)

  def _check_calibrated(self) -> None:
    """Raise ``MantisConfigurationError`` unless the unit's ``arm`` block was given."""
    if (
      self.configuration.offsets is None
      or self.configuration.homing_position_a is None
      or self.configuration.encoder_correction is None
    ):
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
    assert (
      self.configuration.encoder_correction is not None
      and self.configuration.homing_position_a is not None
    )
    reading = await self._driver.request_absolute_encoder_position(motor=motor)
    return compute_absolute_device_position(
      motor=motor,
      reading=reading,
      correction=self.configuration.encoder_correction[motor],
      homing_position_a=self.configuration.homing_position_a[motor],
    )

  async def request_absolute_joint_state(self) -> JointState:
    """Each arm motor's angle from its absolute encoder, in degrees, by motor; nothing moves.

    Kept across power-off, unlike ``request_joint_state`` before the arms are declared.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block; nothing is sent.
    """
    return {
      motor: compute_position(
        motor=motor, device_position=await self._request_absolute_device_position(motor=motor)
      )
      for motor in ARM_MOTORS
    }

  async def request_joint_state(self) -> JointState:
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

  def _get_link_angles(self, *, joint_state: JointState) -> Tuple[float, float]:
    """The two links' angles, the geometry's own, degrees: each arm motor's angle plus its offset.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
    """
    self._check_calibrated()
    assert self.configuration.offsets is not None
    return (
      joint_state[0] + self.configuration.offsets[0],
      joint_state[1] + self.configuration.offsets[1],
    )

  def _get_head_position(self, *, joint_state: JointState) -> Tuple[float, float]:
    """Where the head is in the arm frame, mm, for the arm motors' angles in degrees.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
      MantisKinematicsError: if the links cannot meet at those angles.
    """
    theta_1, theta_2 = self._get_link_angles(joint_state=joint_state)
    return compute_head_position(theta_1=theta_1, theta_2=theta_2)

  def _get_arm_targets(self, *, x: float, y: float) -> Dict[int, float]:
    """Each arm motor's target, in device units, for the head at (x, y): the links' angles less
    each motor's offset.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
      MantisKinematicsError: if (x, y) is out of the arm's reach.
    """
    self._check_calibrated()
    assert self.configuration.offsets is not None
    angles = compute_joint_angles(x=x, y=y)
    return {
      motor: compute_device_position(
        motor=motor, position=angles[motor] - self.configuration.offsets[motor]
      )
      for motor in ARM_MOTORS
    }

  def _get_idle_targets(self) -> Dict[int, float]:
    """Each arm motor's target at idle, in device units: ``POST_HOMING_POSITION`` less its offset.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` block.
    """
    x, y = POST_HOMING_POSITION
    return self._get_arm_targets(x=x, y=y)

  def _check_stage(self) -> None:
    """Raise ``MantisConfigurationError`` unless the unit's ``stage`` block was given."""
    if any(
      value is None
      for value in (
        self.configuration.stage_origin,
        self.configuration.stage_transformation_matrix,
        self.configuration.stage_offset_x,
        self.configuration.stage_offset_y,
        self.configuration.arm_type_rotation_degree,
        self.configuration.arm_type_translation,
        self.configuration.stage_reference_height,
      )
    ):
      raise MantisConfigurationError(
        f"{self._driver.name}: no stage transform for this unit; give its configuration folder "
        "(oem_config_directory) or a configuration saved with it"
      )

  def _get_stage_position(self, *, x: float, y: float, z: float) -> Tuple[float, float, float]:
    """A point of the wellplate frame through the unit's stage transform: x and y in the arm frame,
    mm; z in mm down from Z's flag.

    Args:
      x: right of the plate holder's back-left corner, mm.
      y: in front of the plate holder's back-left corner, mm.
      z: down from the stage's reference height above the plate holder, mm.

    Raises:
      MantisConfigurationError: without the unit's ``stage`` block.
      MantisKinematicsError: if the stage transform maps the point to infinity.
    """
    self._check_stage()
    assert (
      self.configuration.stage_origin is not None
      and self.configuration.stage_transformation_matrix is not None
    )
    assert (
      self.configuration.stage_offset_x is not None
      and self.configuration.stage_offset_y is not None
    )
    assert (
      self.configuration.arm_type_rotation_degree is not None
      and self.configuration.arm_type_translation is not None
    )
    return compute_arm_frame_position(
      position=(x, y, z),
      origin=self.configuration.stage_origin,
      transformation_matrix=self.configuration.stage_transformation_matrix,
      offset_x=self.configuration.stage_offset_x,
      offset_y=self.configuration.stage_offset_y,
      arm_type_rotation_degree=self.configuration.arm_type_rotation_degree,
      arm_type_translation=self.configuration.arm_type_translation,
    )

  def _get_deck_to_arm_transform(self) -> Tuple[float, float, float]:
    """The turn, in radians, and the shift (x, y), in mm, that best put the resource model's arm
    frame on the unit's, fitted over the plate holder through the stage transform.

    The resource model places the arm by its base from the manufacturer's model; units differ from
    it by millimetres, so each unit's placement is fitted from its own calibration.

    Fitted once: the configuration does not change.

    Raises:
      MantisConfigurationError: without the unit's ``stage`` block.
    """
    if self._deck_to_arm_transform is not None:
      return self._deck_to_arm_transform

    def get_samples(size: float) -> List[float]:
      # Every DECK_FIT_SPACING from 0, and the far edge.
      count = math.ceil(size / DECK_FIT_SPACING)
      return [min(index * DECK_FIT_SPACING, size) for index in range(count + 1)]

    pairs = []
    for x in get_samples(PLATE_HOLDER_SIZE_X):
      for y in get_samples(PLATE_HOLDER_SIZE_Y):
        model = (
          PLATE_HOLDER_LOCATION.x + x - ARM_BASE_X,
          ARM_BASE_Y - (PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - y),
        )
        unit_x, unit_y, _ = self._get_stage_position(x=x, y=y, z=0.0)
        pairs.append((model, (unit_x, unit_y)))
    count = len(pairs)
    model_x = sum(model[0] for model, _ in pairs) / count
    model_y = sum(model[1] for model, _ in pairs) / count
    unit_x = sum(unit[0] for _, unit in pairs) / count
    unit_y = sum(unit[1] for _, unit in pairs) / count
    cross = dot = 0.0
    for (mx, my), (ux, uy) in pairs:
      mx, my, ux, uy = mx - model_x, my - model_y, ux - unit_x, uy - unit_y
      cross += mx * uy - my * ux
      dot += mx * ux + my * uy
    turn = math.atan2(cross, dot)
    self._deck_to_arm_transform = (
      turn,
      unit_x - (math.cos(turn) * model_x - math.sin(turn) * model_y),
      unit_y - (math.sin(turn) * model_x + math.cos(turn) * model_y),
    )
    return self._deck_to_arm_transform

  def _get_arm_frame_xy(self, *, x: float, y: float) -> Tuple[float, float]:
    """A deck-frame (x, y), mm, in the arm frame: by the stage transform over the plate holder, by
    the fitted transform elsewhere.

    Raises:
      MantisConfigurationError: without the unit's ``stage`` block.
    """
    holder_x = x - PLATE_HOLDER_LOCATION.x
    holder_y = PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - y
    if 0 <= holder_x <= PLATE_HOLDER_SIZE_X and 0 <= holder_y <= PLATE_HOLDER_SIZE_Y:
      arm_x, arm_y, _ = self._get_stage_position(x=holder_x, y=holder_y, z=0.0)
      return arm_x, arm_y
    turn, shift_x, shift_y = self._get_deck_to_arm_transform()
    model_x, model_y = x - ARM_BASE_X, ARM_BASE_Y - y
    return (
      math.cos(turn) * model_x - math.sin(turn) * model_y + shift_x,
      math.sin(turn) * model_x + math.cos(turn) * model_y + shift_y,
    )

  def _get_deck_frame_xy(self, *, x: float, y: float) -> Tuple[float, float]:
    """An arm-frame (x, y), mm, in the deck frame: the stage transform inverted where it lands on
    the plate holder, so a move there reads back where it was sent; the fitted transform elsewhere.

    Raises:
      MantisConfigurationError: without the unit's ``stage`` block.
    """
    self._check_stage()
    assert self.configuration.stage_origin is not None
    assert self.configuration.stage_transformation_matrix is not None
    assert self.configuration.arm_type_rotation_degree is not None
    assert self.configuration.arm_type_translation is not None
    (a, b, c), (d, e, f), (g, h, i) = self.configuration.stage_transformation_matrix
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if determinant != 0:
      # The matrix's inverse, up to scale (its adjugate), maps the arm frame back onto the stage.
      stage_x = (e * i - f * h) * x + (c * h - b * i) * y + (b * f - c * e)
      stage_y = (f * g - d * i) * x + (a * i - c * g) * y + (c * d - a * f)
      scale = (d * h - e * g) * x + (b * g - a * h) * y + (a * e - b * d)
      if scale != 0:
        framed_x = stage_x / scale - self.configuration.stage_origin[0]
        framed_y = stage_y / scale - self.configuration.stage_origin[1]
        framed_x -= self.configuration.arm_type_translation[0]
        framed_y -= self.configuration.arm_type_translation[1]
        angle = math.radians(self.configuration.arm_type_rotation_degree)
        holder_x = math.cos(angle) * framed_x + math.sin(angle) * framed_y
        holder_y = -math.sin(angle) * framed_x + math.cos(angle) * framed_y
        # Within rounding of the holder, as the stage transform was sent there.
        if (
          -HOLDER_EDGE_TOLERANCE <= holder_x <= PLATE_HOLDER_SIZE_X + HOLDER_EDGE_TOLERANCE
          and -HOLDER_EDGE_TOLERANCE <= holder_y <= PLATE_HOLDER_SIZE_Y + HOLDER_EDGE_TOLERANCE
        ):
          return (
            PLATE_HOLDER_LOCATION.x + holder_x,
            PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - holder_y,
          )
    turn, shift_x, shift_y = self._get_deck_to_arm_transform()
    x, y = x - shift_x, y - shift_y
    model_x = math.cos(turn) * x + math.sin(turn) * y
    model_y = -math.sin(turn) * x + math.cos(turn) * y
    return ARM_BASE_X + model_x, ARM_BASE_Y - model_y

  def _get_z_joint_position(self, *, x: float, y: float, z: float) -> float:
    """Z's position, mm down from its flag, for the nozzle at a deck-frame height over (x, y), the
    stage's tilt there taken in.

    Args:
      x: the nozzle's x in the deck frame, mm.
      y: the nozzle's y in the deck frame, mm.
      z: the nozzle's height above the deck, mm.

    Raises:
      MantisConfigurationError: without the unit's ``stage`` block.
    """
    self._check_stage()
    assert self.configuration.stage_reference_height is not None
    _, _, z_joint_position = self._get_stage_position(
      x=x - PLATE_HOLDER_LOCATION.x,
      y=PLATE_HOLDER_LOCATION.y + PLATE_HOLDER_SIZE_Y - y,
      z=self.configuration.stage_reference_height - (z - PLATE_HOLDER_LOCATION.z),
    )
    return z_joint_position

  async def request_pose(self) -> MantisPose:
    """Where every joint of the arm is and where the nozzle is, in the deck frame, worked out from
    the joint state; nothing moves.

    The nozzle is that of a fitted chip, where the stage transform puts it; the links' heights are
    the resource model's, for drawing. The elbows are placed by the fitted transform, approximate.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` or ``stage`` block; nothing is sent.
      MantisKinematicsError: if the links cannot meet at the angles read.
    """
    self._check_calibrated()
    self._check_stage()
    joints = await self.request_joint_state()
    theta_1, theta_2 = self._get_link_angles(joint_state=joints)
    # The links' height for drawing only, as the resource model places them: ARM_Z at Z's flag is
    # assumed, not measured; no clearance is judged from it.
    link_z = ARM_Z - SIZE_Z - joints[Z_MOTOR]
    right_elbow, left_elbow = compute_elbow_positions(theta_1=theta_1, theta_2=theta_2)
    head = compute_head_position(theta_1=theta_1, theta_2=theta_2)
    head_x, head_y = self._get_deck_frame_xy(x=head[0], y=head[1])
    # The nozzle's height: Z's position with the nozzle on the deck, less Z's position read.
    nozzle_z = self._get_z_joint_position(x=head_x, y=head_y, z=0.0) - joints[Z_MOTOR]
    elbows = [
      self._get_deck_frame_xy(x=elbow[0], y=elbow[1]) for elbow in (right_elbow, left_elbow)
    ]
    return MantisPose(
      right_elbow_joint_location=Coordinate(x=elbows[0][0], y=elbows[0][1], z=link_z),
      left_elbow_joint_location=Coordinate(x=elbows[1][0], y=elbows[1][1], z=link_z),
      head_joint_location=Coordinate(x=head_x, y=head_y, z=link_z),
      nozzle_location=Coordinate(x=head_x, y=head_y, z=nozzle_z),
      joints=joints,
    )

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
    absolute = await self.request_absolute_joint_state()
    z = await self._request_measured_position(motor=Z_MOTOR)
    return z <= tolerance and all(
      abs(absolute[motor] - compute_position(motor=motor, device_position=idle_targets[motor]))
      <= tolerance
      for motor in ARM_MOTORS
    )

  # -- moves ------------------------------------------------------------------

  def _notify_pose(self, *, joint_state: JointState) -> None:
    """Pass a measured pose to ``_pose_observer``, if one is set; a failure only warns.

    Args:
      joint_state: each motor's position by its encoder, by motor, as
        ``_request_measured_position`` reads it.
    """
    if self._pose_observer is None:
      return
    try:
      theta_1, theta_2 = self._get_link_angles(joint_state=joint_state)
      self._pose_observer(theta_1=theta_1, theta_2=theta_2, z=0.0 - joint_state[Z_MOTOR])
    except Exception:
      logger.warning("[%s] the arm's pose was not passed on", self._driver.name, exc_info=True)

  async def _report_pose(self) -> None:
    """Read each motor's encoder (command 84) and pass the pose on, only if ``_pose_observer`` is
    set; a failed read only warns."""
    if self._pose_observer is None:
      return
    try:
      measured = {
        motor: await self._request_measured_position(motor=motor)
        for motor in (*ARM_MOTORS, Z_MOTOR)
      }
    except Exception:
      logger.warning("[%s] the arm's pose was not read", self._driver.name, exc_info=True)
      return
    self._notify_pose(joint_state=measured)

  async def _log_where_it_stopped(self) -> None:
    """Log where each motor is by its encoder after a failure, and pass it on; a failed read is
    logged and dropped."""
    try:
      measured = {
        motor: await self._request_measured_position(motor=motor)
        for motor in (*ARM_MOTORS, Z_MOTOR)
      }
      logger.warning("[%s] the motors stopped at %s by their encoders", self._driver.name, measured)
    except Exception:
      logger.warning("[%s] where the motors stopped is not known", self._driver.name)
      return
    self._notify_pose(joint_state=measured)

  async def _stop_motors(
    self, *, motors: Tuple[int, ...], decelerations: Optional[Dict[int, float]] = None
  ) -> bool:
    """Stop each motor (command 24), each in its own try; a failure is logged. Return whether every
    stop was answered.

    Args:
      motors: the motors to stop.
      decelerations: each motor's, in device units, a move stopped at its own
        acceleration; a motor without one takes ``_get_device_acceleration``.
    """
    stopped = True
    for motor in motors:
      deceleration = (decelerations or {}).get(motor)
      try:
        await self._unchecked_fw_stop(
          motor=motor,
          deceleration=(
            self._get_device_acceleration(motor=motor) if deceleration is None else deceleration
          ),
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
      await self._stop_motors(
        motors=tuple(sent),
        decelerations={motor: acceleration for motor, (_, acceleration) in profiles.items()},
      )
      await self._log_where_it_stopped()
      raise

  def _get_route_line_targets(self, *, route_line: RouteLine) -> Dict[int, float]:
    """The motors a route line moves and their targets, in device units, each within its limits.

    Raises:
      MantisKinematicsError: if its (x, y) is out of the arm's reach.
      ValueError: if a target is outside its motor's limits.
    """
    x, y, z = route_line
    targets = self._get_arm_targets(x=x, y=y) if x is not None and y is not None else {}
    if z is not None:
      targets[Z_MOTOR] = compute_device_position(motor=Z_MOTOR, position=z)
    for motor, position in targets.items():
      self._check_reachable(motor=motor, position=position)
    return targets

  def _get_route_targets(self, *, route: List[RouteLine]) -> List[Dict[int, float]]:
    """Each route line's targets, as ``_get_route_line_targets``, all checked before anything moves.

    Raises:
      MantisKinematicsError, ValueError: as ``_get_route_line_targets``.
    """
    return [self._get_route_line_targets(route_line=route_line) for route_line in route]

  async def _move_along_route(self, *, steps: List[Dict[int, float]]) -> None:
    """Move to each step's targets in turn at the route profile, each motor on its own (command
    22), as in the recorded recoveries.

    Args:
      steps: each route line's targets, from ``_get_route_targets``.
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

  async def _declare_absolute_joint_state(self) -> None:
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

  def _check_near_idle(self, *, joint_state: JointState) -> None:
    """Raise unless the head stands within ``FRONT_ZONE_X_RANGE``, where it is moved to idle from.

    From anywhere else, a move to idle could cross a chip rack.

    Args:
      joint_state: each arm motor's angle, in degrees, by motor.

    Raises:
      MantisMotionError: if the head is elsewhere, or its position is not known.
    """
    try:
      x, y = self._get_head_position(joint_state=joint_state)
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
    if self.configuration.zone_rectangles is None:
      raise MantisConfigurationError(f"{self._driver.name}: no zones for this unit")
    for zone in ZONES:
      minimum_x, maximum_x, minimum_y, maximum_y = self.configuration.zone_rectangles[zone]
      if minimum_x <= x <= maximum_x and minimum_y <= y <= maximum_y:
        return zone
    return UNKNOWN_ZONE

  def _get_routes(self, *, joint_state: JointState) -> Tuple[List[RouteLine], List[RouteLine]]:
    """The routes to safe XY and to idle from where the arm motors' angles put the head.

    The zone's own; without the unit's zones, the Front zone's, from near idle only.

    Args:
      joint_state: each arm motor's angle, in degrees, by motor.

    Raises:
      MantisMotionError: if where the head is is not known, or without zones it is not near idle.
    """
    if (
      self.configuration.zone_rectangles is None
      or self.configuration.safe_xy_routes is None
      or self.configuration.idle_routes is None
    ):
      self._check_near_idle(joint_state=joint_state)
      return FRONT_SAFE_XY_ROUTE, FRONT_IDLE_ROUTE
    try:
      x, y = self._get_head_position(joint_state=joint_state)
    except MantisKinematicsError as error:
      raise MantisMotionError(f"{self._driver.name}: where the head is is not known") from error
    zone = self._get_zone(x=x, y=y)
    logger.info(
      "[%s] the head is at (%.3f, %.3f) mm, in the %s zone", self._driver.name, x, y, zone
    )
    return self.configuration.safe_xy_routes[zone], self.configuration.idle_routes[zone]

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
    await self._declare_absolute_joint_state()
    safe_xy_route, _ = self._get_routes(joint_state=await self.request_joint_state())
    await self._move_along_route(steps=self._get_route_targets(route=safe_xy_route))
    await self._home_z()
    for _ in range(2):
      await self._declare_absolute_joint_state()
      _, idle_route = self._get_routes(joint_state=await self.request_joint_state())
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
      await self._declare_absolute_joint_state()
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
    await self._report_pose()

  async def _rise_to_traverse_height(
    self, *, z_joint_position: float, profiles: Dict[int, Tuple[float, float]]
  ) -> None:
    """Rise Z to the traverse height if its encoder reads it below it (command 22), then read Z
    back at or above it by its encoder (command 84), never the demand position.

    Args:
      z_joint_position: the traverse height as Z's position, mm down from its flag, within limits.
      profiles: each motor's (speed, acceleration) in device units, by motor.

    Raises:
      MantisMotionError: if Z answers the move with an error code, faults, or reads more than
        ``Z_TRAVERSE_TOLERANCE`` below the height after it.
    """
    traverse = compute_device_position(motor=Z_MOTOR, position=z_joint_position)
    measured = await self._request_measured_position(motor=Z_MOTOR)
    if compute_device_position(motor=Z_MOTOR, position=measured) <= traverse:
      return
    await self._move_motors(targets={Z_MOTOR: traverse}, profiles=profiles)
    measured = await self._request_measured_position(motor=Z_MOTOR)
    if measured > z_joint_position + Z_TRAVERSE_TOLERANCE:
      raise MantisMotionError(
        f"{self._driver.name}: Z reads {measured:.3f} mm down by its encoder after rising to "
        f"{z_joint_position:.3f} mm down; the arms are not moved",
        motor=Z_MOTOR,
      )

  def _get_traverse_z_joint_position(
    self, *, minimum_traverse_height: Optional[float], points: Sequence[Tuple[float, float]]
  ) -> float:
    """Z's position, mm down from its flag, for the traverse height over every deck-frame point,
    the highest the stage's tilt asks for; checked against Z's limits.

    Args:
      minimum_traverse_height: the nozzle's height above the deck, mm; None takes
        ``default_minimum_traverse_height``, and with that None ``SAFE_HEIGHT``.
      points: the deck-frame (x, y) the head travels between.

    Raises:
      MantisConfigurationError: for a height in the deck frame without the unit's ``stage`` block.
      ValueError: if the height is outside Z's limits.
    """
    if minimum_traverse_height is None:
      minimum_traverse_height = self.default_minimum_traverse_height
    if minimum_traverse_height is None:
      z_joint_position = 0.0 - SAFE_HEIGHT
    else:
      z_joint_position = min(
        self._get_z_joint_position(x=x, y=y, z=minimum_traverse_height) for x, y in points
      )
    self._check_reachable(
      motor=Z_MOTOR, position=compute_device_position(motor=Z_MOTOR, position=z_joint_position)
    )
    return z_joint_position

  async def _get_park_traverse_z_joint_position(
    self, *, minimum_traverse_height: Optional[float] = None
  ) -> float:
    """Z's position, mm down from its flag, for the traverse height where the head stands, as
    ``_get_traverse_z_joint_position``; the head read only for a height in the deck frame."""
    if minimum_traverse_height is None:
      minimum_traverse_height = self.default_minimum_traverse_height
    points: List[Tuple[float, float]] = []
    if minimum_traverse_height is not None:
      head_x, head_y = self._get_head_position(joint_state=await self.request_joint_state())
      points = [self._get_deck_frame_xy(x=head_x, y=head_y)]
    return self._get_traverse_z_joint_position(
      minimum_traverse_height=minimum_traverse_height, points=points
    )

  def _get_approach_path_states(self, *, path: List[RouteLine]) -> List[Tuple[float, float, float]]:
    """Where each line of an approach path leaves the head: arm-frame x and y, mm, and Z, mm down
    from its flag; an axis a line leaves out kept from the line before, from idle with Z at its
    flag."""
    x, y, z = POST_HOMING_POSITION[0], POST_HOMING_POSITION[1], 0.0
    states = []
    for line_x, line_y, line_z in path:
      if line_x is not None and line_y is not None:
        x, y = line_x, line_y
      if line_z is not None:
        z = line_z
      states.append((x, y, z))
    return states

  def _get_place(self, *, joint_state: JointState) -> Optional[Tuple[str, int]]:
    """The approach path, by name, and its line the head stands at, if it stands at a line of one's
    descended part (after the path's last line at the safe height), within
    ``APPROACH_PATH_TOLERANCE``; None in the open. Of lines the head matches on several paths, the
    deepest along its path is taken.

    Args:
      joint_state: where the motors stand, as ``request_joint_state`` reads them.
    """
    x, y = self._get_head_position(joint_state=joint_state)
    z = joint_state[Z_MOTOR]
    place: Optional[Tuple[str, int]] = None
    for name, path in (self.configuration.approach_paths or {}).items():
      states = self._get_approach_path_states(path=path)
      for index in range(self._get_descent_start(path=path), len(states)):
        line_x, line_y, line_z = states[index]
        near = (
          math.hypot(x - line_x, y - line_y) <= APPROACH_PATH_TOLERANCE
          and abs(z - line_z) <= APPROACH_PATH_TOLERANCE
        )
        if near and (place is None or index > place[1]):
          place = (name, index)
    return place

  def _get_descent_start(self, *, path: List[RouteLine]) -> int:
    """The first line of an approach path's descended part: after its last line at the safe
    height; the path's length if it never reaches the safe height."""
    states = self._get_approach_path_states(path=path)
    at_safe_height = [
      index
      for index, (_, _, line_z) in enumerate(states)
      if line_z <= APPROACH_PATH_TOLERANCE - SAFE_HEIGHT
    ]
    return at_safe_height[-1] + 1 if at_safe_height else len(states)

  @staticmethod
  def _get_shared_length(*, first: List[RouteLine], second: List[RouteLine]) -> int:
    """How many lines two approach paths share from their start."""
    count = 0
    for line, other in zip(first, second):
      if line != other:
        break
      count += 1
    return count

  async def _leave_approach_path(self, *, name: str, index: int, until: int) -> None:
    """Move the head back along an approach path from the line it stands at to an earlier line:
    the path's lines played backwards, one queued move each at the route profile; a line naming the arms and Z moves both in one item.

    Args:
      name: the approach path's name.
      index: the line the head stands at.
      until: the line to stop at, 0 the path's start.
    """
    assert self.configuration.approach_paths is not None
    path = self.configuration.approach_paths[name]
    steps = [line for line in reversed(path[until:index]) if line != (None, None, None)]
    profiles = self._get_route_profiles()
    await self._play_queued_moves(
      moves=[
        self._get_queued_move(targets=targets, profiles=profiles)
        for targets in self._get_route_targets(route=steps)
      ]
    )

  async def _leave_approach_path_if_on_one(self, *, joint_state: JointState) -> JointState:
    """Move the head out to the start of the approach path it stands on, if it stands on one, and
    return where the motors stand then; else return ``joint_state`` as it is.

    Args:
      joint_state: where the motors stand, as ``request_joint_state`` reads them.
    """
    place = self._get_place(joint_state=joint_state)
    if place is None:
      return joint_state
    name, index = place
    logger.info(
      "[%s] the head stands on the %s approach path; it leaves along it", self._driver.name, name
    )
    await self._leave_approach_path(name=name, index=index, until=0)
    return await self.request_joint_state()

  def _get_arm_profiles(
    self,
    *,
    start: Optional[JointState],
    target: JointState,
    xy_speed: Optional[float],
    xy_acceleration: Optional[float],
  ) -> Dict[int, Tuple[float, float]]:
    """Each arm motor's (speed, acceleration) in device units for a move of the nozzle.

    Without an xy speed or acceleration, given or default, the route profile. Given, each
    motor's is scaled by its turn over the nozzle's straight-line distance, so the motors start and
    end together and the move takes as long as a straight one would; the nozzle's path is not
    straight.

    Args:
      start: where the arms stand; None only without an xy speed or acceleration.
      target: the arms' targets, degrees.
      xy_speed: mm/s; None takes ``default_xy_speed``.
      xy_acceleration: mm/s²; None takes ``default_xy_acceleration``.
    """
    speed = self.default_xy_speed if xy_speed is None else xy_speed
    acceleration = self.default_xy_acceleration if xy_acceleration is None else xy_acceleration
    rates = {motor: (ROUTE_ARM_SPEED, ROUTE_ARM_ACCELERATION) for motor in ARM_MOTORS}
    if (speed is not None or acceleration is not None) and start is not None:
      start_x, start_y = self._get_head_position(joint_state=start)
      target_x, target_y = self._get_head_position(joint_state={**start, **target})
      distance = math.hypot(target_x - start_x, target_y - start_y)
      for motor in ARM_MOTORS:
        turn = abs(target.get(motor, start[motor]) - start[motor])
        if distance > 0 and turn > 0:
          rates[motor] = (
            ROUTE_ARM_SPEED if speed is None else speed * turn / distance,
            ROUTE_ARM_ACCELERATION if acceleration is None else acceleration * turn / distance,
          )
    return {
      motor: self._get_profile(motor=motor, speed=motor_speed, acceleration=motor_acceleration)
      for motor, (motor_speed, motor_acceleration) in rates.items()
    }

  async def move_to_joint_state(
    self,
    *,
    joint_state: JointState,
    minimum_traverse_height_start: Optional[float] = None,
    xy_speed: Optional[float] = None,
    xy_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Move the motors named to a joint state, queued (command 68); the others hold.

    A move of an arm motor is guarded, as v1 STAR's moves across the deck: Z rises first to the
    traverse height if its encoder reads it below it, and is read back there, then the arms move
    with Z held, then Z moves if named. A move of Z alone is one queued move. The arms move in one
    joint move, not along a route: nothing here avoids the chip racks.

    Args:
      joint_state: each motor's target, by motor, as ``request_joint_state`` reads them.
      minimum_traverse_height_start: the nozzle's height above the deck to rise to before the arms
        move, mm, over where it starts and ends; ``default_minimum_traverse_height`` when None.
      xy_speed: the nozzle's, mm/s; None takes ``default_xy_speed``.
      xy_acceleration: the nozzle's, mm/s²; None takes ``default_xy_acceleration``.
      z_speed: mm/s; None takes ``default_z_speed``.
      z_acceleration: mm/s²; None takes ``default_z_acceleration``.

    Raises:
      MantisConfigurationError: for a traverse height in the deck frame without the unit's
        ``stage`` block; nothing is sent.
      MantisMotionError: if the arm is not brought up, a motor faults, or Z does not read at the traverse height after rising to it.
      MantisKinematicsError: if the links cannot meet at the arm motors' targets.
      ValueError: if a motor is unknown, a target outside its limits, or a speed or acceleration
        out of range.
    """
    self._check_initialized()
    unknown = set(joint_state) - {*ARM_MOTORS, Z_MOTOR}
    if unknown:
      raise ValueError(f"the motors are {(*ARM_MOTORS, Z_MOTOR)}, got {sorted(unknown)}")
    z_profile = self._get_profile(motor=Z_MOTOR, speed=z_speed, acceleration=z_acceleration)
    for name, value in (("xy_speed", xy_speed), ("xy_acceleration", xy_acceleration)):
      if value is not None:
        self._check_positive(name=name, value=value)
    targets = {
      motor: compute_device_position(motor=motor, position=value)
      for motor, value in joint_state.items()
    }
    for motor, target in targets.items():
      self._check_reachable(motor=motor, position=target)
    arm_targets = {motor: target for motor, target in targets.items() if motor in ARM_MOTORS}
    start = await self._leave_approach_path_if_on_one(joint_state=await self.request_joint_state())
    if not arm_targets:
      await self._play_queued_moves(
        moves=[self._get_queued_move(targets=targets, profiles={Z_MOTOR: z_profile})]
      )
      await self._report_pose()
      return
    end = {**start, **joint_state}
    self._get_head_position(joint_state=end)
    if minimum_traverse_height_start is None:
      minimum_traverse_height_start = self.default_minimum_traverse_height
    points: List[Tuple[float, float]] = []
    if minimum_traverse_height_start is not None:
      for pose in (start, end):
        head_x, head_y = self._get_head_position(joint_state=pose)
        points.append(self._get_deck_frame_xy(x=head_x, y=head_y))
    traverse = self._get_traverse_z_joint_position(
      minimum_traverse_height=minimum_traverse_height_start, points=points
    )
    arm_profiles = self._get_arm_profiles(
      start=start, target=joint_state, xy_speed=xy_speed, xy_acceleration=xy_acceleration
    )
    await self._rise_to_traverse_height(z_joint_position=traverse, profiles={Z_MOTOR: z_profile})
    moves = [self._get_queued_move(targets=arm_targets, profiles=arm_profiles)]
    if Z_MOTOR in targets:
      moves.append(
        self._get_queued_move(targets={Z_MOTOR: targets[Z_MOTOR]}, profiles={Z_MOTOR: z_profile})
      )
    for move in moves:
      await self._play_queued_moves(moves=[move])
    await self._report_pose()

  async def move_to_xy_position(
    self,
    *,
    x: float,
    y: float,
    minimum_traverse_height_start: Optional[float] = None,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
  ) -> None:
    """Move the nozzle across to (x, y) in the deck frame, guarded: Z rises first to the traverse
    height if it is below it, then the arms move with Z held. Nothing avoids the chip racks.

    Over the plate holder the unit's stage transform places it; elsewhere the arm frame fitted to it.

    Args:
      x: right of the deck's front-left corner, mm.
      y: behind the deck's front-left corner, mm.
      minimum_traverse_height_start: the nozzle's height above the deck to rise to first, mm;
        ``default_minimum_traverse_height`` when None.
      speed: the nozzle's, mm/s; None takes ``default_xy_speed``.
      acceleration: the nozzle's, mm/s²; None takes ``default_xy_acceleration``.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` or ``stage`` block; nothing is sent.
      MantisMotionError: if the arm is not brought up, a motor faults, or Z does not read at the traverse height after rising to it.
      MantisKinematicsError: if (x, y) is out of the arm's reach.
      ValueError: if a target is outside its motor's limits, or a speed or acceleration out of
        range.
    """
    self._check_initialized()
    arm_x, arm_y = self._get_arm_frame_xy(x=x, y=y)
    targets = self._get_arm_targets(x=arm_x, y=arm_y)
    await self.move_to_joint_state(
      joint_state={
        motor: compute_position(motor=motor, device_position=target)
        for motor, target in targets.items()
      },
      minimum_traverse_height_start=minimum_traverse_height_start,
      xy_speed=speed,
      xy_acceleration=acceleration,
    )

  async def move_to_z_position(
    self, *, z: float, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> None:
    """Move the nozzle to a height in the deck frame where it stands; the arms hold. The stage's
    tilt under the nozzle is taken in.

    Args:
      z: above the deck, mm.
      speed: in mm/s; None takes ``default_z_speed``.
      acceleration: in mm/s²; None takes ``default_z_acceleration``.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` or ``stage`` block; nothing is sent.
      MantisMotionError: if the arm is not brought up, or Z faults.
      MantisKinematicsError: if the links cannot meet at the angles read.
      ValueError: if the target is outside Z's limits, or a speed or acceleration out of range.
    """
    self._check_initialized()
    self._check_stage()
    z_profile = self._get_profile(motor=Z_MOTOR, speed=speed, acceleration=acceleration)
    joint_state = await self._leave_approach_path_if_on_one(
      joint_state=await self.request_joint_state()
    )
    head_x, head_y = self._get_head_position(joint_state=joint_state)
    x, y = self._get_deck_frame_xy(x=head_x, y=head_y)
    target = compute_device_position(
      motor=Z_MOTOR, position=self._get_z_joint_position(x=x, y=y, z=z)
    )
    self._check_reachable(motor=Z_MOTOR, position=target)
    await self._play_queued_moves(
      moves=[self._get_queued_move(targets={Z_MOTOR: target}, profiles={Z_MOTOR: z_profile})]
    )
    await self._report_pose()

  async def move_to_location(
    self,
    *,
    location: Coordinate,
    minimum_traverse_height_start: Optional[float] = None,
    xy_speed: Optional[float] = None,
    xy_acceleration: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Move the nozzle to a deck-frame location: rise to the traverse height if below it, move
    across with Z held, then move Z to the location's height.

    Every target is checked before anything moves, and Z is read back at the traverse height by
    its encoder before the arms move. The arms move in one joint move: nothing here avoids the
    chip racks.

    Args:
      location: the nozzle's x, y and height above the deck, mm.
      minimum_traverse_height_start: the nozzle's height above the deck to rise to first, mm, over
        both ends; ``default_minimum_traverse_height`` when None.
      xy_speed: the nozzle's, mm/s; None takes ``default_xy_speed``.
      xy_acceleration: the nozzle's, mm/s²; None takes ``default_xy_acceleration``.
      z_speed: mm/s; None takes ``default_z_speed``.
      z_acceleration: mm/s²; None takes ``default_z_acceleration``.

    Raises:
      MantisConfigurationError: without the unit's ``arm`` or ``stage`` block; nothing is sent.
      MantisMotionError: if the arm is not brought up, a motor faults, or Z does not read at the traverse height after rising to it.
      MantisKinematicsError: if the location is out of the arm's reach.
      ValueError: if a target is outside its motor's limits, or a speed or acceleration out of
        range.
    """
    self._check_initialized()
    self._check_stage()
    z_profile = self._get_profile(motor=Z_MOTOR, speed=z_speed, acceleration=z_acceleration)
    for name, value in (("xy_speed", xy_speed), ("xy_acceleration", xy_acceleration)):
      if value is not None:
        self._check_positive(name=name, value=value)
    arm_x, arm_y = self._get_arm_frame_xy(x=location.x, y=location.y)
    arm_targets = self._get_arm_targets(x=arm_x, y=arm_y)
    z_target = self._get_z_joint_position(x=location.x, y=location.y, z=location.z)
    for motor, target in (
      *arm_targets.items(),
      (Z_MOTOR, compute_device_position(motor=Z_MOTOR, position=z_target)),
    ):
      self._check_reachable(motor=motor, position=target)
    start = await self._leave_approach_path_if_on_one(joint_state=await self.request_joint_state())
    head_x, head_y = self._get_head_position(joint_state=start)
    traverse = self._get_traverse_z_joint_position(
      minimum_traverse_height=minimum_traverse_height_start,
      points=[self._get_deck_frame_xy(x=head_x, y=head_y), (location.x, location.y)],
    )
    await self._rise_to_traverse_height(z_joint_position=traverse, profiles={Z_MOTOR: z_profile})
    arm_joint_state = {
      motor: compute_position(motor=motor, device_position=target)
      for motor, target in arm_targets.items()
    }
    arm_profiles = self._get_arm_profiles(
      start=start, target=arm_joint_state, xy_speed=xy_speed, xy_acceleration=xy_acceleration
    )
    z_device_target = compute_device_position(motor=Z_MOTOR, position=z_target)
    await self._play_queued_moves(
      moves=[self._get_queued_move(targets=arm_targets, profiles=arm_profiles)]
    )
    await self._play_queued_moves(
      moves=[
        self._get_queued_move(targets={Z_MOTOR: z_device_target}, profiles={Z_MOTOR: z_profile})
      ]
    )
    await self._report_pose()

  async def _route_to_idle(self, *, z_joint_position: float) -> None:
    """Rise to the traverse height if below it, then move along the route to idle from where the
    absolute encoders put the head, at the route profile.

    Args:
      z_joint_position: the traverse height as Z's position, mm down from its flag.

    Raises:
      MantisMotionError: if a motor faults, Z does not read at the traverse height after rising,
        where the head is is not known, or without the unit's zones it is not near idle.
    """
    await self._wait_for_motors_idle(timeout=MOVE_TIMEOUT)
    _, idle_route = self._get_routes(joint_state=await self.request_absolute_joint_state())
    steps = self._get_route_targets(route=idle_route)
    await self._rise_to_traverse_height(
      z_joint_position=z_joint_position, profiles=self._get_route_profiles()
    )
    await self._move_along_route(steps=steps)

  async def park(self, *, minimum_traverse_height_start: Optional[float] = None) -> None:
    """Move the head to idle along its zone's route, from any zone; from an approach path, first out
    along it.

    The head first rises to the traverse height if it is below it. Without the unit's zones it is
    parked from near idle only.

    Args:
      minimum_traverse_height_start: the nozzle's height above the deck to rise to first, mm;
        ``default_minimum_traverse_height`` when None.

    Raises:
      MantisConfigurationError: for a height in the deck frame without the unit's ``stage`` block.
      MantisMotionError: if the arm is not brought up, a motor faults, Z does not read at the
        traverse height after rising, or without the unit's zones the head is not near idle.
      ValueError: if the traverse height is outside Z's limits.
    """
    self._check_initialized()
    await self._leave_approach_path_if_on_one(joint_state=await self.request_joint_state())
    traverse = await self._get_park_traverse_z_joint_position(
      minimum_traverse_height=minimum_traverse_height_start
    )
    await self._route_to_idle(z_joint_position=traverse)
    await self._report_pose()

  async def _move_along_approach_path(self, *, name: str) -> None:
    """Move the head to the end of an approach path, from wherever it stands.

    On that path, onward from where it stands. On a neighbouring path, one that shares this one's
    descent (the two washes), back to its safe-Z line (its last line but one), then across to this
    one's and down. On another path, back along it to the
    last line the two share, then along this one from there. In the open, to idle first as ``park``
    takes it, then along the path from its start. Where the head stands is read from the motors,
    never remembered.

    Raises:
      MantisConfigurationError: without the path; nothing moves.
      MantisMotionError: if the arm is not brought up, a motor faults, or Z does not read at the
        traverse height after rising.
    """
    self._check_initialized()
    paths = self.configuration.approach_paths or {}
    path = paths.get(name)
    if path is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no approach path to the {name} for this unit; give its "
        "configuration folder (oem_config_directory) or a configuration saved with it"
      )
    profiles = self._get_route_profiles()
    self._get_route_targets(route=path)  # every line checked before anything moves
    place = self._get_place(joint_state=await self.request_joint_state())
    if place is not None and place[0] == name:
      start = place[1] + 1
    elif place is not None:
      other_name, index = place
      other = paths[other_name]
      shared = self._get_shared_length(first=other, second=path)
      safe_z_line = len(other) - 2
      if shared >= self._get_descent_start(path=other) and index >= safe_z_line >= 0:
        await self._leave_approach_path(name=other_name, index=index, until=safe_z_line)
        start = max(len(path) - 2, 0)
      else:
        await self._leave_approach_path(name=other_name, index=index, until=max(shared - 1, 0))
        start = shared
    else:
      if not await self.request_is_parked():
        await self.park()
      start = 0
    moves = [
      self._get_queued_move(targets=targets, profiles=profiles)
      for targets in self._get_route_targets(route=path[start:])
    ]
    await self._play_queued_moves(moves=moves)
    await self._report_pose()

  async def move_to_waste(self) -> None:
    """Move the head into the waste along the unit's approach path, from wherever it stands.

    Raises:
      MantisConfigurationError: without the unit's approach path; nothing moves.
      MantisMotionError: if the arm is not brought up, or a motor faults.
    """
    await self._move_along_approach_path(name=WASTE_APPROACH_PATH)

  async def move_to_wash(self, *, position: int) -> None:
    """Move the head into a wash position along the unit's approach path, from wherever it stands;
    from the other wash position without leaving the wash.

    Args:
      position: 1 or 2.

    Raises:
      MantisConfigurationError: without the unit's approach path; nothing moves.
      MantisMotionError: if the arm is not brought up, or a motor faults.
      ValueError: if ``position`` is not 1 or 2.
    """
    if position not in WASH_POSITIONS:
      raise ValueError(f"position must be one of {WASH_POSITIONS}, got {position}")
    await self._move_along_approach_path(name=WASH_APPROACH_PATH.format(position=position))

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
    idle_targets = self._get_route_line_targets(route_line=(*POST_HOMING_POSITION, None))
    z_up = self._get_route_line_targets(route_line=(None, None, 0.0))
    z_down = self._get_route_line_targets(route_line=(None, None, SHUTDOWN_Z))
    await self._wait_for_motors_idle(timeout=MOVE_TIMEOUT)
    await self._leave_approach_path_if_on_one(joint_state=await self.request_joint_state())
    try:
      self._check_near_idle(joint_state=await self.request_absolute_joint_state())
    except MantisMotionError:
      if self.configuration.zone_rectangles is None:
        raise
      await self._route_to_idle(z_joint_position=await self._get_park_traverse_z_joint_position())
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
    await self._report_pose()

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

"""The iSWAP: a P(x)+P(y)+P(z)+R(elbow)+R(wrist)+EE(gripper) SCARA
with a mechanical gripper as end-effector that moves resources.
"""

import dataclasses
import datetime
import enum
import logging
import math
from dataclasses import dataclass
from typing import (
  TYPE_CHECKING,
  Callable,
  ClassVar,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Tuple,
  Union,
  cast,
)

from pylabrobot.hamilton.protocol.text.framing import parse_firmware_version_date
from pylabrobot.hamilton.star.driver.errors import NoElementError, STARFirmwareError
from pylabrobot.hamilton.star.resource_model import iSWAPHead
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.rotation import Rotation

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.features.x_arm import XArm
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)


# A gripper direction: where the gripper looks - the way its fingers point - in the driver's own
# angles, 0 along +x, deck-right, turning counter-clockwise seen from above. Hamilton's `pd` codes
# name a position rather than a direction, the side of the resource the gripper stands on, so the
# two differ by half a turn.
GRIPPER_DECK_DIRECTIONS: Dict[str, float] = {
  "right": 0.0,
  "back": 90.0,
  "left": 180.0,
  "front": -90.0,
}

RECORDED_FIRMWARE_PREFIX = "4."

# The rotation drive's own dimensions, in mm. Module constants rather than field defaults alone,
# because the front limit below is worked out from them before any configuration exists.
ROTATION_DRIVE_DIAMETER = 30.5
ROTATION_DRIVE_SAFETY_RADIUS = 90.0

# The arm this driver assumes it is on until it has read the one it is actually on. Twelve channels
# is more than most arms carry, and every extra channel in front of the drive holds the drive
# further back, so assuming twelve errs the safe way: it refuses poses a narrower arm could reach,
# rather than allowing ones a wider arm could not. `iSWAP.declare_front_limit` replaces all three
# with what the arm reports, at discovery.
ASSUMED_CHANNELS = 12
ASSUMED_CHANNEL_WIDTH = 9.0
ASSUMED_ARM_FRONT_LIMIT = 6.0


def rotation_drive_front_limit(
  arm_front_limit: float, channel_widths: Sequence[float], swept_radius: float
) -> float:
  """How far forward the rotation drive can be brought, in mm.

  Not the arm's own front limit: the channels ride in front of the drive on the same rail, and the
  drive stops behind the backmost of them however far forward they are packed. Packed is what this
  measures - every channel against the front of the arm's travel, each taking its own width - so
  it is the limit with the deck cleared, not wherever the channels happen to be standing.

  Args:
    arm_front_limit: the front of the arm's own Y travel, in mm.
    channel_widths: how wide each channel is, in mm, backmost first.
    swept_radius: how far from the drive's centre anything it carries reaches, in mm.

  Returns:
    The furthest forward the drive's own reference point can be sent, in mm.
  """
  if not channel_widths:
    return arm_front_limit
  return arm_front_limit + sum(channel_widths[1:]) + channel_widths[0] / 2 + swept_radius


@dataclass
class CartesianPose:
  """Location and rotation of the gripper.

  In the STAR's deck frame: mm from the deck's origin, and degrees counter-clockwise seen from
  above with 0 along +x. Named as the other arms name theirs, since it is the same thing.
  """

  location: Coordinate
  rotation: Rotation


class iSWAPAxis(enum.IntEnum):
  """The iSWAP's addressable axes, as `request_joint_state` keys.

  Units are the axis's own: the prismatic axes and the gripper in mm, the two revolute drives in
  degrees. `Z` is the rotation drive's bottom, which sits above the gripper finger plane by
  `iSWAPConfiguration.rotation_drive_z_offset_above_finger`, so it is not the grip centre's Z.
  """

  X = 1
  Y = 2
  Z = 3
  ROTATION = 4
  WRIST = 5
  GRIPPER = 6

  @property
  def is_in_kinematic_chain(self) -> bool:
    """Whether the axis moves the gripper frame.

    The gripper is driven, but opening it changes what is held rather than where the gripper is.
    """
    return self is not iSWAPAxis.GRIPPER


JointState = Dict[iSWAPAxis, float]
"""Where every axis is, in its own units: the prismatic axes and the gripper in mm, the two
revolute drives in degrees. What the arm is, rather than anything worked out from it."""


@dataclasses.dataclass(frozen=True)
class iSWAPPose:
  """Where every joint of the arm is, and which way the gripper faces.

  One answer for the whole arm rather than for its end. The two links are what put the gripper
  where it is, so a caller asking whether the arm clears something needs the joint between them as
  much as the point at the end of it: a wrist folded back swings link 2 the opposite way to the
  turn, and only the middle joint shows that.

  Every coordinate and rotation here is in the STAR's deck frame, as `CartesianPose` states it.
  """

  rotation_joint_location: Coordinate
  """Where the rotation drive is: the joint link 1 turns about."""
  wrist_joint_location: Coordinate
  """Link 1's far end, which is the joint link 2 turns about. Which way link 1 lies is not stated:
  it is the direction from `rotation_joint_location` to here, and nothing has needed it."""
  gripper_center_location: Coordinate
  """Link 2's far end, between the fingers: the point a grip is programmed against."""
  gripper_deck_orientation: Rotation
  """Which way the gripper faces, in the deck frame - the yaw link 2 lies along, since the gripper
  is bolted to it. Degrees counter-clockwise seen from above, 0 along +x."""
  joints: JointState
  """What each drive reported, in its own units, as `request_joint_state` returns it."""


@dataclasses.dataclass(frozen=True)
class iSWAPYPositions:
  SLOTS: ClassVar[Tuple[str, ...]] = (
    "home",
    "lower_limit",
    "upper_limit",
    "parking",
    "pre_parking",
    "extra_1",
    "extra_2",
    "extra_3",
    "extra_4",
    "extra_5",
  )
  home: int
  lower_limit: int
  upper_limit: int
  parking: int
  pre_parking: int
  extra_1: int
  extra_2: int
  extra_3: int
  extra_4: int
  extra_5: int


@dataclasses.dataclass(frozen=True)
class iSWAPZPositions:
  SLOTS: ClassVar[Tuple[str, ...]] = (
    "home",
    "parking",
    "extra_1",
    "extra_2",
    "extra_3",
    "extra_4",
    "extra_5",
    "extra_6",
    "extra_7",
    "extra_8",
  )
  home: int
  parking: int
  extra_1: int
  extra_2: int
  extra_3: int
  extra_4: int
  extra_5: int
  extra_6: int
  extra_7: int
  extra_8: int


@dataclasses.dataclass(frozen=True)
class iSWAPRotationPositions:
  SLOTS: ClassVar[Tuple[str, ...]] = (
    "home",
    "left",
    "front",
    "right",
    "parking",
    "extra_1",
    "extra_2",
    "extra_3",
    "extra_4",
  )
  home: int
  left: int
  front: int
  right: int
  parking: int
  extra_1: int
  extra_2: int
  extra_3: int
  extra_4: int


@dataclasses.dataclass(frozen=True)
class iSWAPWristPositions:
  SLOTS: ClassVar[Tuple[str, ...]] = (
    "home",
    "right",
    "straight",
    "left",
    "reverse",
    "parking",
    "extra_1",
    "extra_2",
    "extra_3",
  )
  home: int
  right: int
  straight: int
  left: int
  reverse: int
  parking: int
  extra_1: int
  extra_2: int
  extra_3: int


@dataclasses.dataclass(frozen=True)
class iSWAPGripperPositions:
  SLOTS: ClassVar[Tuple[str, ...]] = (
    "home",
    "extra_1",
    "closed",
    "plate_type_1",
    "plate_type_2",
    "plate_type_3",
    "plate_type_4",
    "plate_type_5",
    "plate_type_6",
    "plate_type_7",
  )
  home: int
  extra_1: int
  closed: int
  plate_type_1: int
  plate_type_2: int
  plate_type_3: int
  plate_type_4: int
  plate_type_5: int
  plate_type_6: int
  plate_type_7: int


@dataclass
class iSWAPConfiguration:
  """Device parameters for the installed iSWAP.

  Ported from the legacy `iSWAPInformation`. Two kinds of value: per-device calibration read from
  the device at setup - link lengths, calibrated stops, offsets - which is None until read; and
  device facts of the 4th-generation iSWAP, the only generation supported, which are defaulted.
  Neither changes at runtime.
  """

  module: str = "R0"
  """What the iSWAP is on the bus."""

  firmware_version: Optional[str] = None
  firmware_date: Optional[datetime.date] = None

  link_1_length: Optional[float] = None
  """rotation joint (joint 1) to the wrist joint (joint 2); default: 138.0 mm."""
  tool_length: Optional[float] = None
  """wrist joint (joint 2) to the gripper finger centre, in mm. default: 138.0 mm."""

  # -- X --
  rotation_drive_x_offset: Optional[float] = None
  """Deck X distance from the X-arm carriage reference point to the rotation drive (mm). Stored in
  master EEPROM. The Hamilton factory default is 34.0 mm."""

  # -- Y --
  rotation_drive_predefined_y_positions_increments: Optional[iSWAPYPositions] = None
  """Each Y stop the carriage is calibrated against, in increments."""

  # -- Z --
  rotation_drive_predefined_z_positions_increments: Optional[iSWAPZPositions] = None
  """Each Z stop the rotation drive is calibrated against, in increments of the finger plane.
  Read by discovery; the defaults are factory values, not one unit's calibration."""

  # -- rotation drive --
  rotation_drive_predefined_increments: Optional[iSWAPRotationPositions] = None

  # -- wrist drive --
  wrist_drive_predefined_increments: Optional[iSWAPWristPositions] = None
  # -- gripper drive --
  gripper_drive_predefined_increments: Optional[iSWAPGripperPositions] = None
  """Each jaw width the gripper is calibrated against, in increments. Read by discovery; the
  defaults are factory values, not one unit's calibration."""

  # === Device facts of the 4th-generation iSWAP: per-drive area-of-operation ranges and encoder
  # resolutions. The same across units of a generation, so they are defaulted - but only that
  # generation's are held. On an arm of another generation every conversion below would be wrong,
  # so discovery says so when the arm reports a firmware version these were not taken from. ===

  # -- Y --
  y_range_increments: Tuple[int, int] = (0, 14_000)
  y_mm_per_increment: float = 0.046302083
  y_speed_range_increments: Tuple[int, int] = (50, 8_000)  # increments/sec
  # Speeds run under the documented defaults - Y 68%, rotation 44%, wrist 41% - these swing a plate.
  y_speed_default_increments: int = 4_751
  y_current_limit_default: int = 7
  y_acceleration_level_default: int = 2
  rotation_drive_diameter: float = ROTATION_DRIVE_DIAMETER
  """How wide the rotation drive is, in mm."""

  rotation_drive_safety_radius: float = ROTATION_DRIVE_SAFETY_RADIUS
  """How far past the drive's own edge anything it carries reaches, in mm. A clearance that holds
  at every rotation angle is the drive's radius plus this."""

  rotation_drive_y_min: float = rotation_drive_front_limit(
    ASSUMED_ARM_FRONT_LIMIT,
    [ASSUMED_CHANNEL_WIDTH] * ASSUMED_CHANNELS,
    ROTATION_DRIVE_DIAMETER / 2 + ROTATION_DRIVE_SAFETY_RADIUS,
  )
  """How far forward the rotation drive can be brought, in mm: the front stop the channels leave it.

  The one bound here that is not the drive's own. Defaulted for the assumed twelve-channel arm, so
  a driver that has not read a device still has a limit rather than none, and overwritten with the
  arm's own channels by `iSWAP.declare_front_limit` at discovery."""

  rotation_drive_size_z: float = 120.0
  """How tall to model the rotation drive, in mm. Not read from anywhere: how far the drive extends
  is not something the device reports."""

  # -- Z --
  z_range_increments: Tuple[int, int] = (-187, 26_661)
  z_mm_per_increment: float = 0.01072765
  z_speed_range_increments: Tuple[int, int] = (50, 15_000)  # increments/sec
  z_acceleration_range_increments: Tuple[int, int] = (5, 999)  # 1000 increments/sec^2
  z_speed_default_increments: int = 11_000
  z_acceleration_default_increments: int = 60
  z_current_limit_default: int = 6
  rotation_drive_z_offset_above_finger: float = 13.0
  """How far the rotation drive's lowest point sits above the finger plane, in mm. Z is calibrated
  to the finger plane, so a position read or commanded is that plane's plus this."""

  # -- rotation drive (joint 1) --
  rotation_range_increments: Tuple[int, int] = (-30_032, 30_032)
  rotation_deg_per_increment: float = 0.00309619077
  rotation_speed_range_increments: Tuple[int, int] = (20, 75_000)  # increments/sec
  rotation_acceleration_range_increments: Tuple[int, int] = (5, 200)  # 1000 increments/sec^2
  rotation_speed_default_increments: int = 24_223
  rotation_acceleration_default_increments: int = 161
  rotation_current_limit_default: int = 5

  # -- wrist drive (joint 2) --
  wrist_range_increments: Tuple[int, int] = (-30_000, 30_000)
  wrist_deg_per_increment: float = 0.00507968798
  wrist_speed_range_increments: Tuple[int, int] = (20, 65_000)  # increments/sec
  wrist_acceleration_range_increments: Tuple[int, int] = (5, 200)  # 1000 increments/sec^2
  wrist_speed_default_increments: int = 19_686
  wrist_acceleration_default_increments: int = 143
  wrist_current_limit_default: int = 5

  # -- gripper --
  gripper_range_increments: Tuple[int, int] = (12_780, 24_120)  # jaw width
  gripper_mm_per_increment: float = 0.00554337
  gripper_speed_range_increments: Tuple[int, int] = (20, 9_999)  # increments/sec
  gripper_acceleration_range_increments: Tuple[int, int] = (5, 150)  # 1000 increments/sec^2
  gripper_current_limit_range: Tuple[int, int] = (0, 15)
  gripper_speed_default_increments: int = 8_659
  gripper_acceleration_default_increments: int = 75
  gripper_current_limit_default: int = 15
  gripper_close_speed_default_increments: int = 5_000
  gripper_nudge_speed_default_increments: int = 2_000  # slow: used against a stuck drive
  gripper_stop_trigger_default: int = 200
  gripper_low_pass_filter_default: bool = True
  gripper_stop_band_range_increments: Tuple[int, int] = (80, 1_800)
  """How wide a window the drive accepts around the width a close is aimed at, in its own steps."""
  gripper_counter_drift_increments: int = 50
  """How far the drive's two counters may sit apart before the gap means lost steps rather than
  the ordinary lag between commanding a move and finishing it."""

  # -- conversions: the wire counts in increments, the driver speaks mm and degrees ----------

  @property
  def rotation_drive_y_max(self) -> Optional[float]:
    """How far back the carriage may be sent, in mm: the parking stop it is calibrated against.

    Returns:
      The parking stop in mm, or None until the stored Y table has been read.
    """
    predefined_y_positions = self.rotation_drive_predefined_y_positions_increments
    if predefined_y_positions is None:
      return None
    return self.y_increments_to_mm(predefined_y_positions.parking)

  def y_increments_to_mm(self, increments: int) -> float:
    """A Y-carriage position in mm, from the increments the drive counts in."""
    return round(increments * self.y_mm_per_increment, 2)

  def y_mm_to_increments(self, mm: float) -> int:
    """A Y-carriage position in increments, from mm."""
    return round(mm / self.y_mm_per_increment)

  def z_increments_to_mm(self, increments: int) -> float:
    """A Z position in mm, from increments."""
    return round(increments * self.z_mm_per_increment, 3)

  def z_mm_to_increments(self, mm: float) -> int:
    """A Z position in increments, from mm."""
    return round(mm / self.z_mm_per_increment)

  @property
  def rotation_drive_z_range(self) -> Tuple[float, float]:
    """How far the rotation drive's bottom travels along Z, in mm, lowest first.

    Derived from the drive's documented area of operation, not probed: unlike a head, the iSWAP
    has no command that finds its own limit.
    """
    return (
      round(
        self.z_increments_to_mm(self.z_range_increments[0])
        + self.rotation_drive_z_offset_above_finger,
        1,
      ),
      round(
        self.z_increments_to_mm(self.z_range_increments[1])
        + self.rotation_drive_z_offset_above_finger,
        1,
      ),
    )

  @property
  def rotation_drive_swept_radius(self) -> float:
    """How far from the rotation drive's centre anything it carries can reach, in mm.

    The drive and its arm are treated as one circle, so a clearance measured against it holds
    whichever way the arm happens to be turned.
    """
    return self.rotation_drive_diameter / 2 + self.rotation_drive_safety_radius

  def rotation_drive_increments_to_angle(self, increments: int) -> float:
    """A rotation-drive angle in degrees, from increments, against the calibrated stops.

    Piecewise linear rather than one slope: `left` to `front` spans -90 to 0 degrees and `front`
    to `right` spans 0 to +90, each against the stops this device reports, so they read back as
    exactly -90, 0 and +90 however far calibration has drifted.

    Args:
      increments: what the drive reports.

    Returns:
      The angle in degrees, signed from the calibrated front stop.

    Raises:
      RuntimeError: If the stored stops were not read.
    """
    predefined_positions = self.rotation_drive_predefined_increments
    if predefined_positions is None:
      raise RuntimeError(
        "the rotation drive's stops were not read; have you called `star.setup()`?"
      )
    front = predefined_positions.front
    if increments < front:
      return -90.0 * (front - increments) / (front - predefined_positions.left)
    return 90.0 * (increments - front) / (predefined_positions.right - front)

  def rotation_drive_angle_to_increments(self, angle: float) -> int:
    """A rotation-drive angle in increments, from degrees, against the calibrated stops.

    The inverse of `rotation_drive_increments_to_angle`, piecewise on the same two segments, so
    -90, 0 and +90 land exactly on the stops this device reports.

    Args:
      angle: degrees, signed from the calibrated front stop.

    Returns:
      What to send the drive.

    Raises:
      RuntimeError: If the stored stops were not read.
    """
    predefined_positions = self.rotation_drive_predefined_increments
    if predefined_positions is None:
      raise RuntimeError(
        "the rotation drive's stops were not read; have you called `star.setup()`?"
      )
    front = predefined_positions.front
    if angle < 0:
      return round(front - (angle / -90.0) * (front - predefined_positions.left))
    return round(front + (angle / 90.0) * (predefined_positions.right - front))

  # The wrist's four stops, in the drive's own degrees. The drive is zeroed between `straight` and
  # `left`, which is what puts these at a quarter turn either side of +/-45 rather than at 0 and 90.
  WRIST_STOP_ANGLES = (("right", -135.0), ("straight", -45.0), ("left", 45.0), ("reverse", 135.0))

  def _wrist_calibrated_stops(self) -> List[Tuple[int, float]]:
    """The wrist's stops as increment/degree pairs, in increasing order.

    Returns:
      What this device reports for each stop, against the angle that stop stands at.

    Raises:
      RuntimeError: If the stored stops were not read.
    """
    predefined_positions = self.wrist_drive_predefined_increments
    if predefined_positions is None:
      raise RuntimeError("the wrist drive's stops were not read; have you called `star.setup()`?")
    return [
      (predefined_positions.right, -135.0),
      (predefined_positions.straight, -45.0),
      (predefined_positions.left, 45.0),
      (predefined_positions.reverse, 135.0),
    ]

  def wrist_increments_to_deg(self, increments: int) -> float:
    """A wrist-drive angle in degrees, from increments, against the calibrated stops.

    Piecewise linear across the three segments the four stops divide the travel into, so the stops
    read back exactly however far calibration has drifted, and an angle between two of them is
    measured against the span this device actually reports rather than against a nominal
    resolution. Past the outer stops the end segment's slope carries on.

    Args:
      increments: what the drive reports.

    Returns:
      The angle in degrees, signed from the drive's own zero.

    Raises:
      RuntimeError: If the stored stops were not read.
    """
    stops = self._wrist_calibrated_stops()
    segment = next(
      (i for i in range(len(stops) - 1) if increments < stops[i + 1][0]), len(stops) - 2
    )
    (low_increments, low_angle), (high_increments, high_angle) = stops[segment], stops[segment + 1]
    span = (increments - low_increments) / (high_increments - low_increments)
    return low_angle + span * (high_angle - low_angle)

  def wrist_deg_to_increments(self, deg: float) -> int:
    """A wrist-drive angle in increments, from degrees, against the calibrated stops.

    The inverse of `wrist_increments_to_deg`, piecewise on the same three segments, so a stop's
    angle lands exactly on the increments this device reports for it.

    Args:
      deg: degrees, signed from the drive's own zero.

    Returns:
      What to send the drive.

    Raises:
      RuntimeError: If the stored stops were not read.
    """
    stops = self._wrist_calibrated_stops()
    segment = next((i for i in range(len(stops) - 1) if deg < stops[i + 1][1]), len(stops) - 2)
    (low_increments, low_angle), (high_increments, high_angle) = stops[segment], stops[segment + 1]
    span = (deg - low_angle) / (high_angle - low_angle)
    return round(low_increments + span * (high_increments - low_increments))

  # A rate is a plain division by the drive's resolution, unlike a position, which is piecewise
  # against the stops. Acceleration is counted in thousands of increments.

  def rotation_deg_per_sec_to_increments(self, deg_per_sec: float) -> int:
    """A rotation-drive speed in increments/s, from degrees/s."""
    return round(deg_per_sec / self.rotation_deg_per_increment)

  def rotation_increments_to_deg_per_sec(self, increments: int) -> float:
    """A rotation-drive speed in degrees/s, from increments/s."""
    return round(increments * self.rotation_deg_per_increment, 2)

  def rotation_deg_per_sec2_to_increments(self, deg_per_sec2: float) -> int:
    """A rotation-drive acceleration in thousands of increments/s2, from degrees/s2."""
    return round(deg_per_sec2 / self.rotation_deg_per_increment / 1000)

  def rotation_increments_to_deg_per_sec2(self, increments: int) -> float:
    """A rotation-drive acceleration in degrees/s2, from thousands of increments/s2."""
    return round(increments * 1000 * self.rotation_deg_per_increment, 2)

  def wrist_deg_per_sec_to_increments(self, deg_per_sec: float) -> int:
    """A wrist-drive speed in increments/s, from degrees/s."""
    return round(deg_per_sec / self.wrist_deg_per_increment)

  def wrist_increments_to_deg_per_sec(self, increments: int) -> float:
    """A wrist-drive speed in degrees/s, from increments/s."""
    return round(increments * self.wrist_deg_per_increment, 2)

  def wrist_deg_per_sec2_to_increments(self, deg_per_sec2: float) -> int:
    """A wrist-drive acceleration in thousands of increments/s2, from degrees/s2."""
    return round(deg_per_sec2 / self.wrist_deg_per_increment / 1000)

  def wrist_increments_to_deg_per_sec2(self, increments: int) -> float:
    """A wrist-drive acceleration in degrees/s2, from thousands of increments/s2."""
    return round(increments * 1000 * self.wrist_deg_per_increment, 2)

  @property
  def y_speed_default(self) -> float:
    """Y speed a move uses when the caller names none (mm/s)."""
    return self.y_increments_to_mm(self.y_speed_default_increments)

  @property
  def z_speed_default(self) -> float:
    """Z speed a move uses when the caller names none (mm/s)."""
    return self.z_increments_to_mm(self.z_speed_default_increments)

  @property
  def z_acceleration_default(self) -> float:
    """Z acceleration a move uses when the caller names none (mm/s2)."""
    return round(self.z_acceleration_default_increments * 1000 * self.z_mm_per_increment, 2)

  @property
  def rotation_speed_default(self) -> float:
    """Rotation-drive speed a move uses when the caller names none (deg/s)."""
    return self.rotation_increments_to_deg_per_sec(self.rotation_speed_default_increments)

  @property
  def rotation_acceleration_default(self) -> float:
    """Rotation-drive acceleration a move uses when the caller names none (deg/s2)."""
    return self.rotation_increments_to_deg_per_sec2(self.rotation_acceleration_default_increments)

  @property
  def wrist_speed_default(self) -> float:
    """Wrist-drive speed a move uses when the caller names none (deg/s)."""
    return self.wrist_increments_to_deg_per_sec(self.wrist_speed_default_increments)

  @property
  def wrist_acceleration_default(self) -> float:
    """Wrist-drive acceleration a move uses when the caller names none (deg/s2)."""
    return self.wrist_increments_to_deg_per_sec2(self.wrist_acceleration_default_increments)

  @property
  def gripper_speed_default(self) -> float:
    """Gripper speed a move uses when the caller names none (mm/s)."""
    return self.gripper_increments_to_mm_per_sec(self.gripper_speed_default_increments)

  @property
  def gripper_close_speed_default(self) -> float:
    """Gripper speed a close uses when the caller names none (mm/s)."""
    return self.gripper_increments_to_mm_per_sec(self.gripper_close_speed_default_increments)

  @property
  def gripper_nudge_speed_default(self) -> float:
    """Gripper speed a nudge uses when the caller names none (mm/s)."""
    return self.gripper_increments_to_mm_per_sec(self.gripper_nudge_speed_default_increments)

  @property
  def gripper_stop_band_max(self) -> float:
    """The widest window a close may search either side of its target (mm)."""
    return self.gripper_increments_to_mm(self.gripper_stop_band_range_increments[1])

  @property
  def gripper_acceleration_default(self) -> float:
    """Gripper acceleration a move uses when the caller names none (mm/s2)."""
    return self.gripper_increments_to_mm_per_sec2(self.gripper_acceleration_default_increments)

  def gripper_increments_to_mm(self, increments: int) -> float:
    """A gripper jaw width in mm, from increments."""
    return round(increments * self.gripper_mm_per_increment, 3)

  def gripper_mm_to_increments(self, mm: float) -> int:
    """A gripper jaw width in increments, from mm."""
    return round(mm / self.gripper_mm_per_increment)

  def gripper_mm_per_sec_to_increments(self, mm_per_sec: float) -> int:
    """A gripper-drive speed in increments/s, from mm/s."""
    return round(mm_per_sec / self.gripper_mm_per_increment)

  def gripper_increments_to_mm_per_sec(self, increments: int) -> float:
    """A gripper-drive speed in mm/s, from increments/s."""
    return round(increments * self.gripper_mm_per_increment, 2)

  def gripper_mm_per_sec2_to_increments(self, mm_per_sec2: float) -> int:
    """A gripper-drive acceleration in thousands of increments/s2, from mm/s2."""
    return round(mm_per_sec2 / self.gripper_mm_per_increment / 1000)

  def gripper_increments_to_mm_per_sec2(self, increments: int) -> float:
    """A gripper-drive acceleration in mm/s2, from thousands of increments/s2."""
    return round(increments * 1000 * self.gripper_mm_per_increment, 2)


class iSWAP:
  """The internal Swivel Arm Plate (iSWAP) handler.

  Reached as `driver.iswap`, on a device that has one. It is addressed as `R0`, but the commands
  that move it go to the master, so this feature speaks to both.
  """

  park_traverse_height_range: Tuple[float, float] = (145.0, 360.0)
  """The band a park may be asked to lift to, in mm.

  The top is what the command itself accepts; the floor is this driver's, and parking is refused
  below it."""

  default_minimum_traverse_height: float = 284.0
  """How high the arm lifts to before it travels, in mm, when a caller names no height.

  What legacy sends. Parking drives the arm down to its nest, so this is what holds it clear of the
  deck until it is home."""

  def __init__(self, driver: "STARDriver", configuration: Optional[iSWAPConfiguration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the iSWAP's device facts. Defaults to `iSWAPConfiguration()`.
    """
    self._driver = driver
    self.configuration = configuration or iSWAPConfiguration()
    self.resource: Optional[iSWAPHead] = None
    self.gripped: Optional[bool] = None
    """Whether the arm is holding something, as it last reported.

    None until anything has asked. Set by `request_plate_gripped`, which is the only thing that
    knows: the arm reports it from the fingers themselves, so a plate is not something this driver
    can infer from the commands it sent."""
    self.link_1: Optional[LinkBody] = None
    self.gripper: Optional[MechanicalGripper] = None

  @property
  def arm(self) -> "XArm":
    """The arm carrying this iSWAP.

    It has no X drive of its own: it rides the arm, offset from the carriage reference point by
    `configuration.rotation_drive_x_offset`.

    Returns:
      The arm.
    """
    return next(a for a in self._driver.arms if a.iswap is self)

  # -- session / discovery ---------------------------------------------------

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    """Request the iSWAP's firmware version and build date.

    Returns:
      The version string as reported, and the date in it.
    """
    resp: str = await self._driver.send_command(module="R0", command="RF")
    return resp.split("rf")[-1], parse_firmware_version_date(resp)

  async def rotation_drive_request_x_offset(self) -> float:
    """Request the X distance from the X-arm carriage centre to the rotation drive.

    Stored in the master's own memory, as the 96-head's offset is.

    Returns:
      The offset in mm.
    """
    resp = await self._driver.send_command(module="C0", command="RA", ra="kg", fmt="kg###")
    return cast(int, resp["kg"]) / 10.0

  async def rotation_drive_request_positions(self) -> iSWAPRotationPositions:
    """Request the rotation drive's stored position table.

    The device returns ten signed slots. Nine are positions and the tenth is link 1's length, so
    both are recorded here rather than costing a second read of the same table.

    Returns:
      Each named stop's motor increments.
    """
    c = self.configuration
    slots = await self._request_slots("pw")
    c.rotation_drive_predefined_increments = iSWAPRotationPositions(*slots[:9])
    c.link_1_length = round(slots[9] / 10, 1)
    return c.rotation_drive_predefined_increments

  async def wrist_drive_request_positions(self) -> iSWAPWristPositions:
    """Request the wrist twist drive's stored position table.

    Its tenth slot carries link 2's length, recorded here alongside the stops.

    Returns:
      Each named stop's motor increments.
    """
    c = self.configuration
    slots = await self._request_slots("pt")
    c.wrist_drive_predefined_increments = iSWAPWristPositions(*slots[:9])
    c.tool_length = round(slots[9] / 10, 1)
    return c.wrist_drive_predefined_increments

  async def rotation_drive_request_y_stops(self) -> Dict[str, float]:
    """Request the stored Y stops the carriage is calibrated against.

    The stored table, not where the carriage is now: `rotation_drive_request_y_position` is what
    reads that.

    Returns:
      Each named stop in mm.
    """
    c = self.configuration
    slots = await self._request_slots("py")
    c.rotation_drive_predefined_y_positions_increments = iSWAPYPositions(*slots)
    return {name: c.y_increments_to_mm(slot) for name, slot in zip(iSWAPYPositions.SLOTS, slots)}

  async def request_link_1_length(self) -> float:
    """Request the distance from the rotation joint to the wrist joint.

    Returns:
      Length in mm.
    """
    return round((await self._request_slots("pw"))[9] / 10, 1)

  async def request_tool_center_point_xy_length(self) -> float:
    """Request the distance from the wrist joint to the gripper finger centre in the x-y plane.

    Returns:
      Length in mm.
    """
    return round((await self._request_slots("pt"))[9] / 10, 1)

  async def rotation_drive_request_predefined_z_positions(self) -> Dict[str, float]:
    """Read the Z stops the rotation drive is calibrated against, in mm on the deck.

    The stored table rather than where the drive is now. Its ten slots are all positions, unlike
    the rotation and wrist tables whose tenth slot carries an arm length. The device holds them as
    the finger plane, so each is offset to the drive's bottom the way
    `rotation_drive_request_z_position` reports it, and the two are then in the same terms.

    Beyond home and parking the slots are extra ones, addressable through `R0 ZP` but with no
    documented meaning.

    Returns:
      Each stop in mm, keyed by the position-table field names.
    """
    c = self.configuration
    slots = await self._request_slots("pz")
    c.rotation_drive_predefined_z_positions_increments = iSWAPZPositions(*slots)
    return {
      name: round(c.z_increments_to_mm(increments) + c.rotation_drive_z_offset_above_finger, 1)
      for name, increments in zip(iSWAPZPositions.SLOTS, slots)
    }

  async def gripper_drive_request_widths(self) -> Dict[str, float]:
    """Read the jaw widths the gripper drive is calibrated against, in mm.

    The stored table rather than how far the jaws stand now, which `gripper_request_width` reads.
    Its ten slots are all widths: the one the jaws home and park at, one with no documented
    meaning, the width the drive treats as closed, and seven a plate type is gripped at.

    Records what came back on the configuration, as the other stored tables are recorded, so an
    arm read for it once carries the table from then on.

    Returns:
      Each width in mm, keyed by the position-table field names.
    """
    c = self.configuration
    slots = await self._request_slots("pg")
    c.gripper_drive_predefined_increments = iSWAPGripperPositions(*slots)
    return {
      name: c.gripper_increments_to_mm(increments)
      for name, increments in zip(iSWAPGripperPositions.SLOTS, slots)
    }

  async def _request_slots(self, table: str) -> List[int]:
    """One of the iSWAP's stored tables, as the ten signed slots the device returns."""
    resp = await self._driver.send_command(
      module="R0", command="RA", ra=table, fmt=f"{table}##### (n)"
    )
    return cast(List[int], resp[table])

  async def discover(self):
    """Read this iSWAP's calibration. Read-only: nothing moves."""
    c = self.configuration
    c.firmware_version, c.firmware_date = await self.request_firmware_version()
    if not c.firmware_version.startswith(RECORDED_FIRMWARE_PREFIX):
      logger.warning(
        "this iSWAP reports firmware %s; the ranges and resolutions here were recorded from an arm "
        "reporting %sx, so every position, angle and width converted from them may be wrong. Set "
        "them on iSWAPConfiguration to correct it.",
        c.firmware_version,
        RECORDED_FIRMWARE_PREFIX,
      )
    c.rotation_drive_x_offset = await self.rotation_drive_request_x_offset()
    # Every stored table, through the one reader each has: a table read two ways is a table whose
    # two ways drift. Each records what it read, so a configuration saved after setup carries all
    # of them - left out, they save as nothing, and a simulated arm built from that file cannot
    # answer where its Z drive or its jaws are.
    await self.rotation_drive_request_y_stops()
    await self.rotation_drive_request_positions()
    await self.wrist_drive_request_positions()
    await self.rotation_drive_request_predefined_z_positions()
    await self.gripper_drive_request_widths()
    await self.declare_front_limit()

  async def declare_front_limit(self) -> None:
    """Work out how far forward the rotation drive can be brought, and record it.

    The drive's back stop is its own and is read with the rest of its stored table. Its front stop
    is not: the channels ride in front of it on the same rail, so how far forward it goes is a fact
    about the arm it is on rather than about the drive. It is worked out here, once, from the arm's
    own channel count and widths - `rotation_drive_front_limit` is the arithmetic - and it replaces
    the assumed twelve-channel limit the configuration carries until this runs.

    The channels are discovered alongside this feature rather than before it, so a width that has
    not arrived yet is asked for here rather than waited on. Nothing moves: these are reads, and
    the same read the channels' own discovery makes.

    An arm with no channels keeps whatever the configuration holds: there is nothing in front of
    the drive to work a limit out from, and an assumed limit is better than none.
    """
    device = self._driver.configuration
    pipettes = self.arm.pipettes
    if device is None or pipettes is None or not pipettes.configuration.channels:
      return
    widths = [channel.width for channel in pipettes.configuration.channels]
    if any(width is None for width in widths):
      widths = [await pipettes.request_min_pipette_width(channel) for channel in range(len(widths))]
    front = (
      device.left_arm_min_y_position if self.arm.side == "left" else device.right_arm_min_y_position
    )
    self.configuration.rotation_drive_y_min = round(
      rotation_drive_front_limit(
        front, cast(List[float], widths), self.configuration.rotation_drive_swept_radius
      ),
      2,
    )

  # -- initialization --------------------------------------------------------

  async def initialize(self):
    """Initialize the iSWAP. This moves it."""
    return await self._driver.send_command(module="C0", command="FI", subsystem="R0")

  # -- where it is -----------------------------------------------------------

  def rotation_drive_update_angle(self, angle: float) -> None:
    """Record which way the arm points on the resource that models it.

    The carriage is what the arm is mounted on, so the arm's angle is carried there and anything
    hung off it - the links, and what they hold - turns with it. Stated as the deck angle link 1
    lies along, which is the rotation drive's own angle less ninety degrees, so a resource's
    rotation reads in the frame every other resource is placed in.

    Does nothing until there is a resource to record it on.

    Args:
      angle: the rotation drive's angle, in degrees, as it reports it.
    """
    if self.resource is None:
      return
    self.resource.rotation_drive_angle = angle
    if self.link_1 is not None:
      # The carriage does not turn; the arm mounted on it does. Link 1 leaves the drive at the
      # drive's own angle less ninety degrees, which is the deck angle it lies along.
      self.link_1.rotate_to(z=angle - 90.0, pivot_coordinate=self.link_1.proximal_joint)

  def rotation_drive_get_reference_point_location(self) -> Optional[Coordinate]:
    """Where the model has the rotation drive's reference point, in mm on the deck.

    The inverse of `update_location_by_reference_point`: it converts a reported position into a
    location, and this converts a location back into the position that would be reported. X is
    the arm's, so it is carried through unread.

    Returns:
      Where the model has it, or None when there is nothing modelling it yet.
    """
    deck = self._driver.deck
    if self.resource is None or self.resource.location is None or deck is None:
      return None
    arm = self.resource.parent
    if arm is None:
      return None
    return self.resource.location + arm.get_location_wrt(deck) + self.resource.reference_point

  def wrist_drive_get_angle(self) -> Optional[float]:
    """Which way the model has the wrist turned, as its drive reports it.

    Read from what the drive last reported, as `rotation_drive_get_angle` is: link 2's own rotation is an
    angle from link 1 about a different axis, so recovering a drive angle from it would be
    inverting a rendering rather than reading a fact.

    Returns:
      The angle in degrees, or None while nothing has read it yet.
    """
    return None if self.resource is None else self.resource.wrist_drive_angle

  def rotation_drive_get_angle(self) -> Optional[float]:
    """Which way the model has the arm pointing, as the rotation drive reports it.

    Read from what the drive last reported, not converted back out of the resource's `rotation`:
    that is a deck angle about a different axis, so recovering a drive angle from it would be
    inverting a rendering rather than reading a fact.

    Returns:
      The angle in degrees, or None while nothing has read it yet.
    """
    return None if self.resource is None else self.resource.rotation_drive_angle

  def wrist_drive_update_angle(self, angle: float) -> None:
    """Record which way the wrist is turned on the resource that models it.

    Link 2 turns on the wrist, which link 1 carries, so its angle is measured from link 1 rather
    than from the deck: a resource's rotation adds to its parent's, and link 1 is its parent. What
    the wrist reports when it is straight is where link 2 continues link 1, so the angle here is
    however far the wrist has turned from that.

    Does nothing until the links are modelled.

    Args:
      angle: the wrist drive's angle, in degrees, as it reports it.
    """
    c = self.configuration
    if self.resource is not None:
      self.resource.wrist_drive_angle = angle
    if self.link_1 is None or self.gripper is None or c.wrist_drive_predefined_increments is None:
      return
    straight = c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.straight)
    # The gripper is bolted to link 1's far end, which is link 1's length along its own span.
    self.gripper.rotate_to(z=angle - straight, pivot_coordinate=self.gripper.proximal_joint)

  def gripper_update_width(self, width: float) -> None:
    """Record how far apart the jaws stand on the resource that models them.

    Does nothing until the gripper is modelled, and nothing when the width is outside what the
    model says the fingers do - it says so instead, because the two disagreeing is a question
    about the geometry rather than something to paper over.

    Args:
      width: how far apart the jaws stand, in mm, as the drive reports it.
    """
    gripper = self.gripper
    if gripper is None:
      return
    low, high = gripper.jaw_range
    if not low <= width <= high:
      logger.warning(
        "the gripper reports its jaws %.1f mm apart, outside the %.1f to %.1f mm the model says "
        "they travel, so the model is left where it is",
        width,
        low,
        high,
      )
      return
    gripper.jaw_width = width

  def update_location_by_reference_point(
    self, y: Optional[float] = None, z: Optional[float] = None
  ) -> None:
    """Record where the rotation drive is on the resource that models it.

    Y and Z only: the drive rides the arm, so its resource is a child of the arm's and follows it
    in X without anything having to record that. The drives report the point the resource states as
    its `reference_point`, and a resource is located by its left front bottom corner, so that point
    is taken out before either value is recorded.

    Both drives answer in the deck's frame, while a resource's location is measured from its
    parent, which here is the arm. The arm's own position is taken out too. Does nothing when the
    driver was given no deck, and so has nothing to model.

    Args:
      y: where the drive is now, in mm on the deck. Left as it was when None.
      z: where its bottom is now, in mm on the deck. Left as it was when None.
    """
    deck = self._driver.deck
    if self.resource is None or self.resource.location is None or deck is None:
      return
    arm = self.resource.parent
    if arm is None:
      return
    here, on_the_arm = self.resource.location, arm.get_location_wrt(deck)
    anchor = self.resource.reference_point
    self.resource.location = Coordinate(
      here.x,
      here.y if y is None else y - on_the_arm.y - anchor.y,
      here.z if z is None else z - on_the_arm.z - anchor.z,
    )

  def _check_reachable(self, axis: Literal["x", "y", "z"], value: float) -> None:
    """Raise if the rotation drive cannot be sent where it is being asked to go.

    The one gate every position passes through. What the iSWAP is allowed to do is decided in one
    place: travel limits now, and whatever else has to hold before it moves as it is added.

    The carriage the Y and Z drives position, which is what every move here commands. Where the
    gripper ends up is `_check_pose_reachable`, which works it out from the joint angles: the arm
    reaches past the carriage, and how far and in which direction is what the joints decide.

    Args:
      axis: which axis - `x` along the rail, `y` across the deck, `z` up.
      value: where the rotation drive would be sent, in mm.

    Raises:
      ValueError: If the drive cannot reach it.
      RuntimeError: If the limits were not read, so how far it reaches is unknown.
    """
    c = self.configuration
    device = self._driver.configuration
    if device is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")

    if axis == "x":
      x_range = self.arm.configuration.x_range
      if x_range is None:
        raise RuntimeError("the arm's X travel is not known; have you called `star.setup()`?")
      if c.rotation_drive_x_offset is None:
        raise RuntimeError("the drive's X offset was not read; have you called `star.setup()`?")
      low = x_range[0] - c.rotation_drive_x_offset
      high = x_range[1] - c.rotation_drive_x_offset
    elif axis == "y":
      if c.rotation_drive_y_max is None:
        raise RuntimeError("the drive's Y limit was not read; have you called `star.setup()`?")
      low = (
        device.left_arm_min_y_position
        if self.arm.side == "left"
        else device.right_arm_min_y_position
      )
      high = c.rotation_drive_y_max
    else:
      low, high = c.rotation_drive_z_range

    if not low <= value <= high:
      raise ValueError(
        f"{axis} must be between {round(low, 1)} and {round(high, 1)} mm for the rotation drive, "
        f"is {value}"
      )

  # ----------------------------------------
  # Linear Movement
  # ----------------------------------------

  # -- x position --------------------------------------------------------------------------------

  async def rotation_drive_request_x_position(self) -> float:
    """Read where the rotation drive is along X, in deck mm.

    Returns:
      The rotation drive's X in mm.

    Raises:
      RuntimeError: If the drive's X offset was not read.
    """
    offset = self.configuration.rotation_drive_x_offset
    if offset is None:
      raise RuntimeError(
        "the rotation drive's X offset was not read; have you called `star.setup()`?"
      )
    return round(await self.arm.request_position() - offset, 2)

  # -- y position --------------------------------------------------------------------------------

  async def rotation_drive_request_y_position(self) -> float:
    """Read where the rotation drive is along Y, in deck mm.

    The Y carriage the rotation joint is mounted on, not the gripper finger's Y: where the finger
    is depends on the rotation and wrist angles as well. `request_pose` is what resolves those.

    Returns:
      The rotation drive's Y in mm.
    """
    resp = await self._driver.send_command(module="R0", command="RY", fmt="ry##### (n)")
    # Two counters come back, the firmware's and the hardware's. The hardware one is read. Rounded
    # once, by the conversion: rounding again here left a position coarser than the limits it is
    # compared against, and a carriage at its own back stop reading past it.
    y = self.configuration.y_increments_to_mm(cast(List[int], resp["ry"])[1])
    self.update_location_by_reference_point(y=y)
    return y

  async def _record_where_it_stopped(self, axis: Literal["y", "z", "gripper"]) -> None:
    """Read where a drive came to rest, and record it.

    For a move's failure path. A move that stopped part way left the drive somewhere no target
    describes. Its own failure is logged and swallowed: it must not replace the move's exception,
    which is the one that says what went wrong.

    Args:
      axis: which drive the move drove - `y` across the deck, `z` up and down, `gripper` the jaws.
    """
    try:
      if axis == "y":
        await self.rotation_drive_request_y_position()
      elif axis == "z":
        await self.rotation_drive_request_z_position()
      else:
        await self.gripper_request_width()
    except Exception:
      logger.warning("could not read where the iSWAP stopped along %s; its model is stale", axis)

  async def _unchecked_fw_rotation_drive_move_to_y_position_increments(
    self,
    y_increments: int,
    speed_increments: Optional[int] = None,
    acceleration_level: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Drive the rotation drive to an absolute Y. Nothing is guarded and nothing is recorded.

    Args:
      y_increments: where the drive is to go, in the increments it counts in.
      speed_increments: max velocity, in increments/s.
      acceleration_level: which acceleration curve to use, 1 or 2.
      current_limit: the motor current limit, 0 to 7.
    """
    c = self.configuration
    if speed_increments is None:
      speed_increments = c.y_speed_default_increments
    if acceleration_level is None:
      acceleration_level = c.y_acceleration_level_default
    if current_limit is None:
      current_limit = c.y_current_limit_default
    return await self._driver.send_command(
      module="R0",
      command="YA",
      ya=f"{y_increments:05}",
      yv=f"{speed_increments:04}",
      yr=f"{acceleration_level}",
      yw=f"{current_limit}",
    )

  async def rotation_drive_move_to_y_position(
    self,
    y: float,
    make_space: bool = False,
    speed: Optional[float] = None,
    acceleration_level: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the rotation drive along Y. This moves it.

    The backmost channel is what the drive can run into, so how far back it may go depends on
    where that channel is. The drive and its arm are treated as one circle of
    `configuration.rotation_drive_swept_radius`, which keeps the clearance true whichever way the
    arm is turned.

    Args:
      y: where to put the rotation drive, in mm.
      make_space: whether the channels may be moved aside when the backmost is where the drive
        needs to be. Off by default; making space raises them to Z safety first.
      speed: how fast, in mm/s.
      acceleration_level: how hard to accelerate, 1 or 2.
      current_limit: the motor current limit, 0 to 7.

    Raises:
      ValueError: If the drive cannot reach it, if the arm's current pose carried to it would put a
        joint behind the X-arm or out of reach, if any of the drive parameters is outside what it
        accepts, or if the channels are in the way and may not be moved.
      RuntimeError: If the device's configuration or the drive's Y limit was not read, or the arm is
        modelled but its angles have not been read.
    """
    c = self.configuration
    if speed is None:
      speed = c.y_speed_default
    if acceleration_level is None:
      acceleration_level = c.y_acceleration_level_default
    if current_limit is None:
      current_limit = c.y_current_limit_default
    device = self._driver.configuration
    if device is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    self._check_reachable("y", y)

    speed_increments = c.y_mm_to_increments(speed)
    speed_low, speed_high = c.y_speed_range_increments
    if not speed_low <= speed_increments <= speed_high:
      raise ValueError(
        f"speed must be between {c.y_increments_to_mm(speed_low)} and "
        f"{c.y_increments_to_mm(speed_high)} mm/s, is {speed}"
      )
    if not 1 <= acceleration_level <= 2:
      raise ValueError(f"acceleration_level must be 1 or 2, is {acceleration_level}")
    if not 0 <= current_limit <= 7:
      raise ValueError(f"current_limit must be between 0 and 7, is {current_limit}")

    # The arm rides the carriage, so a Y move carries its pose along without turning a joint: a pose
    # that stands clear here can reach behind the X-arm at the new Y. Checked at the angles the model
    # has, as a rotation is checked at the Y the model has. With no arm modelled - a driver given no
    # deck - there is nothing to check, and linear moves go ahead as they always have.
    if self.rotation_drive_get_reference_point_location() is not None and self.gripper is not None:
      rotation, wrist = self.rotation_drive_get_angle(), self.wrist_drive_get_angle()
      if rotation is None or wrist is None:
        raise RuntimeError("the arm's angles have not been read; have you called `star.setup()`?")
      self._check_pose_reachable(rotation, wrist, y=y)

    # Every argument is checked before this: making space moves the channels, and a move refused
    # afterwards would leave the deck rearranged for a command that never ran.
    await self._make_space_for_y(y, make_space=make_space)

    try:
      resp = await self._unchecked_fw_rotation_drive_move_to_y_position_increments(
        y_increments=c.y_mm_to_increments(y),
        speed_increments=speed_increments,
        acceleration_level=acceleration_level,
        current_limit=current_limit,
      )
      # What was asked for, recorded as soon as the move answers, so the model holds it even if
      # the read below cannot be taken.
      self.update_location_by_reference_point(y=y)
      return resp
    finally:
      # And then what the drive says, which is the last word either way. A move that stopped part
      # way left the carriage somewhere no target describes, and a move that answered has still
      # only answered.
      await self._record_where_it_stopped("y")

  async def _unchecked_fw_position_components_for_free_y_range(self):
    """Position all components so that there is maximum free Y range for the iSWAP. Nothing is
    guarded and nothing is recorded. This moves the channels.
    """
    return await self._driver.send_command(module="C0", command="FY")

  async def _unchecked_fw_release_brake(self):
    """Release the arm's brake. Nothing is guarded and nothing is recorded.

    Dangerous: the brake is what holds the arm up, so releasing it drops whatever it is holding.
    """
    return await self._driver.send_command(module="R0", command="BA")

  async def _unchecked_fw_reengage_brake(self):
    """Re-engage the arm's brake. Nothing is guarded and nothing is recorded."""
    return await self._driver.send_command(module="R0", command="BO")

  async def make_space(self) -> None:
    """Clear the deck volume for the iSWAP. This moves the channels and any head.

    The channels are raised to Z safety and then moved aside; a head is only raised. Nothing may
    travel in Y while one of them is low. The raises are commanded here, not left to
    `_unchecked_fw_position_components_for_free_y_range` to arrange on the way.

    What each carries is read before it is moved. A tip hangs below the drive that holds it, so
    the volume cleared here is not the one a bare channel or head occupies, and the reads are what
    put that on the record. The channels sense theirs; a head only reports what its firmware holds.

    The Y positioning is the master's own, which places every component for the widest free Y
    range there is, rather than this driver working out where each channel should stand. It does
    not say where it left them, so they are read back afterwards either way.
    """
    arm = self.arm
    if arm.pipettes is not None:
      tips = await arm.pipettes.sense_tip_presence()
      mounted = [channel for channel, tip in enumerate(tips) if tip]
      if mounted:
        logger.info("the deck volume is being cleared with tips on channels %s", mounted)
      await arm.pipettes.move_to_safe_z()
    for head in (arm.head96, arm.head384):
      if head is None:
        continue
      # TODO: warn when the head is clearing the deck volume with tips on it. The head has no
      # sleeve sensor, so this has to come from the model rather than from a read, and nothing
      # models what a head carries until tip handling is integrated.
      # if await head.request_tip_presence():
      #   logger.info(
      #     "the deck volume is being cleared with the head's firmware holding that it carries tips"
      #   )
      await head.move_to_safe_z()
    try:
      await self._unchecked_fw_position_components_for_free_y_range()
    finally:
      if arm.pipettes is not None:
        await arm.pipettes.request_y_positions()
      await self.rotation_drive_request_y_position()

  async def _make_space_for_y(self, y: float, make_space: bool) -> None:
    """Make sure the backmost channel is out of the way before the drive travels to `y`.

    Args:
      y: where the rotation drive is going, in mm.
      make_space: whether the channels may be moved to make that space.

    Raises:
      ValueError: If the channel is in the way and either may not be moved, or cannot move far
        enough to clear it.
    """
    pipettes = self.arm.pipettes
    if pipettes is None:
      return

    device = self._driver.configuration
    if device is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")

    widths = [channel.width for channel in pipettes.configuration.channels]
    if any(width is None for width in widths):
      raise RuntimeError("the channels have no width read yet; have you called `star.setup()`?")

    # Where the backmost channel would have to be for the drive to reach `y`, and the furthest
    # back it can get: every channel behind it packed against the front of their travel.
    backmost_y = await pipettes.request_y_position(0)
    target_y = y - cast(float, widths[0]) / 2 - self.configuration.rotation_drive_swept_radius
    furthest_back = device.left_arm_min_y_position + sum(cast(List[float], widths[1:]))

    if backmost_y <= target_y:
      return
    if target_y < furthest_back:
      raise ValueError(
        f"y={y} mm is out of reach: it needs the backmost channel at {round(target_y, 1)} mm, and "
        f"the channels do not fit behind {round(furthest_back, 1)} mm"
      )
    if not make_space:
      raise ValueError(
        f"y={y} mm needs the backmost channel at {round(target_y, 1)} mm or further front, and it "
        f"is at {backmost_y} mm. Pass make_space=True to move the channels out of the way"
      )

    # Channel 0 goes exactly as far forward as this y needs, and the channels behind it follow by
    # their own minimum spacing. Everything is raised first: the channels are about to travel in
    # Y, and the iSWAP is about to travel past whatever a head would leave in its path.
    await pipettes.move_to_safe_z()
    for head in (self.arm.head96, self.arm.head384):
      if head is not None:
        await head.move_to_safe_z()
    await pipettes.move_to_y_positions({0: target_y}, make_space=True)

  async def rotation_drive_request_z_position(self) -> float:
    """Read where the rotation drive's lowest point is along Z.

    The drive reports two counters, the firmware's and the hardware's. The hardware counter is
    the one read, as legacy reads it.

    Returns:
      The rotation drive's bottom Z in mm.
    """
    resp = await self._driver.send_command(module="R0", command="RZ", fmt="rz##### (n)")
    finger_plane = self.configuration.z_increments_to_mm(cast(List[int], resp["rz"])[1])
    z = round(finger_plane + self.configuration.rotation_drive_z_offset_above_finger, 3)
    self.update_location_by_reference_point(z=z)
    return z

  async def _unchecked_fw_rotation_drive_move_to_z_position_increments(
    self,
    z_increments: int,
    speed_increments: Optional[int] = None,
    acceleration_increments: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Drive the rotation drive to an absolute Z. Nothing is guarded and nothing is recorded.

    The drive is calibrated to the gripper finger plane, so what it counts is that plane's height
    rather than the drive's own: `rotation_drive_move_to_z_position` is what takes the offset out.

    Args:
      z_increments: where the finger plane is to go, in the increments the drive counts in.
      speed_increments: max velocity, in increments/s.
      acceleration_increments: in thousands of increments/s2.
      current_limit: the motor current limit, 0 to 7.
    """
    c = self.configuration
    if speed_increments is None:
      speed_increments = c.z_speed_default_increments
    if acceleration_increments is None:
      acceleration_increments = c.z_acceleration_default_increments
    if current_limit is None:
      current_limit = c.z_current_limit_default
    return await self._driver.send_command(
      module="R0",
      command="ZA",
      za=f"{z_increments:+06}",
      zv=f"{speed_increments:05}",
      zr=f"{acceleration_increments:03}",
      zw=f"{current_limit}",
    )

  async def rotation_drive_move_to_z_position(
    self,
    z: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the rotation drive's lowest point to a Z position. This moves it.

    Args:
      z: where to put the rotation drive's bottom, in mm.
      speed: how fast, in mm/s.
      acceleration: how hard, in mm/s2.
      current_limit: the motor current limit, 0 to 7.

    Raises:
      ValueError: If any of them is outside what the drive accepts.
    """
    c = self.configuration
    if speed is None:
      speed = c.z_speed_default
    if acceleration is None:
      acceleration = c.z_acceleration_default
    if current_limit is None:
      current_limit = c.z_current_limit_default
    self._check_reachable("z", z)

    speed_increments = c.z_mm_to_increments(speed)
    speed_low, speed_high = c.z_speed_range_increments
    if not speed_low <= speed_increments <= speed_high:
      raise ValueError(
        f"speed must be between {c.z_increments_to_mm(speed_low)} and "
        f"{c.z_increments_to_mm(speed_high)} mm/s, is {speed}"
      )

    # The drive counts acceleration in thousands of increments per second squared.
    acceleration_increments = c.z_mm_to_increments(acceleration / 1000)
    acceleration_low, acceleration_high = c.z_acceleration_range_increments
    if not acceleration_low <= acceleration_increments <= acceleration_high:
      raise ValueError(
        f"acceleration must be between {c.z_increments_to_mm(acceleration_low * 1000)} and "
        f"{c.z_increments_to_mm(acceleration_high * 1000)} mm/s2, is {acceleration}"
      )

    if not 0 <= current_limit <= 7:
      raise ValueError(f"current_limit must be between 0 and 7, is {current_limit}")

    finger_plane = z - c.rotation_drive_z_offset_above_finger
    try:
      resp = await self._unchecked_fw_rotation_drive_move_to_z_position_increments(
        z_increments=c.z_mm_to_increments(finger_plane),
        speed_increments=speed_increments,
        acceleration_increments=acceleration_increments,
        current_limit=current_limit,
      )
      # What was asked for, recorded as soon as the move answers, so the model holds it even if
      # the read below cannot be taken.
      self.update_location_by_reference_point(z=z)
      return resp
    finally:
      # And then what the drive says, which is the last word either way.
      await self._record_where_it_stopped("z")

  async def rotation_drive_move_to_safe_z_height(
    self,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ) -> float:
    """Move the iSWAP up to the top of its Z travel, and read where that put it. This moves it.

    The precondition for any lateral move, as it is for the channels and the heads. The iSWAP has
    no Z-safety command of its own, so this is an ordinary Z move to the top of `configuration.rotation_drive_z_range`.

    Args:
      speed: how fast, in mm/s.
      acceleration: how hard, in mm/s2.
      current_limit: the motor current limit, 0 to 7.

    Returns:
      The rotation drive's bottom Z once there, in mm.
    """
    c = self.configuration
    if speed is None:
      speed = c.z_speed_default
    if acceleration is None:
      acceleration = c.z_acceleration_default
    if current_limit is None:
      current_limit = c.z_current_limit_default
    await self.rotation_drive_move_to_z_position(
      self.configuration.rotation_drive_z_range[1],
      speed=speed,
      acceleration=acceleration,
      current_limit=current_limit,
    )
    # The move read the drive back and recorded it on the way out, so the model holds where it
    # stopped. Asking again would be a second `RZ` for the same answer, on the one method every
    # lateral move goes through. Without a deck there is no model to hold it, and then it is read.
    here = self.rotation_drive_get_reference_point_location()
    return here.z if here is not None else await self.rotation_drive_request_z_position()

  # ----------------------------------------
  # Rotational Movement
  # ----------------------------------------

  # -- rotation, wrist and gripper --------------------------------------------
  # -- both joints, which the drive command carries together -----------------------

  async def _unchecked_fw_rotation_drive_rotate_increments(
    self,
    rotation_increments: int,
    wrist_increments: int,
    rotation_speed_increments: Optional[int] = None,
    wrist_speed_increments: Optional[int] = None,
    rotation_acceleration_increments: Optional[int] = None,
    wrist_acceleration_increments: Optional[int] = None,
    rotation_current_limit: Optional[int] = None,
    wrist_current_limit: Optional[int] = None,
  ):
    """Drive both joints to absolute increments. Nothing is guarded and nothing is recorded.

    The lowest command there is here: it takes what the drives count in and sends it. Both joints
    go in one command because they move together - the wrist rides the rotation drive, so sending
    them separately turns the arm and then corrects the wrist, sweeping a path neither target
    describes. A caller that means to move one holds the other at where it already is.

    Args:
      rotation_increments: where the rotation drive is to go, signed.
      wrist_increments: where the wrist drive is to go, signed.
      rotation_speed_increments: max velocity of the rotation drive, in increments/s.
      wrist_speed_increments: max velocity of the wrist drive, in increments/s.
      rotation_acceleration_increments: for the rotation drive, in thousands of increments/s2.
      wrist_acceleration_increments: for the wrist drive, in thousands of increments/s2.
      rotation_current_limit: the rotation motor's current limit.
      wrist_current_limit: the wrist motor's current limit.
    """
    c = self.configuration
    if rotation_speed_increments is None:
      rotation_speed_increments = c.rotation_speed_default_increments
    if wrist_speed_increments is None:
      wrist_speed_increments = c.wrist_speed_default_increments
    if rotation_acceleration_increments is None:
      rotation_acceleration_increments = c.rotation_acceleration_default_increments
    if wrist_acceleration_increments is None:
      wrist_acceleration_increments = c.wrist_acceleration_default_increments
    if rotation_current_limit is None:
      rotation_current_limit = c.rotation_current_limit_default
    if wrist_current_limit is None:
      wrist_current_limit = c.wrist_current_limit_default
    return await self._driver.send_command(
      module="R0",
      command="PA",
      wa=f"{rotation_increments:+06}",
      wv=f"{rotation_speed_increments:05}",
      wr=f"{rotation_acceleration_increments:03}",
      ww=f"{rotation_current_limit}",
      ta=f"{wrist_increments:+06}",
      tv=f"{wrist_speed_increments:05}",
      tr=f"{wrist_acceleration_increments:03}",
      tw=f"{wrist_current_limit}",
    )

  def _resolve_rotation_increments(self, angle: Union[str, float]) -> int:
    """A rotation stop's name or an angle, as the increments the drive counts in.

    Args:
      angle: a named stop, or degrees from the calibrated front stop.

    Returns:
      Where the drive is to go, in increments.

    Raises:
      ValueError: If the name is not a stop, or the angle is outside the drive's travel.
      RuntimeError: If the stored stops have not been read.
    """
    c = self.configuration
    if isinstance(angle, str):
      predefined_positions = c.rotation_drive_predefined_increments
      if predefined_positions is None:
        raise RuntimeError("the rotation drive's stops were not read; have you called `setup()`?")
      if angle not in iSWAPRotationPositions.SLOTS:
        raise ValueError(f"{angle!r} is not one of the stops {iSWAPRotationPositions.SLOTS}")
      increments = {
        "home": predefined_positions.home,
        "left": predefined_positions.left,
        "front": predefined_positions.front,
        "right": predefined_positions.right,
        "parking": predefined_positions.parking,
        "extra_1": predefined_positions.extra_1,
        "extra_2": predefined_positions.extra_2,
        "extra_3": predefined_positions.extra_3,
        "extra_4": predefined_positions.extra_4,
      }[angle]
    else:
      increments = c.rotation_drive_angle_to_increments(angle)
    low, high = c.rotation_range_increments
    if not low <= increments <= high:
      raise ValueError(
        f"{angle} is {increments} increments, outside the {low} to {high} the drive travels"
      )
    return increments

  def _resolve_rotation_absolute_increments(self, angle: Union[str, float]) -> int:
    """Where link 1 is to point on the deck, as the increments the rotation drive counts in.

    The drive turns link 1 and nothing else, so the deck angle and the drive's own differ by the
    quarter turn between the drive's front stop and the deck's +x, and a stop's name means the
    same in either frame.

    Args:
      angle: a named stop, or degrees on the deck.

    Returns:
      Where the drive is to go, in increments.

    Raises:
      ValueError: If the name is not a stop, or the deck angle is outside the drive's travel.
      RuntimeError: If the stored stops have not been read.
    """
    if isinstance(angle, str):
      return self._resolve_rotation_increments(angle)
    c = self.configuration
    drive_angle = (angle + 90.0 + 180.0) % 360.0 - 180.0
    increments = c.rotation_drive_angle_to_increments(drive_angle)
    low, high = c.rotation_range_increments
    if not low <= increments <= high:
      raise ValueError(
        f"pointing link 1 at {angle} deg on the deck needs the drive at {drive_angle} deg, which "
        f"is {increments} increments, outside the {low} to {high} it travels"
      )
    return increments

  def _resolve_gripper_direction_increments(
    self, angle: Union[str, float], rotation_increments: int
  ) -> int:
    """A gripper direction, as the increments the wrist drive counts in.

    The wrist carries link 2 on link 1, so where the gripper ends up pointing is both joints
    together. Given where link 1 will be, this is the wrist that points it where it is asked to.

    Args:
      angle: a direction in `GRIPPER_DECK_DIRECTIONS`, or degrees on the deck.
      rotation_increments: where the rotation drive will be, in its own increments.

    Returns:
      Where the wrist drive is to go, in increments.

    Raises:
      ValueError: If the name is not a direction, or the fold it asks of the wrist is outside its
        travel.
      RuntimeError: If the wrist's stored stops were not read.
    """
    c = self.configuration
    if isinstance(angle, str):
      if angle not in GRIPPER_DECK_DIRECTIONS:
        raise ValueError(f"{angle!r} is not one of {tuple(GRIPPER_DECK_DIRECTIONS)}")
      deck_angle = GRIPPER_DECK_DIRECTIONS[angle]
    else:
      deck_angle = angle
    if c.wrist_drive_predefined_increments is None:
      raise RuntimeError("the wrist's stored stops were not read; have you called `setup()`?")

    link_1_deck_angle = c.rotation_drive_increments_to_angle(rotation_increments) - 90.0
    straight = c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.straight)
    # A direction is the same direction a turn either way round, so the fold is taken to the
    # half-turn nearest zero before it is asked of the drive: +225 and -135 point the same way,
    # and only one of them is inside the travel.
    wrist_deg = (deck_angle - link_1_deck_angle + straight + 180.0) % 360.0 - 180.0
    increments = c.wrist_deg_to_increments(wrist_deg)
    # A stop's own angle converts back to the increment this arm stores for it, so a named
    # direction off a named rotation lands there without being pushed. What is left is rounding:
    # an angle a hair off a stop takes the stop, which is the tolerance legacy used.
    for stored, _ in (
      (c.wrist_drive_predefined_increments.right, -135.0),
      (c.wrist_drive_predefined_increments.straight, -45.0),
      (c.wrist_drive_predefined_increments.left, 45.0),
      (c.wrist_drive_predefined_increments.reverse, 135.0),
    ):
      if abs(wrist_deg - c.wrist_increments_to_deg(stored)) <= c.wrist_deg_per_increment:
        increments = stored
        break
    low, high = c.wrist_range_increments
    if not low <= increments <= high:
      raise ValueError(
        f"pointing the gripper at {angle} deg with link 1 at {link_1_deck_angle} deg needs the "
        f"wrist at {wrist_deg} deg, outside its travel of "
        f"{c.wrist_increments_to_deg(low)} to {c.wrist_increments_to_deg(high)} deg"
      )
    return increments

  def _resolve_wrist_increments(self, angle: Union[str, float]) -> int:
    """A wrist stop's name or an angle, as the increments the drive counts in.

    Args:
      angle: a named stop, or degrees from the drive's own zero.

    Returns:
      Where the drive is to go, in increments.

    Raises:
      ValueError: If the name is not a stop, or the angle is outside the drive's travel.
      RuntimeError: If the stored stops have not been read.
    """
    c = self.configuration
    if isinstance(angle, str):
      stops = c.wrist_drive_predefined_increments
      if stops is None:
        raise RuntimeError("the wrist drive's stops were not read; have you called `setup()`?")
      if angle not in iSWAPWristPositions.SLOTS:
        raise ValueError(f"{angle!r} is not one of the stops {iSWAPWristPositions.SLOTS}")
      increments = {
        "home": stops.home,
        "right": stops.right,
        "straight": stops.straight,
        "left": stops.left,
        "reverse": stops.reverse,
        "parking": stops.parking,
        "extra_1": stops.extra_1,
        "extra_2": stops.extra_2,
        "extra_3": stops.extra_3,
      }[angle]
    else:
      increments = c.wrist_deg_to_increments(angle)
    low, high = c.wrist_range_increments
    if not low <= increments <= high:
      raise ValueError(
        f"{angle} is {increments} increments, outside the {low} to {high} the wrist travels"
      )
    return increments

  async def rotate_to_angles(
    self,
    rotation_relative_angle: Optional[Union[str, float]] = None,
    rotation_absolute_angle: Optional[Union[str, float]] = None,
    gripper_relative_angle: Optional[Union[str, float]] = None,
    gripper_absolute_angle: Optional[Union[str, float]] = None,
    raise_features: bool = True,
    make_space: bool = False,
    rotation_speed: Optional[float] = None,
    wrist_speed: Optional[float] = None,
    rotation_acceleration: Optional[float] = None,
    wrist_acceleration: Optional[float] = None,
    rotation_current_limit: Optional[int] = None,
    wrist_current_limit: Optional[int] = None,
  ):
    """Rotate one or both iSWAP joints to absolute angles in a single motion. This moves the arm.

    Each joint takes either angle, and a stop's name means the same in both: `relative` is the
    drive's own frame, `absolute` is the deck. They differ for a float - the rotation drive reads
    zero at its front stop, a quarter turn from the deck's +x, and the wrist reads from its own
    zero and turns with link 1 under it. A joint given neither angle holds where it is, read from
    the drive rather than assumed.

    Both joints arrive together under a single motion plan, so the gripper sweeps a straight
    joint-space path and IK-driven trajectories can be executed.

    Collision risk: the whole arm sweeps, and the path is neither joint's alone.

    Args:
      rotation_relative_angle: where the rotation drive is to sit - `left`, `front` or `right` - or
        degrees signed from its front stop. Mutually exclusive with `rotation_absolute_angle`.
      rotation_absolute_angle: where link 1 is to point on the deck - `left`, `front` or `right` -
        or degrees on the deck. Mutually exclusive with `rotation_relative_angle`.
      gripper_relative_angle: where the wrist drive is to sit - `right`, `straight`, `left` or
        `reverse` - or degrees from its own zero. Mutually exclusive with `gripper_absolute_angle`.
      gripper_absolute_angle: where the gripper is to point on the deck - `right`, `front`, `left`
        or `back` - or degrees on the deck. Mutually exclusive with `gripper_relative_angle`.
      raise_features: whether to raise the channels and any head to safe Z before rotating. On by
        default, since the arm sweeps over whatever they are standing in.
      make_space: whether to clear the deck volume of the pose being commanded. Not built yet, so
        passing True refuses rather than rotating without the clearance it promises. `make_space`
        is the blanket clearance in the meantime.
      rotation_speed [deg/sec]: max angular velocity, within what
        `configuration.rotation_speed_range_increments` accepts.
      wrist_speed [deg/sec]: max angular velocity, within what
        `configuration.wrist_speed_range_increments` accepts.
      rotation_acceleration [deg/sec^2]: max angular acceleration, within what
        `configuration.rotation_acceleration_range_increments` accepts.
      wrist_acceleration [deg/sec^2]: max angular acceleration, within what
        `configuration.wrist_acceleration_range_increments` accepts.
      rotation_current_limit: motor current protection limiter, 0..7.
      wrist_current_limit: motor current protection limiter, 0..7.

    Raises:
      RuntimeError: if `setup()` has not populated the predefined-stop tables, or an absolute angle
        is given to a driver with no deck.
      ValueError: if no angle is provided, if a joint is given both of its angles, or if either
        resolved target increment is outside the hardware range.
      NotImplementedError: if `make_space` is True, which is not built for a rotation yet.
    """
    c = self.configuration
    if rotation_speed is None:
      rotation_speed = c.rotation_speed_default
    if wrist_speed is None:
      wrist_speed = c.wrist_speed_default
    if rotation_acceleration is None:
      rotation_acceleration = c.rotation_acceleration_default
    if wrist_acceleration is None:
      wrist_acceleration = c.wrist_acceleration_default
    if rotation_current_limit is None:
      rotation_current_limit = c.rotation_current_limit_default
    if wrist_current_limit is None:
      wrist_current_limit = c.wrist_current_limit_default
    for relative, absolute, joint, own_frame in (
      (rotation_relative_angle, rotation_absolute_angle, "rotation", "rotation drive"),
      (gripper_relative_angle, gripper_absolute_angle, "gripper", "wrist drive"),
    ):
      if relative is not None and absolute is not None:
        raise ValueError(
          f"pass {joint}_relative_angle or {joint}_absolute_angle, not both: they name the same "
          f"joint, one in the {own_frame}'s own frame and one on the deck"
        )
    if all(
      angle is None
      for angle in (
        rotation_relative_angle,
        rotation_absolute_angle,
        gripper_relative_angle,
        gripper_absolute_angle,
      )
    ):
      raise ValueError("pass at least one angle; all four are None")
    # An absolute angle is on the deck: with no deck, there is nothing to measure it against.
    if (
      rotation_absolute_angle is not None or gripper_absolute_angle is not None
    ) and self.rotation_drive_get_reference_point_location() is None:
      raise RuntimeError(
        "absolute angles are on the deck, and the iSWAP's arm is not modelled: the driver was "
        "given no deck. Pass relative angles instead"
      )
    # Held in the drive's own increments rather than through its angle, so a joint that is holding
    # is sent exactly where it already is.
    if rotation_absolute_angle is not None:
      rotation = self._resolve_rotation_absolute_increments(rotation_absolute_angle)
    elif rotation_relative_angle is not None:
      rotation = self._resolve_rotation_increments(rotation_relative_angle)
    else:
      rotation = await self._rotation_drive_request_increments()
    if gripper_absolute_angle is not None:
      wrist = self._resolve_gripper_direction_increments(gripper_absolute_angle, rotation)
    elif gripper_relative_angle is not None:
      wrist = self._resolve_wrist_increments(gripper_relative_angle)
    else:
      wrist = await self._wrist_drive_request_increments()
    rotation_speed_increments = c.rotation_deg_per_sec_to_increments(rotation_speed)
    wrist_speed_increments = c.wrist_deg_per_sec_to_increments(wrist_speed)
    rotation_acceleration_increments = c.rotation_deg_per_sec2_to_increments(rotation_acceleration)
    wrist_acceleration_increments = c.wrist_deg_per_sec2_to_increments(wrist_acceleration)
    for name, asked, increments, (low, high), in_degrees in (
      (
        "rotation_speed",
        rotation_speed,
        rotation_speed_increments,
        c.rotation_speed_range_increments,
        c.rotation_increments_to_deg_per_sec,
      ),
      (
        "wrist_speed",
        wrist_speed,
        wrist_speed_increments,
        c.wrist_speed_range_increments,
        c.wrist_increments_to_deg_per_sec,
      ),
      (
        "rotation_acceleration",
        rotation_acceleration,
        rotation_acceleration_increments,
        c.rotation_acceleration_range_increments,
        c.rotation_increments_to_deg_per_sec2,
      ),
      (
        "wrist_acceleration",
        wrist_acceleration,
        wrist_acceleration_increments,
        c.wrist_acceleration_range_increments,
        c.wrist_increments_to_deg_per_sec2,
      ),
    ):
      if not low <= increments <= high:
        raise ValueError(
          f"{name} must be between {in_degrees(low)} and {in_degrees(high)}, is {asked}"
        )
    for name, value in (
      ("rotation_current_limit", rotation_current_limit),
      ("wrist_current_limit", wrist_current_limit),
    ):
      if not 0 <= value <= 7:
        raise ValueError(f"{name} must be between 0 and 7, is {value}")

    rotation_target = c.rotation_drive_increments_to_angle(rotation)
    wrist_target = c.wrist_increments_to_deg(wrist)

    # Check 1 - drive compliance: is the pose itself reachable? Says nothing about what else
    # stands on the deck.
    self._check_pose_reachable(rotation_target, wrist_target)

    if raise_features:
      arm = self.arm
      if arm.pipettes is not None:
        await arm.pipettes.move_to_safe_z()
      for head in (arm.head96, arm.head384):
        if head is not None:
          await head.move_to_safe_z()

    # Check 2 - collision detection:

    # channels

    # head

    # if collision_detection is not None and make_space:

    # TODO: clear the deck volume for the pose being commanded, in the shape `_make_space_for_y`
    # uses - the frontmost point the arm would reach, and channel 0 moved in front of it.
    if make_space:
      raise NotImplementedError(
        "make_space is not built for a rotation yet. Rotating anyway would sweep the arm while the "
        "caller believes the deck was cleared, so this refuses instead. Clear the deck volume "
        "first with `make_space()`, or pass make_space=False to accept the pose unchecked"
      )

    try:
      resp = await self._unchecked_fw_rotation_drive_rotate_increments(
        rotation_increments=rotation,
        wrist_increments=wrist,
        rotation_speed_increments=rotation_speed_increments,
        wrist_speed_increments=wrist_speed_increments,
        rotation_acceleration_increments=rotation_acceleration_increments,
        wrist_acceleration_increments=wrist_acceleration_increments,
        rotation_current_limit=rotation_current_limit,
        wrist_current_limit=wrist_current_limit,
      )
      # What was asked for, recorded before anything is read: a move that answered has arrived,
      # and the model says so even if the reads below cannot be taken.
      self.rotation_drive_update_angle(c.rotation_drive_increments_to_angle(rotation))
      self.wrist_drive_update_angle(c.wrist_increments_to_deg(wrist))
      return resp
    finally:
      # And then what the drives say, which is the last word either way. A move that stopped part
      # way left the arm somewhere no target describes, and this is the only thing that finds it.
      await self._record_where_the_joints_stopped()

  def _compute_pose_at_angles(
    self, rotation_angle: float, gripper_relative_angle: float, y: Optional[float] = None
  ) -> iSWAPPose:
    """Where the arm would be with its joints at these angles. Nothing is read or moved.

    Worked from where the model has the drive, so it costs no commands, and it is what both of the
    checks below a move ask. None when the arm is not modelled or the numbers the kinematics need
    have not been read.

    Args:
      rotation_angle: the rotation drive's angle, in degrees.
      gripper_relative_angle: the wrist drive's angle, in degrees.
      y: where the drive would be, in mm. Where the model has it when None.

    Returns:
      The pose.

    Raises:
      RuntimeError: If the arm is not modelled, its gripper is not, or the kinematics' numbers
        have not been read.
    """
    c = self.configuration
    predefined_wrist_positions = c.wrist_drive_predefined_increments
    drive = self.rotation_drive_get_reference_point_location()
    gripper = self.gripper
    if drive is None:
      raise RuntimeError("the iSWAP's arm is not modelled; the driver was given no deck")
    if gripper is None:
      raise RuntimeError("the iSWAP's arm is modelled but its gripper is not")
    if c.link_1_length is None or predefined_wrist_positions is None:
      raise RuntimeError("the arm's link length or the wrist's stops were not read")
    return self._forward_kinematics(
      joints={
        iSWAPAxis.X: drive.x,
        iSWAPAxis.Y: drive.y if y is None else y,
        iSWAPAxis.Z: drive.z,
        iSWAPAxis.ROTATION: rotation_angle,
        iSWAPAxis.WRIST: gripper_relative_angle,
      },
      link_1_length=c.link_1_length,
      # Asked of the tool, not taken off the arm: the gripper knows how far its grip centre sits
      # from the wrist, and a different end-effector would answer differently.
      tool_center_point_distance=gripper.tool_center_point.x,
      wrist_straight_angle=c.wrist_increments_to_deg(predefined_wrist_positions.straight),
      rotation_drive_z_offset_above_finger=c.rotation_drive_z_offset_above_finger,
    )

  def _check_pose_reachable(
    self, rotation_angle: float, gripper_relative_angle: float, y: Optional[float] = None
  ) -> None:
    """Raise if the arm cannot put its gripper where these angles would.

    Not what `_check_reachable` answers: that bounds one value on one axis.

    The X-arm is a rail across the back of the deck, behind the drive's own Y travel, so nothing
    the arm carries may stand further back than the drive itself reaches. A pose is worked out
    before it is commanded rather than discovered on the way into the rail.

    The crudest check there is: two points, at the end of the move, against one bound. It says
    nothing about what the arm sweeps through on the way, nor about anything else standing on the
    deck. Skipped entirely when the arm is not modelled or the numbers it needs have not been
    read - a check that cannot be made must not look like one that passed.

    Args:
      rotation_angle: where the rotation drive is being sent, in degrees.
      gripper_relative_angle: where the wrist is being sent, in degrees.
      y: where the drive is being sent, in mm. Where the model has it when None, which is what a
        rotation leaves it at; a Y move carries the pose to a new Y without turning either joint.

    Raises:
      ValueError: If either joint would land behind the drive's own back stop, or further forward
        than the arm can carry it.
    """
    y_max = self.configuration.rotation_drive_y_max
    if y_max is None or self.rotation_drive_get_reference_point_location() is None:
      return
    pose = self._compute_pose_at_angles(rotation_angle, gripper_relative_angle, y=y)
    at = "" if y is None else f" and the drive at y {y:.1f} mm"
    # Known, or `_compute_pose_at_angles` would have refused to work the pose out at all.
    link_1 = cast(float, self.configuration.link_1_length)
    tool = cast(MechanicalGripper, self.gripper).tool_center_point.x
    # Each joint against the window it can be carried through: back, the drive's own stop, which
    # both joints reach past by their own length; front, the stop the channels leave the drive,
    # less that same length. Both moving joints, not only the far one - link 1 alone is long enough
    # to put the wrist behind the rail while the grip centre is still clear of it.
    y_min = self.configuration.rotation_drive_y_min
    for what, point, front in (
      ("wrist joint", pose.wrist_joint_location, y_min - link_1),
      ("grip centre", pose.gripper_center_location, y_min - link_1 - tool),
    ):
      if point.y > y_max:
        raise ValueError(
          f"rotation {rotation_angle:.2f} deg with the wrist at {gripper_relative_angle:.2f}{at} would put the "
          f"{what} at y {point.y:.1f} mm, behind the {y_max:.1f} mm the rotation drive itself "
          f"reaches - the X-arm runs across the back of the deck there. Turn the arm the other "
          f"way, or move the drive forward first"
        )
      if point.y < front:
        raise ValueError(
          f"rotation {rotation_angle:.2f} deg with the wrist at {gripper_relative_angle:.2f}{at} would put the "
          f"{what} at y {point.y:.1f} mm, in front of the {front:.1f} mm the arm reaches with the "
          f"drive at its own front stop of {y_min:.1f} mm - the channels ride in front of it and "
          f"it stops behind them. Turn the arm the other way, or move the drive back first"
        )

  async def _record_where_the_joints_stopped(self) -> None:
    """Read both joints and record them on the model.

    Its own failure is logged and swallowed: it runs on a move's failure path as well as its
    success, and it must not replace the exception that says what went wrong.
    """
    try:
      await self.rotation_drive_request_angle()
      await self.wrist_drive_request_angle()
    except Exception:
      logger.warning("could not read where the iSWAP's joints stopped; its model is stale")

  # -- rotation drive --------------------------------------------------------------

  async def rotation_drive_request_angle(self) -> float:
    """Read the rotation drive's angle, signed from the calibrated front stop.

    Returns:
      The angle in degrees.
    """
    angle = self.configuration.rotation_drive_increments_to_angle(
      await self._rotation_drive_request_increments()
    )
    self.rotation_drive_update_angle(angle)
    return angle

  async def _rotation_drive_request_increments(self) -> int:
    """Reads the rotation drive's position in the increments the drive counts in.

    Returns:
      int: The drive's position, in increments.
    """
    resp = await self._driver.send_command(module="R0", command="RW", fmt="rw######")
    return cast(int, resp["rw"])

  async def rotation_drive_rotate_to_angle(
    self,
    angle: Union[str, float],
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ):
    """Turn the rotation drive to an angle, holding the wrist where it is. This moves the arm.

    A caller for `rotate_to_angles`, which is where the move and the model update live. The wrist
    is left to hold where it is, so one command carries both joints.

    Args:
      angle: a named stop, or degrees signed from the
        calibrated front stop.
      speed [deg/sec]: max angular velocity.
      acceleration [deg/sec^2]: max angular acceleration.
      current_limit: motor current protection limiter, 0..7.

    Raises:
      ValueError: If the angle lands outside the drive's travel, or an argument is out of range.
    """
    c = self.configuration
    if speed is None:
      speed = c.rotation_speed_default
    if acceleration is None:
      acceleration = c.rotation_acceleration_default
    if current_limit is None:
      current_limit = c.rotation_current_limit_default
    return await self.rotate_to_angles(
      rotation_relative_angle=angle,
      rotation_speed=speed,
      rotation_acceleration=acceleration,
      rotation_current_limit=current_limit,
    )

  # -- wrist drive -----------------------------------------------------------------

  async def wrist_drive_request_angle(self) -> float:
    """Read the wrist drive's angle, signed from the motor's own zero.

    That zero sits between the straight and left stops, which keeps the reachable range symmetric
    about it rather than anchoring it on a stop.

    Returns:
      The angle in degrees.
    """
    angle = self.configuration.wrist_increments_to_deg(await self._wrist_drive_request_increments())
    self.wrist_drive_update_angle(angle)
    return angle

  async def _wrist_drive_request_increments(self) -> int:
    """Reads the wrist drive's position in the increments the drive counts in.

    Returns:
      int: The drive's position, in increments.
    """
    resp = await self._driver.send_command(module="R0", command="RT", fmt="rt######")
    return cast(int, resp["rt"])

  async def wrist_drive_rotate_to_angle(
    self,
    angle: Union[str, float],
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ):
    """Turn the wrist to an angle, holding the rotation drive where it is. This moves the arm.

    The mirror of `rotation_drive_rotate_to_angle`, and the same one move underneath.

    Args:
      angle: one of the named wrist stops - `straight`, `left`, `right`, `reverse`,
        `parking` - which goes to the increment this arm stores for it, or degrees signed from the
        drive's own zero.
      speed [deg/sec]: max angular velocity.
      acceleration [deg/sec^2]: max angular acceleration.
      current_limit: motor current protection limiter, 0..7.

    Raises:
      ValueError: If the angle lands outside the drive's travel, or an argument is out of range.
    """
    c = self.configuration
    if speed is None:
      speed = c.wrist_speed_default
    if acceleration is None:
      acceleration = c.wrist_acceleration_default
    if current_limit is None:
      current_limit = c.wrist_current_limit_default
    # This one is named for its drive, so it stays in the drive's terms: the wrist angle is turned
    # into the deck direction it points the gripper, which is what `rotate_to_angles` takes.
    wrist = self._resolve_wrist_increments(angle)
    rotation = await self._rotation_drive_request_increments()
    link_1_deck_angle = c.rotation_drive_increments_to_angle(rotation) - 90.0
    if c.wrist_drive_predefined_increments is None:
      raise RuntimeError("the wrist's stored stops were not read; have you called `setup()`?")
    straight = c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.straight)
    return await self.rotate_to_angles(
      gripper_absolute_angle=link_1_deck_angle + (c.wrist_increments_to_deg(wrist) - straight),
      wrist_speed=speed,
      wrist_acceleration=acceleration,
      wrist_current_limit=current_limit,
    )

  # -- gripper drive ---------------------------------------------------------------

  async def gripper_request_counters(self) -> Tuple[int, int]:
    """Read both counters the gripper drive keeps, in its own increments.

    The drive answers what the firmware believes it commanded and what the encoder reads back.
    They part when the drive has lost steps - jammed against something, or driven into its own
    stop - and the gap is the only sign of it: a single width read cannot show it, and a drive
    whose counters have parted will refuse to initialize until it has been freed.

    Returns:
      The counter the firmware keeps and the one the encoder reads, in that order.
    """
    resp = await self._driver.send_command(module="R0", command="RG", fmt="rg##### (n)")
    firmware, hardware = cast(List[int], resp["rg"])
    if abs(firmware - hardware) > self.configuration.gripper_counter_drift_increments:
      logger.warning(
        "the gripper drive's counters are %d increments apart (firmware %d, hardware %d), which is "
        "a drive that has lost steps rather than one that has moved",
        abs(firmware - hardware),
        firmware,
        hardware,
      )
    return firmware, hardware

  async def gripper_request_latest_force_applied(self) -> Dict[str, int]:
    """Read what the gripper's force sensor and motor current did during the last movement.

    All of it is the arm's own raw measurement, and the last entry is the only one in engineering
    units - the arm converts it with a divisor it keeps in its own memory.

    Returns:
      The peak drive current and peak force during the last movement, the sensor's idle offset,
      its last reading, all in the sensor's own counts, and that last reading in millinewtons.
    """
    resp = await self._driver.send_command(module="R0", command="RH", fmt="rh#### (n)")
    current, peak, idle, last, millinewtons = cast(List[int], resp["rh"])
    return {
      "peak_current": current,
      "peak_force": peak,
      "idle_offset": idle,
      "last_force": last,
      "last_force_millinewtons": millinewtons,
    }

  async def gripper_request_width(self) -> float:
    """Read how far the gripper jaws are open.

    Where the fingers are, which after a grip is not how wide the thing between them is: a close
    stops by pressing into what it meets, so it leaves them nearer together than the object stands.

    Returns:
      The jaw width in mm.
    """
    # Through the counters rather than off the wire again: it is the same command, and reading it
    # there is what says whether the drive has lost steps. A width taken on its own cannot show
    # that, and a caller who only ever asks how wide the jaws are would never be told.
    _, hardware = await self.gripper_request_counters()
    width = self.configuration.gripper_increments_to_mm(hardware)
    self.gripper_update_width(width)
    return width

  async def request_plate_gripped(self) -> bool:
    """Read whether the arm is holding something between its fingers.

    The arm's own answer, not the model's: the gripper reports it, so a plate taken or dropped by
    anything other than this driver still shows up. `gripped` is what the model says, and the two
    disagreeing means the model has lost track of what the arm is carrying.

    Returns:
      True while it holds something.
    """
    resp = await self._driver.send_command(module="C0", command="QP", subsystem="R0", fmt="ph#")
    gripped = cast(int, resp["ph"]) == 1
    self.gripped = gripped
    return gripped

  async def _unchecked_fw_gripper_move_to_jaw_position_increments(
    self,
    increments: int,
    speed_increments: Optional[int] = None,
    acceleration_increments: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Drive the jaws to an absolute width. Nothing is guarded and nothing is recorded.

    The lowest command there is here: it takes what the drive counts in and sends it. It feels
    nothing on the way - the drive pushes to where it is told with whatever the current limit
    allows, and says so only once it has locked. What checks, chooses and records is
    `gripper_move_to_jaw_position`; the drive's own knobs are here for a caller that needs them.

    Args:
      increments: where the jaws are to go, in the drive's own steps.
      speed_increments: max velocity, in increments/s. The drive's own default when None.
      acceleration_increments: in thousands of increments/s2. The drive's own default when None.
      current_limit: the motor current limit. The drive's own default when None.
    """
    c = self.configuration
    if speed_increments is None:
      speed_increments = c.gripper_speed_default_increments
    if acceleration_increments is None:
      acceleration_increments = c.gripper_acceleration_default_increments
    if current_limit is None:
      current_limit = c.gripper_current_limit_default
    return await self._driver.send_command(
      module="R0",
      command="GA",
      ga=f"{increments:05}",
      gv=f"{speed_increments:04}",
      gr=f"{acceleration_increments:03}",
      gw=f"{current_limit:02}",
    )

  async def gripper_move_to_jaw_position(
    self,
    width: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    current_limit: Optional[int] = None,
  ):
    """Open the jaws to a width. This moves them.

    A position, driven: the jaws go where they are told whether or not something is in the way, and
    the drive says nothing until it has locked. It feels nothing on the way, so closing onto a
    thing is `gripper_close_with_force_sensed_width_window`, which watches the force sensor and
    reports what it met.

    The jaws are read first, so which way this move goes is known rather than assumed. A close the
    caller named no speed for is driven at half the configured default, since whatever the jaws
    meet is met at whatever they were driven at.

    Args:
      width: how far apart to stand the jaws, in mm.
      speed: how fast to drive them, in mm/s. `configuration.gripper_speed_default_increments`
        when None, halved for a close.
      acceleration: how hard to accelerate, in mm/s2.
        `configuration.gripper_acceleration_default_increments` when None.
      current_limit: the motor current limit, 0 the weakest and 15 the strongest.
        `configuration.gripper_current_limit_default` when None.

    Raises:
      ValueError: If the width is outside the drive's travel, or the speed, acceleration or
        current limit is outside what the drive accepts.
    """
    c = self.configuration
    # Compared in mm rather than in increments: a width read off the drive and sent straight back
    # loses a fraction of an increment on the way, and the ends of the travel are exactly the
    # widths a caller asks for when it wants the jaws shut or wide open.
    low = c.gripper_increments_to_mm(c.gripper_range_increments[0])
    high = c.gripper_increments_to_mm(c.gripper_range_increments[1])
    if not low <= width <= high:
      raise ValueError(f"width must be between {low} and {high} mm, is {width}")
    width_increments = min(
      max(c.gripper_mm_to_increments(width), c.gripper_range_increments[0]),
      c.gripper_range_increments[1],
    )

    # Read the jaws before anything is chosen: which way this move goes decides what it is driven
    # at, and a width that happens to equal the default is not the same as a caller naming none.
    closing = width < await self.gripper_request_width()

    if current_limit is None:
      current_limit = c.gripper_current_limit_default
    limit_low, limit_high = c.gripper_current_limit_range
    if not limit_low <= current_limit <= limit_high:
      raise ValueError(
        f"current_limit must be between {limit_low} and {limit_high}, is {current_limit}"
      )

    if speed is not None:
      speed_increments = c.gripper_mm_per_sec_to_increments(speed)
    else:
      # Half speed into a close the caller did not set a speed for: this command feels nothing, so
      # whatever the jaws meet is met at whatever they were driven at.
      speed_increments = c.gripper_speed_default_increments
      if closing:
        speed_increments //= 2
    acceleration_increments = (
      c.gripper_acceleration_default_increments
      if acceleration is None
      else c.gripper_mm_per_sec2_to_increments(acceleration)
    )
    for name, asked, increments, (limit_low, limit_high), in_mm in (
      (
        "speed",
        speed,
        speed_increments,
        c.gripper_speed_range_increments,
        c.gripper_increments_to_mm_per_sec,
      ),
      (
        "acceleration",
        acceleration,
        acceleration_increments,
        c.gripper_acceleration_range_increments,
        c.gripper_increments_to_mm_per_sec2,
      ),
    ):
      if not limit_low <= increments <= limit_high:
        raise ValueError(
          f"{name} must be between {in_mm(limit_low)} and {in_mm(limit_high)}, is {asked}"
        )

    try:
      resp = await self._unchecked_fw_gripper_move_to_jaw_position_increments(
        increments=width_increments,
        speed_increments=speed_increments,
        acceleration_increments=acceleration_increments,
        current_limit=current_limit,
      )
      # What was asked for, recorded as soon as the move answers, so the model holds it even if
      # the read below cannot be taken.
      self.gripper_update_width(width)
      return resp
    finally:
      # And then what the drive says, which is the last word. This one stalls: sent the full sweep
      # from its open end it has locked part way and answered an error, leaving the jaws nowhere
      # the target described - which a model taking only the target would have denied.
      await self._record_where_it_stopped("gripper")

  async def gripper_open(self):
    """Open the jaws all the way. This moves them.

    Opening cannot close on anything, so it is a plain move to the far end of the drive's travel.
    What clears the jaws of whatever they are about to take hold of.
    """
    c = self.configuration
    return await self.gripper_move_to_jaw_position(
      c.gripper_increments_to_mm(c.gripper_range_increments[1])
    )

  async def gripper_close(self):
    """Close the jaws all the way. This moves them.

    Shut, which is below the width the master's own close will be aimed at, so it is a plain move
    too. Closing onto something and holding it is what `gripper_move_to_jaw_position` does at any
    width above that floor.
    """
    c = self.configuration
    return await self.gripper_move_to_jaw_position(
      c.gripper_increments_to_mm(c.gripper_range_increments[0])
    )

  async def _unchecked_fw_gripper_close_with_force_sensed_width_window_increments(
    self,
    grip_strength: int,
    width_increments: int,
    width_tolerance_increments: int,
  ):
    """Close the jaws onto an object, feeling for it. Nothing is guarded and nothing is recorded.

    The lower of the two commands the jaws take and the one that senses: the master closes until
    the force sensor says it has met something, and answers an error when it meets nothing. What
    guards and records is `gripper_close_with_force_sensed_width_window`.

    Both widths are in the tenths of a millimetre the master counts in, which is not what the
    drive counts in: a grip is stated to the master, and a position to the drive.

    Args:
      grip_strength: how hard to hold, 0 the weakest and 9 the strongest.
      width_increments: how wide the thing between the jaws is said to be, in tenths of a
        millimetre.
      width_tolerance_increments: how far off that width the thing may be, in tenths of a
        millimetre. The jaws must start further apart than the width and this together.
    """
    return await self._driver.send_command(
      module="C0",
      command="GC",
      subsystem="R0",
      gw=f"{grip_strength}",
      gb=f"{width_increments:04}",
      gt=f"{width_tolerance_increments:02}",
    )

  async def gripper_close_with_force_sensed_width_window(
    self,
    width: float,
    grip_strength: int = 5,
    width_tolerance: float = 2.0,
  ):
    """Close the jaws onto whatever is between them and hold it. This moves them.

    They stop on what they meet, where `gripper_move_to_jaw_position` drives to a width whatever is
    in the way. So the width is where to look, not where the jaws end up: they end inside the thing,
    and a plate stated at 85.5 mm was held at 80.3 mm. The jaws have to start clear of it.

    Args:
      width: how wide the thing between the jaws is said to be, in mm.
      grip_strength: how hard to hold, 0 the weakest and 9 the strongest.
      width_tolerance: how far off that width the thing may be, in mm. Something met inside that
        window is gripped; a close that runs past it reports finding nothing.

    Raises:
      ValueError: If any of them is outside what the command accepts.
    """
    c = self.configuration
    if not 0 <= grip_strength <= 9:
      raise ValueError(f"grip_strength must be between 0 and 9, is {grip_strength}")
    # The master's own floor: below it the closing ramp would run past the drive's minimum.
    high = c.gripper_increments_to_mm(c.gripper_range_increments[1])
    if not 76.0 < width <= high:
      raise ValueError(f"width must be between 76.0 and {high} mm, is {width}")
    if not 0.5 <= width_tolerance <= 9.9:
      raise ValueError(f"width_tolerance must be between 0.5 and 9.9 mm, is {width_tolerance}")
    # TODO: compute what is actually between the fingers before closing, and refuse a width that
    # does not describe it. Doing that needs a `Resource.contains(point)` that respects rotation
    # - the arm turns, and a corner plus a bounding-box extent is not a rotated box - and a deck
    # query that answers it without sweeping every well and tip. Neither exists yet, and the
    # version removed here was wrong on rotated resources, silently skipped unless the fingers
    # lay within a few degrees of a deck axis, and cost 72 ms per grip on a loaded deck.

    try:
      resp = await self._unchecked_fw_gripper_close_with_force_sensed_width_window_increments(
        grip_strength=grip_strength,
        width_increments=round(width * 10),
        width_tolerance_increments=round(width_tolerance * 10),
      )
      return resp
    finally:
      # Where the jaws stopped is the only word on it: a close has no target / is a probing action,
      # so nothing here knows the width until the drive is read.
      await self._record_where_it_stopped("gripper")

  async def _unchecked_fw_gripper_close_to_object_increments(
    self,
    destination_increments: int,
    stop_band_increments: int,
    stop_trigger: Optional[int] = None,
    speed_increments: Optional[int] = None,
    current_limit: Optional[int] = None,
    low_pass_filter: Optional[bool] = None,
  ):
    """Close the jaws toward a width, stopping on whatever they meet, without checking or
    recording.

    The drive's own version of the master's close, with the two things the master hides under a
    dial: the band around the destination in which meeting something counts, and the force at
    which meeting is declared. The arm still feels, and still answers an error when it reaches the
    end of the band having met nothing.

    It has a destination, which is what makes it safe to point at an unknown object: it stops
    there whatever happens, rather than closing until something stops it.

    Args:
      destination_increments: where the jaws are expected to meet the object, in the drive's steps.
      stop_band_increments: how far either side of that still counts, in the drive's steps.
      stop_trigger: how hard a push counts as meeting something, in the sensor's own counts.
      speed_increments: max gripping velocity, in increments/s.
      current_limit: the motor current limit, 0 to 15.
      low_pass_filter: whether to filter the current signal the trigger is read from.
    """
    c = self.configuration
    if stop_trigger is None:
      stop_trigger = c.gripper_stop_trigger_default
    if speed_increments is None:
      speed_increments = c.gripper_close_speed_default_increments
    if current_limit is None:
      current_limit = c.gripper_current_limit_default
    if low_pass_filter is None:
      low_pass_filter = c.gripper_low_pass_filter_default
    return await self._driver.send_command(
      module="R0",
      command="GB",
      gb=f"{destination_increments:05}",
      gu=f"{speed_increments:04}",
      gd=f"{stop_band_increments:04}",
      gw=f"{current_limit:02}",
      gi=f"{stop_trigger:03}",
      fi=f"{int(low_pass_filter)}",
    )

  async def gripper_probe_for_object(
    self,
    expected_width: float,
    band: Optional[float] = None,
    stop_trigger: Optional[int] = None,
    current_limit: Optional[int] = None,
  ) -> Optional[float]:
    """Close the jaws toward a width and report what they met on the way. This moves them.

    What the master's close cannot do, because its band is fixed at a couple of millimetres: this
    one opens the window as wide as the drive allows, so something several millimetres off the
    width expected is still found rather than reported missing.

    It stops at the width given whether or not it meets anything, which is what keeps it away from
    the drive's own stop - a close with no destination runs the jaws into it and latches the drive.

    The jaws are left where they stopped. What was found is being held, and letting go is the
    caller's decision.

    Args:
      expected_width: roughly how wide the thing between the jaws is, in mm.
      band: how far either side of that to accept, in mm.
      stop_trigger: how hard a push counts as meeting something, in the sensor's own counts.
      current_limit: the motor current limit, 0 to 15.

    Returns:
      How far apart the jaws stopped, in mm, or None when they reached the width given without
      meeting anything within the band.

      A width is where the fingers stopped pushing, not how wide what they met is: this stops on a
      lighter push than a grip does and so stops nearer the object, but it still stops inside it.
      A plate 85.5 mm across was found at 82.1 mm by this and held at 80.3 mm by a grip.

      None is not a promise that the jaws are empty: something far enough outside the band is met
      without being reported, and the arm has answered "plate not found" with its force sensor
      reading twenty times its idle value.

    Raises:
      ValueError: If any argument is outside what the drive accepts.
    """
    c = self.configuration
    if band is None:
      band = c.gripper_stop_band_max
    if stop_trigger is None:
      stop_trigger = c.gripper_stop_trigger_default
    if current_limit is None:
      current_limit = c.gripper_current_limit_default
    low = c.gripper_increments_to_mm(c.gripper_range_increments[0])
    high = c.gripper_increments_to_mm(c.gripper_range_increments[1])
    if not low <= expected_width <= high:
      raise ValueError(f"expected_width must be between {low} and {high} mm, is {expected_width}")
    band_increments = c.gripper_mm_to_increments(band)
    band_low, band_high = c.gripper_stop_band_range_increments
    if not band_low <= band_increments <= band_high:
      raise ValueError(
        f"band must be between {c.gripper_increments_to_mm(band_low)} and "
        f"{c.gripper_increments_to_mm(band_high)} mm, is {band}"
      )
    if not 0 <= stop_trigger <= 999:
      raise ValueError(f"stop_trigger must be between 0 and 999, is {stop_trigger}")
    if not 0 <= current_limit <= 15:
      raise ValueError(f"current_limit must be between 0 and 15, is {current_limit}")

    destination = min(
      max(c.gripper_mm_to_increments(expected_width), c.gripper_range_increments[0]),
      c.gripper_range_increments[1],
    )
    found = True
    try:
      await self._unchecked_fw_gripper_close_to_object_increments(
        destination_increments=destination,
        stop_band_increments=band_increments,
        stop_trigger=stop_trigger,
        current_limit=current_limit,
      )
    except STARFirmwareError as error:
      # An error is one per module, so the arm's own part is what says it met nothing: the master
      # reports a failure alongside it, and the dict is keyed by display name rather than by id.
      met_nothing = any(
        part.raw_module == "R0" and isinstance(part, NoElementError)
        for part in error.errors.values()
      )
      if not met_nothing:
        raise
      found = False
    finally:
      # Where the jaws stopped is the answer, and it can only be read: a probe has no target.
      await self._record_where_it_stopped("gripper")

    return await self.gripper_request_width() if found else None

  async def initialize_gripper_drive(self, current_limit: Optional[int] = None):
    """Bring the gripper drive back to its own reference. This moves the jaws.

    The arm's own initialize brings every drive up and swings the whole arm to do it. This is the
    one drive, which is what a gripper that has lost its reference needs - and what it refuses
    while it is jammed, since it cannot travel to find its sensor edge. `_unchecked_fw_gripper_move_relative_increments`
    is what frees it first.

    Args:
      current_limit: the motor current limit, 0 to 15.

    Raises:
      ValueError: If the current limit is outside what the drive accepts.
    """
    c = self.configuration
    if current_limit is None:
      current_limit = c.gripper_current_limit_default
    if not 0 <= current_limit <= 15:
      raise ValueError(f"current_limit must be between 0 and 15, is {current_limit}")

    try:
      return await self._driver.send_command(module="R0", command="GI", gw=f"{current_limit:02}")
    finally:
      # Finding the sensor edge is a travel, so where the jaws end up is only known by reading.
      await self._record_where_it_stopped("gripper")

  async def _unchecked_fw_gripper_move_relative_increments(
    self,
    distance_increments: int,
    opening: bool,
    speed_increments: Optional[int] = None,
    acceleration_increments: Optional[int] = None,
    current_limit: Optional[int] = None,
  ):
    """Move the jaws a distance, unsupervised, without checking or recording.

    The one movement the drive accepts while it is uninitialized, and the only way out of a jam:
    everything else is refused until the drive has a reference, and the initialize cannot give it
    one while it cannot move. Unsupervised means the drive reports nothing about whether it
    arrived, so the counters have to be read after each attempt - and a nudge that changes nothing
    is a drive still stuck rather than one that had nowhere to go.

    Args:
      distance_increments: how far to travel, in the drive's own steps.
      opening: whether to travel the way that opens the jaws.
      speed_increments: max velocity, in increments/s. Slower than a normal move by default,
        since this is used against something that is stuck.
      acceleration_increments: in thousands of increments/s2.
      current_limit: the motor current limit, 0 to 15.
    """
    c = self.configuration
    if speed_increments is None:
      speed_increments = c.gripper_nudge_speed_default_increments
    if acceleration_increments is None:
      acceleration_increments = c.gripper_acceleration_default_increments
    if current_limit is None:
      current_limit = c.gripper_current_limit_default
    return await self._driver.send_command(
      module="R0",
      command="GS",
      gs=f"{distance_increments:04}",
      gt=f"{0 if opening else 1}",
      gv=f"{speed_increments:04}",
      gr=f"{acceleration_increments:03}",
      gw=f"{current_limit:02}",
    )

  async def _switch_gripper_drive_off(self):
    """Cut the current to the gripper drive, so the jaws can be moved by hand.

    What the arm does to itself on any gripper error, and what a jam is freed by when the drive
    cannot free itself. Whatever is held is released, and the drive keeps no reference through it -
    `initialize_gripper_drive` is what gives it one back.
    """
    try:
      return await self._driver.send_command(module="R0", command="GO")
    finally:
      # Letting go moves the jaws under whatever load is on them.
      await self._record_where_it_stopped("gripper")

  async def recover_gripper_drive(
    self,
    attempts: int = 4,
    nudge: float = 1.1,
    current_limit: Optional[int] = None,
  ) -> bool:
    """Get a stuck gripper drive moving again, and back onto its own reference. This moves it.

    A drive that has run into something it cannot pass stops reporting where it is: its two
    counters part, every ordinary move answers that it is locked, and the initialize that would
    fix the reference cannot run, because it has to travel to find its sensor edge and it cannot
    travel. That is a state the arm cannot leave on its own.

    So this works outwards. It reads the counters, tries the initialize, and when that is refused
    it nudges the jaws open by the one movement an uninitialized drive accepts, checking after each
    nudge whether anything actually moved - unsupervised means the drive answers whether the
    command was taken, not whether it went anywhere. A nudge that moves nothing is met by cutting
    the drive's current and letting it go slack before trying again, which is what frees a drive
    holding itself against its own stop.

    Args:
      attempts: how many times to nudge and retry the initialize.
      nudge: how far to open the jaws on each nudge, in mm.
      current_limit: the motor current limit, 0 to 15.

    Returns:
      True when the drive initialized, False when it is still stuck and needs freeing by hand.

    Raises:
      ValueError: If any argument is outside what the drive accepts.
    """
    c = self.configuration
    if current_limit is None:
      current_limit = c.gripper_current_limit_default
    if attempts < 1:
      raise ValueError(f"attempts must be at least 1, is {attempts}")
    nudge_increments = c.gripper_mm_to_increments(nudge)
    if not 0 < nudge_increments <= 9_999:
      raise ValueError(
        f"nudge must be between {c.gripper_increments_to_mm(1)} and "
        f"{c.gripper_increments_to_mm(9_999)} mm, is {nudge}"
      )
    if not 0 <= current_limit <= 15:
      raise ValueError(f"current_limit must be between 0 and 15, is {current_limit}")

    for attempt in range(attempts):
      firmware, hardware = await self.gripper_request_counters()
      try:
        await self.initialize_gripper_drive(current_limit=current_limit)
      except STARFirmwareError:
        logger.info(
          "the gripper drive will not initialize (counters %d and %d); freeing it, attempt %d of %d",
          firmware,
          hardware,
          attempt + 1,
          attempts,
        )
      else:
        _, hardware = await self.gripper_request_counters()
        logger.info("the gripper drive is back on its reference, reading %d", hardware)
        return True

      before = hardware
      try:
        await self._unchecked_fw_gripper_move_relative_increments(
          distance_increments=nudge_increments, opening=True, current_limit=current_limit
        )
      except STARFirmwareError:
        # Even unsupervised, a drive that cannot turn at all says so. That is not a reason to
        # stop: what comes next is cutting its current, which is the thing that frees it.
        logger.debug("the nudge was refused as well")
      _, after = await self.gripper_request_counters()

      if after == before:
        # It did not move, so it is holding itself somewhere. Letting go is the only thing left
        # to try, and the drive keeps no reference through it - which the initialize above will
        # give back on the next turn of this loop.
        logger.info("the nudge moved nothing, so the drive is being switched off to let it go")
        await self._switch_gripper_drive_off()

    firmware, hardware = await self.gripper_request_counters()
    # Nudges move the jaws without reporting where to, so the last one leaves the model stale.
    await self._record_where_it_stopped("gripper")
    logger.warning(
      "the gripper drive is still stuck after %d attempts, reading %d and %d. Its jaws have to be "
      "freed by hand, and `initialize_gripper_drive` run afterwards",
      attempts,
      firmware,
      hardware,
    )
    return False

  # -- pose ------------------------------------------------------------------

  async def request_joint_state(self) -> JointState:
    """Read every axis, one after another, as the joint state the kinematics run on.

    Each read records what it answered, so this is also what brings the whole model back in step
    with the arm - which is what a move touching more than one drive reads in its `finally`.

    Returns:
      Each axis's position, in that axis's own units.
    """
    return {
      iSWAPAxis.X: await self.rotation_drive_request_x_position(),
      iSWAPAxis.Y: await self.rotation_drive_request_y_position(),
      iSWAPAxis.Z: await self.rotation_drive_request_z_position(),
      iSWAPAxis.ROTATION: await self.rotation_drive_request_angle(),
      iSWAPAxis.WRIST: await self.wrist_drive_request_angle(),
      iSWAPAxis.GRIPPER: await self.gripper_request_width(),
    }

  @staticmethod
  def _forward_kinematics(
    joints: JointState,
    link_1_length: float,
    tool_center_point_distance: float,
    wrist_straight_angle: float,
    rotation_drive_z_offset_above_finger: float,
  ) -> iSWAPPose:
    """Where a joint state puts the gripper. Pure arithmetic: nothing is read.

    One link off the rotation drive, and whatever is bolted to its far end. Link 1 leaves the drive
    at the rotation angle; the tool leaves the wrist at that plus however far the wrist is turned
    from straight. Angles are signed
    counter-clockwise seen from above, and a yaw of 0 points along +x, deck-right.

    Args:
      joints: the joint state, as `request_joint_state` returns it.
      link_1_length: rotation joint to wrist joint, in mm - the arm's own.
      tool_center_point_distance: wrist joint to the point the end-effector is programmed against,
        in mm - the tool's own, which the gripper reports as its `tool_center_point`.
      wrist_straight_angle: what the wrist reports when it is straight, in degrees.
      rotation_drive_z_offset_above_finger: how far the drive's bottom sits above the fingers.

    Returns:
      Every joint of the arm, and the deck angle the gripper faces along.
    """
    link_1_deck_angle = joints[iSWAPAxis.ROTATION] - 90.0
    gripper_deck_angle = link_1_deck_angle + (joints[iSWAPAxis.WRIST] - wrist_straight_angle)

    alpha_1 = math.radians(link_1_deck_angle)
    alpha_2 = math.radians(gripper_deck_angle)

    # Both joints sit at the drive's own height; the fingers hang below its bottom.
    base = Coordinate(x=joints[iSWAPAxis.X], y=joints[iSWAPAxis.Y], z=joints[iSWAPAxis.Z])
    wrist = Coordinate(
      x=base.x + link_1_length * math.cos(alpha_1),
      y=base.y + link_1_length * math.sin(alpha_1),
      z=base.z,
    )
    return iSWAPPose(
      rotation_joint_location=base,
      wrist_joint_location=wrist,
      gripper_center_location=Coordinate(
        x=wrist.x + tool_center_point_distance * math.cos(alpha_2),
        y=wrist.y + tool_center_point_distance * math.sin(alpha_2),
        z=base.z - rotation_drive_z_offset_above_finger,
      ),
      gripper_deck_orientation=Rotation(z=gripper_deck_angle),
      joints=joints,
    )

  async def _unchecked_fw_request_gripper_tcp(self) -> Coordinate:
    """Ask the master where the gripper's tool centre point is. Nothing is guarded.

    The master answers from what it tracks rather than from the drives, and it has only been
    measured right with both joints at predefined stops. Away from them it answers the rotation
    drive's own position, which is wrong by however far the arm reaches - 275 mm with the links
    extended. `request_pose` reads the drives and runs the kinematics, and is what anything
    relying on the answer should call.

    Returns:
      The tool centre point, in mm on the deck, as the master has it.
    """
    resp = await self._driver.send_command(
      module="C0", command="QG", fmt="xs#####xd#yj####yd#zj####zd#"
    )
    return Coordinate(
      x=cast(int, resp["xs"]) / 10 * (1 if resp["xd"] == 0 else -1),
      y=cast(int, resp["yj"]) / 10 * (1 if resp["yd"] == 0 else -1),
      z=cast(int, resp["zj"]) / 10 * (1 if resp["zd"] == 0 else -1),
    )

  async def request_pose(self) -> iSWAPPose:
    """Where the gripper is, worked out from the joint state.

    Read and computed rather than asked for: the master answers a gripper position of its own, but
    only correctly after certain commands have run. This reads each drive and runs the kinematics,
    so it holds whenever it is called.

    Returns:
      Every joint of the arm and where its tool ends up, in one answer: what a caller needs to say
      whether the arm clears something is where its middle joint is as much as where its end is.

    Raises:
      RuntimeError: If the arm's link length or the wrist's stops were not read, or the gripper is
        not modelled.
    """
    c = self.configuration
    gripper = self.gripper
    if c.link_1_length is None:
      raise RuntimeError("the arm's link length was not read; have you called `star.setup()`?")
    if gripper is None:
      raise RuntimeError("the gripper is not modelled, so how far it reaches is unknown")
    if c.wrist_drive_predefined_increments is None:
      raise RuntimeError("the wrist drive's stops were not read; have you called `star.setup()`?")

    return self._forward_kinematics(
      joints=await self.request_joint_state(),
      link_1_length=c.link_1_length,
      tool_center_point_distance=gripper.tool_center_point.x,
      wrist_straight_angle=c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.straight),
      rotation_drive_z_offset_above_finger=c.rotation_drive_z_offset_above_finger,
    )

  # -- parking ---------------------------------------------------------------

  async def request_parked(self, tolerance_increments: int = 2) -> bool:
    """Whether the arm is parked, judged from where its drives are.

    Each drive is checked against the parking stop in its stored table, which setup reads:
    - Y must be on its stop.
    - Z must be at or above its stop, because parking retracts at the traverse height.
    - The elbow must be on its stop.
    - The wrist must be on its stop.
    - The jaws must be at their home position. The gripper's table has no parking stop, and
      parking closes the jaws to home.
    - X is not checked. No table stores an X stop, and parking doesn't move the carriage.

    Args:
      tolerance_increments: how far a drive may be from its stop and still count as parked, in
        that drive's increments.

    Returns:
      True if every checked drive is at its parking position.

    Raises:
      RuntimeError: If a drive's stored table was not read, so its parking stop is unknown.
    """
    c = self.configuration
    joints = await self.request_joint_state()

    if (
      c.rotation_drive_predefined_y_positions_increments is None
      or c.rotation_drive_predefined_z_positions_increments is None
      or c.rotation_drive_predefined_increments is None
      or c.wrist_drive_predefined_increments is None
      or c.gripper_drive_predefined_increments is None
    ):
      raise RuntimeError("the iSWAP position tables were not read; have you called `star.setup()`?")

    def at_stop(
      stop: int,
      to_units: Callable[[int], float],
      position: float,
      at_least: bool = False,
    ) -> bool:
      stop_units = to_units(stop)
      tolerance = abs(to_units(stop + tolerance_increments) - stop_units)
      return (
        stop_units - position <= tolerance if at_least else abs(position - stop_units) <= tolerance
      )

    return all(
      [
        at_stop(
          c.rotation_drive_predefined_y_positions_increments.parking,
          c.y_increments_to_mm,
          joints[iSWAPAxis.Y],
        ),
        at_stop(
          c.rotation_drive_predefined_z_positions_increments.parking,
          c.z_increments_to_mm,
          joints[iSWAPAxis.Z] - c.rotation_drive_z_offset_above_finger,
          at_least=True,
        ),
        at_stop(
          c.rotation_drive_predefined_increments.parking,
          c.rotation_drive_increments_to_angle,
          joints[iSWAPAxis.ROTATION],
        ),
        at_stop(
          c.wrist_drive_predefined_increments.parking,
          c.wrist_increments_to_deg,
          joints[iSWAPAxis.WRIST],
        ),
        at_stop(
          c.gripper_drive_predefined_increments.home,
          c.gripper_increments_to_mm,
          joints[iSWAPAxis.GRIPPER],
        ),
      ]
    )

  async def _unchecked_fw_park(self, traverse_height: Optional[float] = None):
    """Close the gripper and park the arm. Nothing is guarded and nothing is recorded.

    Args:
      traverse_height: how high to lift to before travelling, in mm.
        `default_minimum_traverse_height` when None.

    Raises:
      ValueError: If the traverse height is outside what the command takes.
    """
    if traverse_height is None:
      traverse_height = self.default_minimum_traverse_height
    low, high = self.park_traverse_height_range
    if not low <= traverse_height <= high:
      raise ValueError(f"the arm parks from {low} to {high} mm, not {traverse_height}")
    return await self._driver.send_command(
      module="C0",
      command="PG",
      subsystem="R0",
      th=f"{round(traverse_height * 10):04}",
    )

  async def park(self, traverse_height: Optional[float] = None):
    """Close the gripper and park the arm. This moves it.

    Parking retracts the arm, which is lateral motion, so the arm lifts to `traverse_height` before
    it starts. Sending no height leaves that to the master, which does not raise the arm: an arm
    left out over the deck is driven down into whatever is under it.

    Every axis moves, so every axis is read back afterwards, whether or not the park answered.

    Args:
      traverse_height: how high to lift to before travelling, in mm.
        `default_minimum_traverse_height` when None.
    """
    try:
      return await self._unchecked_fw_park(traverse_height)
    finally:
      # Parking drives every axis, so every axis is read back: each read records what it answered,
      # and one command answering does not say where the others stopped. Its own failure is logged
      # and swallowed, so it cannot replace the exception that says what went wrong.
      try:
        await self.request_joint_state()
      except Exception:
        logger.warning("could not read where the iSWAP parked; its model is stale")

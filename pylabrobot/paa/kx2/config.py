"""Per-arm configuration loaded from the KX2 drives during setup.

The arm-specific calibration (link lengths, travel limits, motor conversion
factors) lives on the drives themselves; the backend reads it once into a
single `KX2Config` record at setup. The backend holds it as
`Optional[KX2Config]` (None pre-setup) — but every field on the record
itself is required, because once setup has run the whole record is filled.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Literal, Optional

from pylabrobot.paa.kx2.driver import JointMoveDirection


GripperFingerSide = Literal["barcode_reader", "proximity_sensor"]


class Axis(IntEnum):
  """KX2 axis -> CANopen node-ID mapping."""

  SHOULDER = 1
  Z = 2
  ELBOW = 3
  WRIST = 4
  RAIL = 5
  SERVO_GRIPPER = 6

  @property
  def is_motion(self) -> bool:
    """The four-axis arm proper. Excludes the rail (Cartesian carrier) and
    the servo gripper (end-effector). Used for setup, halt, freedrive — any
    operation that targets "the arm" without its peripherals."""
    return self in (Axis.SHOULDER, Axis.Z, Axis.ELBOW, Axis.WRIST)

  @property
  def is_linear(self) -> bool:
    """Axis travels in linear units (mm/s, mm/s^2). All others are rotary
    (deg/s, deg/s^2). Used to pick the right speed/acceleration from the
    linear/rotary split in JointMoveParams / CartesianMoveParams."""
    return self in (Axis.Z, Axis.RAIL, Axis.SERVO_GRIPPER)


@dataclass
class AxisConfig:
  """Per-axis params loaded from a single drive during setup.

  Lives in `KX2Config.axes[axis]`. Order of fields here doesn't reflect
  drive register order — they're grouped by what they describe.
  """

  motor_conversion_factor: float
  max_travel: float
  min_travel: float
  unlimited_travel: bool
  absolute_encoder: bool
  max_vel: float
  max_accel: float
  joint_move_direction: JointMoveDirection

  # I/O pin assignments — channel -> human-readable name ("GripperSensor",
  # "Buzzer", "AuxPin42", or "" when unassigned). Probed from UI[5..16] but
  # not currently consumed; kept for introspection.
  digital_inputs: Dict[int, str]
  analog_inputs: Dict[int, str]
  outputs: Dict[int, str]


@dataclass
class ServoGripperConfig:
  """Servo gripper params (axis 6 UF6..UF17). Only present when the
  gripper is detected on the bus."""

  home_pos: int
  home_search_vel: int
  home_search_accel: int
  home_default_position_error: int
  home_hard_stop_position_error: int
  home_hard_stop_offset: int
  home_index_offset: int
  home_offset_vel: int
  home_offset_accel: int
  home_timeout_msec: int
  continuous_current: float
  peak_current: float


@dataclass
class KX2Config:
  # Geometry (read from the shoulder drive's UF8/UF9/UF10).
  wrist_offset: float
  elbow_offset: float
  elbow_zero_offset: float

  # Tooling — supplied at backend construction, copied here so kinematics
  # has a single source of truth.
  gripper_length: float
  gripper_z_offset: float

  # Which finger is treated as the radial "front" by IK/FK. The fingers are
  # physically symmetric, so flipping side just negates the gripper_length
  # offset in the wrist frame. Default matches the legacy convention.
  gripper_finger_side: GripperFingerSide

  # Per-axis params keyed by drive node-id (= Axis value). Iterating
  # `axes` (or `axes.keys()`) gives the axes present on this arm.
  axes: Dict[int, AxisConfig]

  # Robot-level clearance limits (shoulder UF6/UF7).
  base_to_gripper_clearance_z: float
  base_to_gripper_clearance_arm: float

  robot_on_rail: bool

  # None if no servo gripper is present on the bus.
  servo_gripper: Optional[ServoGripperConfig]

  eps: float = 1e-6

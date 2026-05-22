"""Core types, enums, and constants for the Bravo liquid handler.

Ported from HomewoodTypes.h, ProjectIncludes.h, and homewoodstatics.h.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag, auto


# ---------------------------------------------------------------------------
# Axis definitions
# ---------------------------------------------------------------------------


class Axis(IntEnum):
  """Robot motion axes. Values match the C++ HW namespace constants."""

  X = 0  # Horizontal gantry (left-right across deck columns)
  Y = 1  # Horizontal gantry (front-back across deck rows)
  Z = 2  # Pipette head vertical (higher value = lower physical position)
  W = 3  # Pipette plunger / fluid displacement
  G = 4  # Gripper open/close
  Zg = 5  # Gripper vertical

  @property
  def label(self) -> str:
    return f"{self.name}-axis"


NUM_AXES_NO_GRIPPER = 4
NUM_AXES_WITH_GRIPPER = 6
AXIS_NAMES = {a: a.label for a in Axis}


# ---------------------------------------------------------------------------
# Deck layout
# ---------------------------------------------------------------------------

MIN_LOCATION = 1
MAX_ROWS = 3
MAX_COLS = 3
MAX_LOCATIONS = MAX_ROWS * MAX_COLS  # 9
DEFAULT_NUM_LOCATIONS = 9

# Spacing between deck positions (mm)
X_TO_X_DISTANCE = 186.690
Y_TO_Y_DISTANCE = 109.093


def location_to_row_col(location: int) -> tuple[int, int]:
  """Convert 1-based location (1-9) to (row, col) each 0-based."""
  if not (MIN_LOCATION <= location <= MAX_LOCATIONS):
    raise ValueError(f"Location must be {MIN_LOCATION}-{MAX_LOCATIONS}, got {location}")
  idx = location - 1
  return idx // MAX_COLS, idx % MAX_COLS


def row_col_to_location(row: int, col: int) -> int:
  """Convert 0-based (row, col) to 1-based location."""
  return row * MAX_COLS + col + 1


# ---------------------------------------------------------------------------
# Encoder scales (ticks per engineering unit)
# ---------------------------------------------------------------------------

TICKS_PER_MM: dict[Axis, float] = {
  Axis.X: 314.96,
  Axis.Y: 314.96,
  Axis.Z: 1600.0,
  Axis.G: 944.88,
  Axis.Zg: 787.40,
}

# ---------------------------------------------------------------------------
# Axis ranges (mm)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AxisRange:
  min_pos: float
  max_pos: float


AXIS_RANGES: dict[Axis, AxisRange] = {
  Axis.X: AxisRange(0.0, 390.0),
  Axis.Y: AxisRange(0.5, 231.0),
  Axis.Z: AxisRange(-1.8, 150.0),
  Axis.W: AxisRange(0.0, 250.0),  # varies by head, this is max
  Axis.G: AxisRange(-4.0, 10.0),
  Axis.Zg: AxisRange(-20.0, 105.0),
}

# ---------------------------------------------------------------------------
# Safety constants (mm)
# ---------------------------------------------------------------------------

Z_CLEARANCE = 20.0  # Teleshake clearance for pick-and-place
Z_CLEARANCE_NOT_PICKANDPLACE = 3.0
Z_CLEARANCE_NOT_PICKANDPLACE_SRT = 2.0
COLLISION_BUFFER = 20.0
GRIPPER_THICKNESS = 18.7452
GRIPPER_TO_BASE_OF_HEAD_GAP = 0.79
MAX_HEAD_X_SIZE_GRIPPER = 211.0
MAX_HEAD_X_SIZE_NOGRIPPER = 172.0
MAX_HEAD_Y_SIZE_GRIPPER = 107.12
MAX_HEAD_Y_SIZE_NOGRIPPER = 105.11
MIN_PLATE_WIDTH = 77.53
MIN_PLATE_THICKNESS = 3.0

# ---------------------------------------------------------------------------
# Motion constants
# ---------------------------------------------------------------------------

Z_SAFE_POSITION_DEFAULT = 0.0
APPROACH_HEIGHT_DEFAULT = 10.0
MAX_Z_AXIS_CURRENT_PERCENT = 0.67
MAX_G_AXIS_CURRENT_PERCENT = 0.5
GRIP_JOG_TOLERANCE = 5.0
OPEN_GRIPPER_POSITION = 0.0
VACUUM_OPEN_GRIPPER_POSITION = -4.0
VACUUM_CLOSED_GRIPPER_POSITION = -2.0
GRIP_POSITION_TOLERANCE = 4  # encoder counts
TIPBOX_JOG_TOLERANCE = 5.0
HEAD_TYPE_TOLERANCE = 20  # ADC counts

EPSILON = 1e-6
VOLUME_EPSILON = 0.001  # uL
AXIS_EPSILON = 0.07  # mm

# Homing
HOMING_OFFSET_PEAK_CURRENT = 0.03
HOMING_OFFSET_JOG_TOLERANCE = -5.0
HOMING_OFFSET_MAX_JOG_POSITION = -5.0
HOMING_OFFSET_SAFETY_BUFFER = 1.0

# SRT
SRT_250_PAD_HEIGHT = 2.4
HEIGHT_DIFF_96AM_TO_96LT = 4.71

# ---------------------------------------------------------------------------
# Head types
# ---------------------------------------------------------------------------


class HeadType(IntEnum):
  HT_UNKNOWN = -1
  HT_8_D_LT = 0
  HT_8_F_50 = 1
  HT_16_D_ST = 2
  HT_96_D_70 = 3
  HT_96_D_70_S2 = 4
  HT_96_D_200 = 5
  HT_96_D_200_S2 = 6
  HT_96_F_50 = 7
  HT_96_F_200 = 8
  HT_96_PINTOOL = 9
  HT_96_ASSAYMAP = 10
  HT_384_D_70 = 11
  HT_384_D_70_S2 = 12
  HT_384_F_50 = 13
  HT_384_PINTOOL = 14
  HT_1536_PINTOOL = 15

  @property
  def channels(self) -> int:
    _ch = {0: 8, 1: 8, 2: 16}
    if self.value in _ch:
      return _ch[self.value]
    if self.value == 15:
      return 1536
    if self.value >= 11:
      return 384
    return 96

  @property
  def is_disposable(self) -> bool:
    return "_D_" in self.name or self.name.endswith("_D_LT")

  @property
  def is_fixed(self) -> bool:
    return "_F_" in self.name

  @property
  def is_pintool(self) -> bool:
    return "PINTOOL" in self.name

  @property
  def is_assaymap(self) -> bool:
    return "ASSAYMAP" in self.name


# ---------------------------------------------------------------------------
# Speed profiles
# ---------------------------------------------------------------------------


class SpeedLevel(IntEnum):
  FAST = 0
  MED = 1
  SLOW = 2
  HOMING = 3
  SAFE = 4


@dataclass(frozen=True)
class SpeedProfile:
  velocity: float  # mm/s (or uL/s for W)
  acceleration: float  # mm/s^2 (or uL/s^2 for W)


# ---------------------------------------------------------------------------
# Light control
# ---------------------------------------------------------------------------


class LightColor(IntFlag):
  RED = 0x01
  YELLOW = 0x02
  GREEN = 0x04
  BLUE = 0x08


class LightState(IntEnum):
  OFF = 0
  IDLE = auto()
  PROTOCOL = auto()
  ERROR = auto()
  INITIALIZING = auto()


@dataclass
class LightCommand:
  color: LightColor
  period_ms: int = 0  # blink period; 0 = solid
  duty_cycle: float = 1.0  # 0.0-1.0; 1.0 = always on


# ---------------------------------------------------------------------------
# Device state flags (from CMD_QUERY_STATE response)
# ---------------------------------------------------------------------------


class DeviceStateFlag(IntFlag):
  ROBOT_DISABLE = 0x01
  MOTOR_POWER = 0x02
  GO_BUTTON = 0x04
  ROBOT_DISABLE_BUTTON = 0x08


# ---------------------------------------------------------------------------
# Gripper state
# ---------------------------------------------------------------------------


class GripperDetectionState(IntEnum):
  NOT_YET_DETECTED = 0
  DETECTED = 1
  NOT_DETECTED = 2


# ---------------------------------------------------------------------------
# Location types
# ---------------------------------------------------------------------------


class LocationType(IntEnum):
  STANDARD = 0
  ACCESSORY = 2
  SRT250PAD = 3


# ---------------------------------------------------------------------------
# Tip current limits (amps vs tip count, for interpolation)
# ---------------------------------------------------------------------------

LT_TIP_CURRENT_TABLE: list[tuple[int, float]] = [
  (1, 0.04),
  (8, 0.07),
  (12, 0.10),
  (96, 0.60),
]

ST_TIP_CURRENT_TABLE: list[tuple[int, float]] = [
  (1, 0.04),
  (16, 0.10),
  (384, 0.80),
]


def interpolate_tip_current(table: list[tuple[int, float]], tip_count: int) -> float:
  """Linear interpolation of tip-pressing current limit."""
  if tip_count <= table[0][0]:
    return table[0][1]
  if tip_count >= table[-1][0]:
    return table[-1][1]
  for i in range(len(table) - 1):
    n0, c0 = table[i]
    n1, c1 = table[i + 1]
    if n0 <= tip_count <= n1:
      t = (tip_count - n0) / (n1 - n0)
      return c0 + t * (c1 - c0)
  return table[-1][1]


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

NUM_EXTERNAL_ROBOTS = 4
HEAD_RESOURCE_ID = 100  # virtual resource ID for location locking

"""Core value types and physical constants for the Bravo pipetting head and deck.

The Bravo is a fixed-head liquid handler: a single gantry carries a pipetting
head (and, on gripper-equipped units, a plate gripper) over a small deck of
labware locations. This module has no hardware-communication logic of its
own; it defines the vocabulary that the transport, protocol, and motion
layers share — which motion axis is which, which head hardware is installed,
how fast a move should run, and the physical dimensions (deck spacing,
encoder scale, axis travel limits) that come from the instrument's
mechanical design rather than from any single command.

Numeric constants and wire codes here come directly from the firmware and
mechanical drawings; do not adjust them without a source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, IntFlag, auto
from typing import Literal

# ---------------------------------------------------------------------------
# Axis definitions
# ---------------------------------------------------------------------------

Axis = Literal["x", "y", "z", "w", "g", "zg"]
"""A single motion axis.

``x``/``y`` are the horizontal gantry, ``z`` is the pipette head's vertical
travel, ``w`` is the plunger (fluid displacement), and ``g``/``zg`` are the
gripper's jaw and vertical travel. Values are the lowercase firmware axis
letters.
"""

ALL_AXES: tuple[Axis, ...] = ("x", "y", "z", "w", "g", "zg")
"""Every axis, in firmware declaration order."""

_AXIS_CODES: dict[Axis, int] = {
  "x": 0,
  "y": 1,
  "z": 2,
  "w": 3,
  "g": 4,
  "zg": 5,
}

_AXIS_BY_CODE: dict[int, Axis] = {code: axis for axis, code in _AXIS_CODES.items()}

_AXIS_DISPLAY_NAMES: dict[Axis, str] = {
  "x": "X",
  "y": "Y",
  "z": "Z",
  "w": "W",
  "g": "G",
  "zg": "Zg",
}


def axis_code(axis: Axis) -> int:
  """Return the firmware wire code for an axis.

  Args:
    axis: The axis to encode.

  Returns:
    The integer code the firmware uses to identify this axis.
  """
  return _AXIS_CODES[axis]


def axis_display_name(axis: Axis) -> str:
  """Return the short display form of an axis, e.g. ``"Zg"``.

  Args:
    axis: The axis to name.

  Returns:
    The short mixed-case name, or the raw axis string if it is unrecognized.
  """
  return _AXIS_DISPLAY_NAMES.get(axis, axis)


def axis_label(axis: Axis) -> str:
  """Return the human-readable label used in log and error messages.

  Args:
    axis: The axis to label.

  Returns:
    A string such as ``"Zg-axis"``.
  """
  return f"{axis_display_name(axis)}-axis"


# Homing order that keeps the head and gripper clear of the deck.
#
# Homing drives axes to their limits, and on a cold start no position is
# trustworthy yet — the head may be anywhere, possibly down in labware. Moving
# X or Y before the head and gripper are lifted drags them sideways through
# whatever is on the deck, so vertical clearance always comes first:
#
#   z   lifts the pipette head
#   zg  lifts the gripper
#   g   jaws — cannot strike the deck, but belongs with the gripper
#   x,y lateral gantry, only safe once the above are clear
#   w   plunger, internal to the head, no collision path
#
# This is a safety invariant, not a preference. Do not reorder without
# understanding what is physically above the deck at each step.
SAFE_HOME_ORDER: tuple[Axis, ...] = ("z", "zg", "g", "x", "y", "w")


def safe_home_order(axes: Iterable[Axis]) -> list[Axis]:
  """Order axes so vertical clearance happens before any lateral motion.

  Duplicates are dropped. Any axis not in :data:`SAFE_HOME_ORDER` is placed
  last rather than discarded, so an unknown axis is still homed.

  Args:
    axes: The axes to home, in any order.

  Returns:
    The same axes, ordered so homing them in sequence is safe.
  """
  rank = {axis: index for index, axis in enumerate(SAFE_HOME_ORDER)}
  return sorted(dict.fromkeys(axes), key=lambda a: rank.get(a, len(rank)))


NUM_AXES_NO_GRIPPER = 4
NUM_AXES_WITH_GRIPPER = 6
AXIS_NAMES: dict[Axis, str] = {axis: axis_label(axis) for axis in ALL_AXES}
"""Human-readable label for every axis, e.g. ``{"zg": "Zg-axis"}``."""


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
  """Convert a 1-based deck location number to a 0-based (row, col) pair.

  Args:
    location: The deck location, numbered 1 through :data:`MAX_LOCATIONS`.

  Returns:
    The location's zero-based (row, col) position in the 3x3 deck grid.

  Raises:
    ValueError: If ``location`` is outside the valid range.
  """
  if not (MIN_LOCATION <= location <= MAX_LOCATIONS):
    raise ValueError(f"Location must be {MIN_LOCATION}-{MAX_LOCATIONS}, got {location}")
  idx = location - 1
  return idx // MAX_COLS, idx % MAX_COLS


def row_col_to_location(row: int, col: int) -> int:
  """Convert a 0-based (row, col) deck position to a 1-based location number.

  Args:
    row: Zero-based row in the deck grid.
    col: Zero-based column in the deck grid.

  Returns:
    The 1-based deck location number.
  """
  return row * MAX_COLS + col + 1


# ---------------------------------------------------------------------------
# Encoder scales (ticks per engineering unit)
# ---------------------------------------------------------------------------

TICKS_PER_MM: dict[Axis, float] = {
  "x": 314.96,
  "y": 314.96,
  "z": 1600.0,
  "g": 944.88,
  "zg": 787.40,
}
"""Encoder ticks per millimetre for each linear axis. ``w`` has no fixed
mm scale — its units depend on the installed head — so it is omitted."""

DEFAULT_W_TICKS_PER_UL = 48.0
"""Encoder ticks per microlitre for the W (plunger) axis, before head detection.

The W axis's real scale depends on the installed head's syringe volume and
is set from head-specific data once a head is detected. This is the value
used until then, and the value a controller falls back to if no
head-specific scale is ever supplied.
"""

# ---------------------------------------------------------------------------
# Axis ranges (mm)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AxisRange:
  """The travel limits of one motion axis, in millimetres."""

  min_pos: float
  max_pos: float


AXIS_RANGES: dict[Axis, AxisRange] = {
  "x": AxisRange(0.0, 390.0),
  "y": AxisRange(0.5, 231.0),
  "z": AxisRange(-1.8, 150.0),
  "w": AxisRange(0.0, 250.0),  # varies by head, this is max
  "g": AxisRange(-4.0, 10.0),
  "zg": AxisRange(-20.0, 105.0),
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

HeadType = Literal[
  "unknown",
  "8_d_lt",
  "8_f_50",
  "16_d_st",
  "96_d_70",
  "96_d_70_s2",
  "96_d_200",
  "96_d_200_s2",
  "96_f_50",
  "96_f_200",
  "96_pintool",
  "96_assaymap",
  "384_d_70",
  "384_d_70_s2",
  "384_f_50",
  "384_pintool",
  "1536_pintool",
]
"""The pipetting head hardware installed on the gantry.

Disposable-tip heads carry ``_d_`` (e.g. ``"96_d_70"``, a 96-channel head for
70 uL disposable tips); fixed-tip heads carry ``_f_``; pintool and AssayMAP
heads are named directly. ``"unknown"`` means no head has been identified
yet.
"""

ALL_HEAD_TYPES: tuple[HeadType, ...] = (
  "unknown",
  "8_d_lt",
  "8_f_50",
  "16_d_st",
  "96_d_70",
  "96_d_70_s2",
  "96_d_200",
  "96_d_200_s2",
  "96_f_50",
  "96_f_200",
  "96_pintool",
  "96_assaymap",
  "384_d_70",
  "384_d_70_s2",
  "384_f_50",
  "384_pintool",
  "1536_pintool",
)
"""Every head type, in firmware declaration order."""

_HEAD_TYPE_CODES: dict[HeadType, int] = {
  "unknown": -1,
  "8_d_lt": 0,
  "8_f_50": 1,
  "16_d_st": 2,
  "96_d_70": 3,
  "96_d_70_s2": 4,
  "96_d_200": 5,
  "96_d_200_s2": 6,
  "96_f_50": 7,
  "96_f_200": 8,
  "96_pintool": 9,
  "96_assaymap": 10,
  "384_d_70": 11,
  "384_d_70_s2": 12,
  "384_f_50": 13,
  "384_pintool": 14,
  "1536_pintool": 15,
}

_HEAD_TYPE_CHANNELS: dict[HeadType, int] = {
  "unknown": 96,  # permissive default before head detection; not a firmware-declared value.
  "8_d_lt": 8,
  "8_f_50": 8,
  "16_d_st": 16,
  "96_d_70": 96,
  "96_d_70_s2": 96,
  "96_d_200": 96,
  "96_d_200_s2": 96,
  "96_f_50": 96,
  "96_f_200": 96,
  "96_pintool": 96,
  "96_assaymap": 96,
  "384_d_70": 384,
  "384_d_70_s2": 384,
  "384_f_50": 384,
  "384_pintool": 384,
  "1536_pintool": 1536,
}

TipKind = Literal["disposable", "fixed", "pintool", "assaymap", "none"]
"""The kind of tip (or tip-like tool) a head type carries."""

_HEAD_TYPE_TIP_KIND: dict[HeadType, TipKind] = {
  "unknown": "none",
  "8_d_lt": "disposable",
  "8_f_50": "fixed",
  "16_d_st": "disposable",
  "96_d_70": "disposable",
  "96_d_70_s2": "disposable",
  "96_d_200": "disposable",
  "96_d_200_s2": "disposable",
  "96_f_50": "fixed",
  "96_f_200": "fixed",
  "96_pintool": "pintool",
  "96_assaymap": "assaymap",
  "384_d_70": "disposable",
  "384_d_70_s2": "disposable",
  "384_f_50": "fixed",
  "384_pintool": "pintool",
  "1536_pintool": "pintool",
}


def head_type_code(head_type: HeadType) -> int:
  """Return the firmware wire code for a head type.

  Args:
    head_type: The head type to encode.

  Returns:
    The integer code the firmware uses to identify this head, or ``-1`` for
    ``"unknown"``.
  """
  return _HEAD_TYPE_CODES[head_type]


def head_type_channels(head_type: HeadType) -> int:
  """Return the number of pipetting channels a head type has.

  ``"unknown"`` returns 96, a permissive default so geometry lookups do not
  raise before a head has been identified. This module does not reject
  ``"unknown"``; a caller that needs to guarantee a real head is installed
  before pipetting should check for it at the operation boundary.

  Args:
    head_type: The head type to look up.

  Returns:
    The channel count (8, 16, 96, 384, or 1536).
  """
  return _HEAD_TYPE_CHANNELS[head_type]


def head_type_tip_kind(head_type: HeadType) -> TipKind:
  """Return the kind of tip (or tip-like tool) a head type carries.

  Args:
    head_type: The head type to check.

  Returns:
    ``"disposable"``, ``"fixed"``, ``"pintool"``, ``"assaymap"``, or
    ``"none"`` for ``"unknown"``.
  """
  return _HEAD_TYPE_TIP_KIND[head_type]


def head_type_is_disposable(head_type: HeadType) -> bool:
  """Return whether a head type uses disposable tips.

  Args:
    head_type: The head type to check.

  Returns:
    True if the head is a disposable-tip head.
  """
  return _HEAD_TYPE_TIP_KIND[head_type] == "disposable"


def head_type_is_fixed(head_type: HeadType) -> bool:
  """Return whether a head type has fixed (non-disposable) tips.

  Args:
    head_type: The head type to check.

  Returns:
    True if the head is a fixed-tip head.
  """
  return _HEAD_TYPE_TIP_KIND[head_type] == "fixed"


def head_type_is_pintool(head_type: HeadType) -> bool:
  """Return whether a head type is a pintool head.

  Args:
    head_type: The head type to check.

  Returns:
    True if the head is a pintool head.
  """
  return _HEAD_TYPE_TIP_KIND[head_type] == "pintool"


def head_type_is_assaymap(head_type: HeadType) -> bool:
  """Return whether a head type is an AssayMAP head.

  Args:
    head_type: The head type to check.

  Returns:
    True if the head is an AssayMAP head.
  """
  return _HEAD_TYPE_TIP_KIND[head_type] == "assaymap"


# ---------------------------------------------------------------------------
# Speed profiles
# ---------------------------------------------------------------------------

SpeedLevel = Literal["fast", "med", "slow", "homing", "safe"]
"""A named motion speed profile.

Callers select a move speed by name; each level maps to a velocity and
acceleration pair that varies by axis and installed head (see
``DEFAULT_SPEEDS`` in the motion layer).
"""

ALL_SPEED_LEVELS: tuple[SpeedLevel, ...] = ("fast", "med", "slow", "homing", "safe")
"""Every speed level, in firmware declaration order."""

_SPEED_LEVEL_CODES: dict[SpeedLevel, int] = {
  "fast": 0,
  "med": 1,
  "slow": 2,
  "homing": 3,
  "safe": 4,
}


def speed_level_code(speed: SpeedLevel) -> int:
  """Return the firmware wire code for a speed level.

  Args:
    speed: The speed level to encode.

  Returns:
    The integer code the firmware uses to identify this speed level.
  """
  return _SPEED_LEVEL_CODES[speed]


@dataclass(frozen=True)
class SpeedProfile:
  """A velocity/acceleration pair for one axis at one speed level."""

  velocity: float  # mm/s (or uL/s for w)
  acceleration: float  # mm/s^2 (or uL/s^2 for w)


# ---------------------------------------------------------------------------
# Light control
# ---------------------------------------------------------------------------


class LightColor(IntFlag):
  """The indicator light's color channels, combinable with ``|``."""

  RED = 0x01
  YELLOW = 0x02
  GREEN = 0x04
  BLUE = 0x08


class LightState(IntEnum):
  """The driver's high-level indicator light state."""

  OFF = 0
  IDLE = auto()
  PROTOCOL = auto()
  ERROR = auto()
  INITIALIZING = auto()


@dataclass
class LightCommand:
  """A command to the indicator light: a color, blink period, and duty cycle.

  Attributes:
    color: The color channel(s) to light, combinable with ``|``.
    period: Blink period, in seconds. ``0`` means solid (no blinking).
    duty_cycle: Fraction of each period the light is on, from 0.0 to 1.0;
      ``1.0`` means always on.

  Note:
    The wire protocol encodes the period as a 32-bit count of milliseconds,
    not seconds. Use :func:`light_command_period_ms` to convert before
    sending a command, rather than truncating ``period`` directly —
    ``int(period)`` silently rounds any sub-second period to 0, which the
    firmware reads as solid instead of blinking.
  """

  color: LightColor
  period: float = 0.0
  duty_cycle: float = 1.0


def light_command_period_ms(command: LightCommand) -> int:
  """Return a light command's blink period in the wire protocol's units.

  Args:
    command: The light command to convert.

  Returns:
    The blink period in whole milliseconds, rounded to the nearest
    millisecond.
  """
  return round(command.period * 1000)


# ---------------------------------------------------------------------------
# Device state flags (from CMD_QUERY_STATE response)
# ---------------------------------------------------------------------------


class DeviceStateFlag(IntFlag):
  """Bit flags reported by the device in its ``CMD_QUERY_STATE`` response."""

  ROBOT_DISABLE = 0x01
  MOTOR_POWER = 0x02
  GO_BUTTON = 0x04
  ROBOT_DISABLE_BUTTON = 0x08


# ---------------------------------------------------------------------------
# Gripper state
# ---------------------------------------------------------------------------


class GripperDetectionState(IntEnum):
  """Whether the gripper accessory has been detected on the gantry."""

  NOT_YET_DETECTED = 0
  DETECTED = 1
  NOT_DETECTED = 2


# ---------------------------------------------------------------------------
# Location types
# ---------------------------------------------------------------------------


class LocationType(IntEnum):
  """The kind of fixture occupying a deck location."""

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
  """Linearly interpolate the tip-pressing current limit for a tip count.

  Args:
    table: Sorted ``(tip_count, current_amps)`` breakpoints.
    tip_count: The number of tips being pressed.

  Returns:
    The interpolated current limit in amps, clamped to the table's range.
  """
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

"""Per-axis motion configuration and default speed profiles.

Each motion axis needs a handful of values beyond the physical constants in
:mod:`.types` before a controller can drive it: the encoder scale to use for
tick conversion, the homing direction and register layout the firmware
expects, and a velocity/acceleration pair for each named
:data:`~.types.SpeedLevel`. :class:`AxisConfig` collects all of that into one
typed record per axis, and :func:`default_axis_config` builds one populated
entirely from the constants this module already knows, so a controller
constructed without any caller-supplied configuration still has complete
values for every axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import (
  AXIS_RANGES,
  DEFAULT_W_TICKS_PER_UL,
  TICKS_PER_MM,
  Axis,
  AxisRange,
  SpeedLevel,
  SpeedProfile,
)

_TICKS_PER_ENG_UNIT: dict[Axis, float] = {**TICKS_PER_MM, "w": DEFAULT_W_TICKS_PER_UL}
"""Encoder ticks per engineering unit for every axis, including W."""


@dataclass
class AxisConfig:
  """Complete motion configuration for one axis.

  Attributes:
    axis: The axis this configuration applies to.
    ticks_per_eng_unit: Encoder ticks per engineering unit (mm, or uL for
      the W axis) for this axis.
    range: The axis's travel limits, in engineering units.
    homing_offset: The engineering-unit position the axis reports once
      homing completes, i.e. its park position.
    home_in_positive_direction: Whether the home sensor sits at the
      positive end of the axis's travel. Homing departs from the sensor in
      the opposite direction from this flag.
    home_flag_bitmask: The bit in the firmware's home-sensor status byte
      that reports this axis's sensor state.
    home_flag_register: The Agile register address that reports whether
      homing is in progress for this axis.
    home_complete_register: The Agile register whose value confirms this
      axis's home flag once homing has completed.
    homing_soft_stop_decel: Deceleration used to bring the axis to a
      controlled stop at the end of a homing search phase.
    min_move_full_accel: The shortest move distance, in engineering units,
      for which the axis reaches its full commanded acceleration before
      needing to decelerate again.
    check_for_alignment: Whether moves on this axis should be rejected
      unless the axis has already been homed.
    speeds: Velocity/acceleration pairs for this axis, keyed by speed
      level.
  """

  axis: Axis
  ticks_per_eng_unit: float
  range: AxisRange
  homing_offset: float = 0.0
  home_in_positive_direction: bool = False
  home_flag_bitmask: int = 0
  home_flag_register: int = 0
  home_complete_register: int = 0
  homing_soft_stop_decel: float = 300.0
  min_move_full_accel: float = 0.0
  check_for_alignment: bool = True
  speeds: dict[SpeedLevel, SpeedProfile] = field(default_factory=dict)


DEFAULT_SPEEDS: dict[Axis, dict[SpeedLevel, SpeedProfile]] = {
  "x": {
    "fast": SpeedProfile(400.0, 2000.0),
    "med": SpeedProfile(200.0, 1000.0),
    "slow": SpeedProfile(50.0, 500.0),
    "homing": SpeedProfile(50.0, 500.0),
    "safe": SpeedProfile(100.0, 500.0),
  },
  "y": {
    "fast": SpeedProfile(400.0, 2000.0),
    "med": SpeedProfile(200.0, 1000.0),
    "slow": SpeedProfile(50.0, 500.0),
    "homing": SpeedProfile(50.0, 500.0),
    "safe": SpeedProfile(100.0, 500.0),
  },
  "z": {
    "fast": SpeedProfile(150.0, 1500.0),
    "med": SpeedProfile(75.0, 750.0),
    "slow": SpeedProfile(25.0, 250.0),
    "homing": SpeedProfile(25.0, 250.0),
    "safe": SpeedProfile(50.0, 500.0),
  },
  "w": {
    "fast": SpeedProfile(250.0, 2500.0),
    "med": SpeedProfile(125.0, 1250.0),
    "slow": SpeedProfile(25.0, 250.0),
    "homing": SpeedProfile(25.0, 250.0),
    "safe": SpeedProfile(50.0, 500.0),
  },
  "g": {
    "fast": SpeedProfile(50.0, 500.0),
    "med": SpeedProfile(25.0, 250.0),
    "slow": SpeedProfile(10.0, 100.0),
    "homing": SpeedProfile(10.0, 100.0),
    "safe": SpeedProfile(10.0, 100.0),
  },
  "zg": {
    "fast": SpeedProfile(150.0, 1500.0),
    "med": SpeedProfile(75.0, 750.0),
    "slow": SpeedProfile(25.0, 250.0),
    "homing": SpeedProfile(25.0, 250.0),
    "safe": SpeedProfile(50.0, 500.0),
  },
}
"""Velocity/acceleration pairs for every axis and speed level."""


def default_axis_config(axis: Axis) -> AxisConfig:
  """Build the default configuration for an axis.

  Populates :attr:`AxisConfig.ticks_per_eng_unit`, :attr:`AxisConfig.range`,
  and :attr:`AxisConfig.speeds` from :data:`~.types.TICKS_PER_MM`,
  :data:`~.types.AXIS_RANGES`, and :data:`DEFAULT_SPEEDS`; every other field
  keeps its dataclass default. The W axis has no fixed mm scale in
  :data:`~.types.TICKS_PER_MM` (its ticks-per-uL ratio depends on the
  installed head), so its encoder scale here is
  :data:`~.types.DEFAULT_W_TICKS_PER_UL` -- the same head-independent
  default every controller in this package seeds itself with -- rather than
  an arbitrary placeholder.

  Args:
    axis: The axis to build a default configuration for.

  Returns:
    A complete, typed configuration for ``axis``.
  """
  return AxisConfig(
    axis=axis,
    ticks_per_eng_unit=_TICKS_PER_ENG_UNIT[axis],
    range=AXIS_RANGES[axis],
    speeds=DEFAULT_SPEEDS.get(axis, {}),
  )

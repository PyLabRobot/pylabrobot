"""Axis configuration and speed profiles.

Defines per-axis configuration (encoder scale, range, homing, speeds) and
default speed profiles ported from HomewoodProfile.h.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis, SpeedLevel, SpeedProfile, AxisRange, AXIS_RANGES, TICKS_PER_MM


@dataclass
class AxisConfig:
    """Complete configuration for one axis (matches C++ CAgileAxis / profile registry)."""
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
    Axis.X: {
        SpeedLevel.FAST: SpeedProfile(400.0, 2000.0),
        SpeedLevel.MED: SpeedProfile(200.0, 1000.0),
        SpeedLevel.SLOW: SpeedProfile(50.0, 500.0),
        SpeedLevel.HOMING: SpeedProfile(50.0, 500.0),
        SpeedLevel.SAFE: SpeedProfile(100.0, 500.0),
    },
    Axis.Y: {
        SpeedLevel.FAST: SpeedProfile(400.0, 2000.0),
        SpeedLevel.MED: SpeedProfile(200.0, 1000.0),
        SpeedLevel.SLOW: SpeedProfile(50.0, 500.0),
        SpeedLevel.HOMING: SpeedProfile(50.0, 500.0),
        SpeedLevel.SAFE: SpeedProfile(100.0, 500.0),
    },
    Axis.Z: {
        SpeedLevel.FAST: SpeedProfile(150.0, 1500.0),
        SpeedLevel.MED: SpeedProfile(75.0, 750.0),
        SpeedLevel.SLOW: SpeedProfile(25.0, 250.0),
        SpeedLevel.HOMING: SpeedProfile(25.0, 250.0),
        SpeedLevel.SAFE: SpeedProfile(50.0, 500.0),
    },
    Axis.W: {
        SpeedLevel.FAST: SpeedProfile(250.0, 2500.0),
        SpeedLevel.MED: SpeedProfile(125.0, 1250.0),
        SpeedLevel.SLOW: SpeedProfile(25.0, 250.0),
        SpeedLevel.HOMING: SpeedProfile(25.0, 250.0),
        SpeedLevel.SAFE: SpeedProfile(50.0, 500.0),
    },
    Axis.G: {
        SpeedLevel.FAST: SpeedProfile(50.0, 500.0),
        SpeedLevel.MED: SpeedProfile(25.0, 250.0),
        SpeedLevel.SLOW: SpeedProfile(10.0, 100.0),
        SpeedLevel.HOMING: SpeedProfile(10.0, 100.0),
        SpeedLevel.SAFE: SpeedProfile(10.0, 100.0),
    },
    Axis.Zg: {
        SpeedLevel.FAST: SpeedProfile(150.0, 1500.0),
        SpeedLevel.MED: SpeedProfile(75.0, 750.0),
        SpeedLevel.SLOW: SpeedProfile(25.0, 250.0),
        SpeedLevel.HOMING: SpeedProfile(25.0, 250.0),
        SpeedLevel.SAFE: SpeedProfile(50.0, 500.0),
    },
}


def get_default_axis_config(axis: Axis) -> AxisConfig:
    """Get the default configuration for an axis."""
    return AxisConfig(
        axis=axis,
        ticks_per_eng_unit=TICKS_PER_MM.get(axis, 1.0),
        range=AXIS_RANGES[axis],
        speeds=DEFAULT_SPEEDS.get(axis, {}),
    )

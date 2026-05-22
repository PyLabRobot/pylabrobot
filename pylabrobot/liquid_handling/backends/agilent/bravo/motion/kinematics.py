"""Unit conversions and coupled Z+W motion.

Provides helpers to convert between engineering units (mm, mm/s, mm/s^2) and
motor ticks, plus coupled Z/W motion for dynamic tip retraction.
"""

from __future__ import annotations


def mm_to_ticks(mm: float, ticks_per_mm: float) -> float:
    """Convert a position in millimetres to encoder ticks."""
    return mm * ticks_per_mm


def ticks_to_mm(ticks: float, ticks_per_mm: float) -> float:
    """Convert encoder ticks to millimetres."""
    return ticks / ticks_per_mm


def velocity_mm_to_ticks(mm_per_s: float, ticks_per_mm: float) -> float:
    """Convert velocity from mm/s to ticks/ms.

    The motor controller expects velocity in ticks per millisecond, so the
    result is ``mm_per_s * ticks_per_mm / 1000``.
    """
    return mm_per_s * ticks_per_mm / 1000.0


def accel_mm_to_ticks(mm_per_s2: float, ticks_per_mm: float) -> float:
    """Convert acceleration from mm/s^2 to ticks/ms^2.

    The motor controller expects acceleration in ticks per millisecond squared,
    so the result is ``mm_per_s2 * ticks_per_mm / 1_000_000``.
    """
    return mm_per_s2 * ticks_per_mm / 1_000_000.0


def couple_zw_motion(
    z_velocity: float,
    z_acceleration: float,
    w_velocity: float,
    coupling_variable: float,
) -> tuple[float, float]:
    """Scale Z velocity and acceleration for dynamic tip retraction.

    When the Z axis moves down the W axis must retract proportionally so
    the liquid level stays constant.  *coupling_variable* is the ratio of
    W displacement per unit Z displacement.

    Returns:
        (coupled_w_velocity, coupled_w_acceleration) scaled from the Z
        motion parameters and clamped so W never exceeds *w_velocity*.
    """
    coupled_w_velocity = min(abs(z_velocity * coupling_variable), abs(w_velocity))
    coupled_w_acceleration = abs(z_acceleration * coupling_variable)
    return coupled_w_velocity, coupled_w_acceleration

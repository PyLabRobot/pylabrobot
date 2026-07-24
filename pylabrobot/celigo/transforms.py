"""Coordinate transforms for the Celigo.

The instrument chains 2D frames:

    image pixels -> sample(plate) mm -> stage mm -> encoder ticks      (stage path)
    galvo mm -> galvo volts -> DAC counts                              (galvo FOV path)

This module implements the config-driven, hardware-facing transforms used by the
device:

* **encoder ticks <-> stage mm** (per axis), and
* **galvo mm <-> galvo volts <-> DAC** (per-optical-filter 2D cubic polynomial).

The pixel<->mm and sample<->stage affine frames (rotation + scale + shear) are provided
by :mod:`pylabrobot.celigo.coordinates` from ``CalibrationConfig.xml``.

The vendor implementation's ``ApplyScale`` path uses ``Forward`` for galvo volts ->
millimetres, while ``RemoveScale`` uses ``Reverse`` for millimetres -> galvo volts.
"""

from __future__ import annotations

from typing import Dict, Tuple

from pylabrobot.celigo.config import AxisConfig, Calibrated2DPolynomialTransform

# 16-bit galvo DAC: clamp to +/-10 V, map about the midpoint at 3276.75 counts/V.
_GALVO_DAC_ZERO_VOLTS = 32767.5
_GALVO_DAC_PER_VOLT = 3276.75


def volts_to_dac_units(volts: float) -> int:
  """16-bit galvo DAC: clamp to +/-10 V then map about the midpoint."""
  clamped = max(-10.0, min(volts, 10.0))
  return int(min(65535.0, max(0.0, round(clamped * _GALVO_DAC_PER_VOLT + _GALVO_DAC_ZERO_VOLTS))))


def dac_units_to_volts(dac: int) -> float:
  """Inverse of :func:`volts_to_dac_units`."""
  return (dac - _GALVO_DAC_ZERO_VOLTS) / _GALVO_DAC_PER_VOLT


# term name -> exponents (px, py) of the monomial vx**px * vy**py
_CUBIC_MONOMIALS: Dict[str, Tuple[int, int]] = {
  "OffsetTerm": (0, 0),
  "LinearXTerm": (1, 0),
  "LinearYTerm": (0, 1),
  "QuadraticXTerm": (2, 0),
  "CrossTerm": (1, 1),
  "QuadraticYTerm": (0, 2),
  "CubicXTerm": (3, 0),
  "CubicYTerm": (0, 3),
  "QuadraticXLinearYTerm": (2, 1),
  "QuadraticYLinearXTerm": (1, 2),
}


def evaluate_polynomial_2d(
  terms: Dict[str, "Tuple[float, float]"], vx: float, vy: float
) -> Tuple[float, float]:
  """Evaluate a 2D quadratic/cubic polynomial transform at ``(vx, vy)``.

  ``terms`` maps each named coefficient (see :data:`_CUBIC_MONOMIALS`) to its
  ``(x_output, y_output)`` contribution. Returns ``(out_x, out_y)``.
  """
  out_x = 0.0
  out_y = 0.0
  for name, (cx, cy) in terms.items():
    exponents = _CUBIC_MONOMIALS.get(name)
    if exponents is None:
      continue
    px, py = exponents
    monomial = (vx**px) * (vy**py)
    out_x += cx * monomial
    out_y += cy * monomial
  return out_x, out_y


evaluate_cubic_2d = evaluate_polynomial_2d


def galvo_volts_to_mm(
  transform: Calibrated2DPolynomialTransform, x_volts: float, y_volts: float
) -> Tuple[float, float]:
  """Galvo volts -> deflection mm (``Forward`` polynomial)."""
  return evaluate_polynomial_2d(transform.forward, x_volts, y_volts)


def galvo_mm_to_volts(
  transform: Calibrated2DPolynomialTransform, x_mm: float, y_mm: float
) -> Tuple[float, float]:
  """Galvo deflection mm -> volts (``Reverse`` polynomial)."""
  return evaluate_polynomial_2d(transform.reverse, x_mm, y_mm)


def galvo_mm_to_dac(
  transform: Calibrated2DPolynomialTransform, x_mm: float, y_mm: float
) -> Tuple[int, int]:
  """Galvo deflection mm -> (x_dac, y_dac) 16-bit counts ready for ``MOVE_GALVOS``."""
  x_volts, y_volts = galvo_mm_to_volts(transform, x_mm, y_mm)
  return volts_to_dac_units(x_volts), volts_to_dac_units(y_volts)


def galvo_dac_to_mm(
  transform: Calibrated2DPolynomialTransform, x_dac: int, y_dac: int
) -> Tuple[float, float]:
  """Inverse of :func:`galvo_mm_to_dac`."""
  return galvo_volts_to_mm(transform, dac_units_to_volts(x_dac), dac_units_to_volts(y_dac))


def mm_to_encoder_ticks(mm: float, axis: AxisConfig) -> int:
  """Stage mm -> encoder ticks for an axis.

  ``ticks = round((mm * sign + home_offset) / mm_per_encoder_tick)`` where ``sign`` is
  -1 when the axis direction is inverted.
  """
  if axis.mm_per_encoder_tick == 0:
    raise ValueError(f"Axis {axis.motion_name!r} has mm_per_encoder_tick == 0")
  sign = -1.0 if axis.invert_axis_direction else 1.0
  return round((mm * sign + axis.home_offset) / axis.mm_per_encoder_tick)


def encoder_ticks_to_mm(ticks: int, axis: AxisConfig) -> float:
  """Encoder ticks -> stage mm for an axis."""
  sign = -1.0 if axis.invert_axis_direction else 1.0
  return (ticks * axis.mm_per_encoder_tick - axis.home_offset) * sign


def mm_per_sec_to_ticks_per_sec(mm_per_sec: float, axis: AxisConfig) -> int:
  """Velocity conversion (no offset/inversion), for EZStepper velocity commands."""
  if axis.mm_per_encoder_tick == 0:
    raise ValueError(f"Axis {axis.motion_name!r} has mm_per_encoder_tick == 0")
  return round(mm_per_sec / axis.mm_per_encoder_tick)

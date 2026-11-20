"""Where PyLabRobot's units meet Biomek's: the one place they are converted.

The public API takes and returns PyLabRobot's units: mm, mm/s, uL, uL/s, s, deg, in the deck's
frame (Y towards the back). Biomek works in its own, and so does the driver's planning (the
plans, the recipes): cm, its frame (X to the right, Y
towards the front), and a speed field, per mille of each axis's maximum velocity (1..1000). A
public method converts its arguments here once and hands them to its Biomek-unit counterpart;
nothing inside converts back and forth, so a run is the same request for request.

Imports nothing of the package's, so every layer can use it.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod

MM_PER_CM = 10.0

# The deck's frame against Biomek's, chosen rather than measured: its left edge 10 cm left of
# Biomek's X origin, clear of the left trash bin, and its front edge 70 cm in front of Biomek's Y
# origin, clear of the front row of ALPs. x = X + FRAME_X, y = FRAME_Y - Y (mm).
FRAME_X = 10.0 * MM_PER_CM
FRAME_Y = 70.0 * MM_PER_CM

FULL_SPEED = 1000
"""The speed field's top: the axis's maximum velocity."""


# -- lengths ---------------------------------------------------------------------------------------


def cm(value: float) -> float:
  """A length Biomek states in centimetres, in millimetres.

  Every dimension transcribed from a Biomek definition goes through here, written as Biomek states
  it, so the literal in the code can be compared against its source as it stands.
  """
  return value * MM_PER_CM


def to_cm(mm: float) -> float:
  """A length in mm, in Biomek's cm."""
  return mm / MM_PER_CM


def optional_cm(mm: Optional[float]) -> Optional[float]:
  """`to_cm`, passing None through (an argument left to its default)."""
  return None if mm is None else to_cm(mm)


def to_mm(cm_value: float) -> float:
  """A length in Biomek's cm, in mm."""
  return cm_value * MM_PER_CM


# -- the frame -------------------------------------------------------------------------------------


def biomek_x(x: float) -> float:
  """A deck x, in mm, as Biomek's X in mm."""
  return x - FRAME_X


def biomek_y(y: float) -> float:
  """A deck y, in mm, as Biomek's Y in mm (the flip is its own inverse: `deck_y`)."""
  return FRAME_Y - y


def deck_x(x: float) -> float:
  """Biomek's X, in mm, as a deck x in mm."""
  return x + FRAME_X


deck_y = biomek_y
"""Biomek's Y, in mm, as a deck y in mm."""


@dataclass(frozen=True)
class BiomekPoint:
  """A point in Biomek's frame, cm: X to the right, Y towards the front, Z up from the deck."""

  x: float
  y: float
  z: float


def to_biomek(point: Coordinate) -> BiomekPoint:
  """A deck coordinate (mm, PyLabRobot's frame) in Biomek's frame (cm)."""
  return BiomekPoint(
    biomek_x(point.x) / MM_PER_CM, biomek_y(point.y) / MM_PER_CM, point.z / MM_PER_CM
  )


def offset_to_biomek(x: float, y: float, offset: Coordinate) -> Tuple[float, float]:
  """Biomek X/Y (cm) moved by a PyLabRobot `offset` (mm): Biomek's Y runs to the front."""
  return x + offset.x / MM_PER_CM, y - offset.y / MM_PER_CM


# -- axis values -----------------------------------------------------------------------------------


def _is_y(axis: str) -> bool:
  return axis == "GY" or (axis[0] == "Y" and (len(axis) == 1 or axis[1:].isdigit()))


def axis_to_biomek(axis: str, value: float) -> float:
  """An axis's target in PyLabRobot's units as Biomek's value for it:
  - X, Y, Y1-Y8, GY: a deck x / y, mm, in Biomek's frame, cm;
  - Z, Z1-Z8, GZ (mm above the deck), GG, S (mm): cm;
  - GR: degrees counterclockwise from +X seen from above (PyLabRobot's), as Biomek's, which turn
    the other way (its 90 points the fingers to the front);
  - the pumps (D, D1-D8): uL, as Biomek's.
  """
  if axis == "X":
    return to_cm(biomek_x(value))
  if _is_y(axis):
    return to_cm(biomek_y(value))
  if axis == "GR":
    return -value
  if axis[0] == "D":
    return value
  return to_cm(value)


def axis_from_biomek(axis: str, value: float) -> float:
  """`axis_to_biomek`'s inverse."""
  if axis == "X":
    return deck_x(to_mm(value))
  if _is_y(axis):
    return deck_y(to_mm(value))
  if axis == "GR":
    return -value
  if axis[0] == "D":
    return value
  return to_mm(value)


def axis_to_counts(pod: "Pod", axis: str, value: float) -> int:
  """An axis's target in PyLabRobot's units (`axis_to_biomek`) in counts."""
  declared = pod.declared
  if declared is None:
    raise RuntimeError(f"{pod} has no declared configuration")
  return declared.to_counts(axis, axis_to_biomek(axis, value))


def counts_to_axis(pod: "Pod", axis: str, counts: int) -> float:
  """An axis's position in counts in PyLabRobot's units (`axis_from_biomek`)."""
  declared = pod.declared
  if declared is None:
    raise RuntimeError(f"{pod} has no declared configuration")
  return axis_from_biomek(axis, declared.to_value(axis, counts))


# -- speeds ----------------------------------------------------------------------------------------


def percent_to_field(percent: float, truncate: bool = False) -> int:
  """A speed Biomek states in percent of an axis's maximum, as the speed field, 1..1000. Biomek
  rounds, except where it truncates (`truncate`: the Span-8 prime's `D1SpeedLimit`, 80.17 %, is
  the logged 801)."""
  field = int(percent * 10) if truncate else round(percent * 10)
  return max(1, min(FULL_SPEED, field))


def _per_unit(pod: "Pod", axis: str) -> float:
  """Counts per PyLabRobot unit of `axis` (per mm, deg or uL)."""
  declared = pod.declared
  if declared is None:
    raise RuntimeError(f"{pod} has no declared configuration")
  conversion = declared.conversion[axis]
  return abs(conversion.const) / (MM_PER_CM if conversion.unit == "cm" else 1.0)


def max_rate(pod: "Pod", axis: str) -> float:
  """`axis`'s maximum velocity in PyLabRobot units per second (mm/s, deg/s, uL/s)."""
  return pod.axis(axis).limits.max_velocity / _per_unit(pod, axis)


def rate_to_field(pod: "Pod", axis: str, rate: float, clamp: bool = False) -> int:
  """The speed field that moves `axis` at `rate` PyLabRobot units per second (mm/s, deg/s, uL/s):
  its peak velocity; the move's acceleration scales with it, as the instrument has one speed per
  move.

  Raises:
    ValueError: for a rate not above 0, or above the axis's maximum (unless `clamp`, which takes
      it to 1..1000).
  """
  field = round(rate * _per_unit(pod, axis) / pod.axis(axis).limits.max_velocity * FULL_SPEED)
  if clamp:
    return max(1, min(FULL_SPEED, field))
  if not rate > 0 or field > FULL_SPEED:
    raise ValueError(
      f"{pod}: {axis} moves at up to {max_rate(pod, axis):.4g} per second, not {rate}"
    )
  return max(1, field)


def field_to_rate(pod: "Pod", axis: str, field: int) -> float:
  """The rate (PyLabRobot units per second) the speed field `field` moves `axis` at."""
  return field / FULL_SPEED * max_rate(pod, axis)


def fraction_to_field(fraction: float) -> int:
  """A speed as a fraction of each axis's maximum, (0, 1], as the speed field: for a move of
  several axes, whose maxima differ, so that no single rate describes it.

  Raises:
    ValueError: outside (0, 1].
  """
  if not 0 < fraction <= 1 or math.isnan(fraction):
    raise ValueError(f"a speed fraction is above 0 and at most 1, not {fraction}")
  return max(1, round(fraction * FULL_SPEED))


def cutoff_to_counts(pod: "Pod", axis: str, rate: float) -> int:
  """A pump's cutoff velocity, uL/s, in counts/s."""
  return round(rate * _per_unit(pod, axis))

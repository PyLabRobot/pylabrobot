"""Biomek tip cones, and a private helper that turns Biomek well sections into height↔volume tables.

Biomek describes a tip by its length, capacity and seating depth (which `Tip` holds) and also by its
cone. Plate wells are ordinary PyLabRobot `Well`s: Biomek's section outline is only used when
defining them, to build `height_volume_data`.
"""

import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.tip import Tip


class BiomekTip(Tip):
  """A tip for Biomek instruments: a `Tip` with its cone, which Biomek uses to place the tip near a
  wall (a touch-off), from `end_diameter` at its end to `diameter` over `cone_length` (mm)."""

  def __init__(
    self,
    name: str,
    diameter: float,
    size_z: float,
    has_filter: bool,
    maximal_volume: float,
    fitting_depth: float,
    end_diameter: float,
    cone_length: float,
    nominal_volume: Optional[float] = None,
    collar_height: Optional[float] = None,
    category: str = "tip",
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
    is_conductive: Optional[bool] = None,
  ):
    """Initialize a Biomek tip with its dimensions and its cone."""
    super().__init__(
      name=name,
      diameter=diameter,
      size_z=size_z,
      has_filter=has_filter,
      maximal_volume=maximal_volume,
      fitting_depth=fitting_depth,
      nominal_volume=nominal_volume,
      collar_height=collar_height,
      category=category,
      model=model,
      pick_up_location=pick_up_location,
      is_conductive=is_conductive,
    )
    self.end_diameter = end_diameter
    self.cone_length = cone_length

  def __eq__(self, other: object) -> bool:
    """Compare tip fields and the cone."""
    return (
      isinstance(other, BiomekTip)
      and super().__eq__(other)
      and self.end_diameter == other.end_diameter
      and self.cone_length == other.cone_length
    )

  def serialize(self) -> dict:
    """Serialize a Biomek tip with its cone."""
    return {
      **super().serialize(),
      "end_diameter": self.end_diameter,
      "cone_length": self.cone_length,
    }

  def radius_at(self, from_end: float) -> float:
    """The tip's outside radius `from_end` mm above its end: widening along the cone, then its
    full radius."""
    end, full = self.end_diameter / 2, self.get_size_x() / 2
    if self.cone_length <= 0:
      return full
    return end + (full - end) * min(max(from_end, 0.0) / self.cone_length, 1.0)


@dataclass(frozen=True)
class _WellSegment:
  """One Biomek well section, from the bottom up, in mm (authoring only).

  Attributes:
    height: how tall the section is.
    top_x, top_y: its half-widths at its top (the radius, for a round section).
    bottom_x, bottom_y: its half-widths at its bottom (0 for a cone from a point).
    round: whether its outline is round (else a rectangle).
    cap: a round bottom: a spherical cap reaching `top_x` at `height`.
  """

  height: float
  top_x: float
  top_y: float
  bottom_x: float
  bottom_y: float
  round: bool = True
  cap: bool = False

  def half_widths(self, z: float) -> Tuple[float, float]:
    """Half-widths (x, y), mm, `z` mm above the section's bottom."""
    if self.cap:
      sphere = (self.top_x * self.top_x + self.height * self.height) / (2 * self.height)
      r = math.sqrt(max(0.0, 2 * sphere * z - z * z))
      return r, r
    t = z / self.height
    return (
      self.bottom_x + (self.top_x - self.bottom_x) * t,
      self.bottom_y + (self.top_y - self.bottom_y) * t,
    )

  def area(self, z: float) -> float:
    """Cross-section, mm^2, `z` mm above the section's bottom."""
    hx, hy = self.half_widths(z)
    return math.pi * hx * hy if self.round else 4 * hx * hy

  def volume(self, h: float) -> float:
    """uL (mm^3) in the bottom `h` mm of the section. The area is quadratic in z for every shape,
    so Simpson's rule is exact."""
    h = min(h, self.height)
    if h <= 0:
      return 0.0
    return h / 6 * (self.area(0) + 4 * self.area(h / 2) + self.area(h))


# mm between a height -> volume table's heights where a section's cross-section changes with height
# (a cone, a cap): PyLabRobot interpolates between them.
_KNOT_STEP = 0.1


def _volume_at(segments: Sequence[_WellSegment], z: float) -> float:
  """uL held `z` mm above the bottom, by the sections."""
  left, v = z, 0.0
  for seg in segments:
    v += seg.volume(left)
    left -= seg.height
    if left <= 0:
      break
  return v


def height_volume_data_from_segments(
  segments: Sequence[_WellSegment], depth: float
) -> Dict[float, float]:
  """A height (mm) -> volume (uL) table from Biomek well sections, up to `depth`."""
  zs = [0.0]
  for seg in segments:
    h = seg.height
    constant = seg.half_widths(0.0) == seg.half_widths(h) == seg.half_widths(h / 2)
    n = 1 if constant else max(1, math.ceil(h / _KNOT_STEP))
    zs += [zs[-1] + h * i / n for i in range(1, n + 1)]
  if depth > zs[-1]:
    zs.append(depth)
  return {round(z, 6): _volume_at(segments, z) for z in zs}

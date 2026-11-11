"""Biomek's own shapes on PyLabRobot's resources: a tip's cone and a well's outline by height.

Biomek describes a tip by its length, capacity and seating depth (which `Tip` holds) and also by its
cone, and a well by its sections from the bottom up, each with an outline that changes with height.
A Biomek touch-off places the tip against the wall at a height, so it needs both: these classes
carry them, and everything else about the resource is PyLabRobot's.
"""

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.well import Well
from pylabrobot.utils.interpolation import interpolate_1d


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
class WellSegment:
  """One section of a Biomek well, from the bottom up, in mm.

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
KNOT_STEP = 0.1


class BiomekWell(Well):
  """A well with Biomek's outline by height (`segments`, bottom up): its height <-> volume table
  (`height_volume_data`) is made from them, and `wall_distance` says where its wall is at a height.
  With no segments it is a plain `Well`."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    segments: Sequence[Union[WellSegment, Mapping[str, Any]]] = (),
    height_volume_data: Optional[Dict[float, float]] = None,
    max_volume: Optional[float] = None,
    **kwargs: Any,
  ):
    self.segments: Tuple[WellSegment, ...] = tuple(
      s if isinstance(s, WellSegment) else WellSegment(**s) for s in segments
    )
    if height_volume_data is None and self.segments:
      height_volume_data = self._height_volume_table(size_z)
    if max_volume is None and height_volume_data is not None:
      max_volume = interpolate_1d(size_z, height_volume_data, bounds_handling="error")
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      height_volume_data=height_volume_data,
      max_volume=max_volume,
      **kwargs,
    )

  def half_widths(self, z: float) -> Tuple[float, float]:
    """Half-widths (x, y), mm, `z` mm above the bottom (the top's above the top)."""
    left = max(0.0, z)
    for seg in self.segments:
      if left <= seg.height:
        return seg.half_widths(left)
      left -= seg.height
    last = self.segments[-1]
    return last.half_widths(last.height)

  def wall_distance(self, z: float, angle: float) -> float:
    """How far, mm, the wall is from the well's axis `z` mm above its bottom, in direction `angle`
    (radians): the radius for a round well, to the nearest side for a rectangular one."""
    hx, hy = self.half_widths(z)
    if all(s.round for s in self.segments):
      return hx
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    return min(hx / c if c > 1e-12 else math.inf, hy / s if s > 1e-12 else math.inf)

  def volume_at(self, z: float) -> float:
    """uL held `z` mm above the bottom, by the segments."""
    left, v = z, 0.0
    for seg in self.segments:
      v += seg.volume(left)
      left -= seg.height
      if left <= 0:
        break
    return v

  def _height_volume_table(self, depth: float) -> Dict[float, float]:
    """A point at each section's ends, and every `KNOT_STEP` in a section whose cross-section
    changes, up to the rim."""
    zs = [0.0]
    for seg in self.segments:
      h = seg.height
      constant = seg.half_widths(0.0) == seg.half_widths(h) == seg.half_widths(h / 2)
      n = 1 if constant else max(1, math.ceil(h / KNOT_STEP))
      zs += [zs[-1] + h * i / n for i in range(1, n + 1)]
    if depth > zs[-1]:
      zs.append(depth)
    return {round(z, 6): self.volume_at(z) for z in zs}

  def __eq__(self, other: object) -> bool:
    return (
      isinstance(other, BiomekWell) and super().__eq__(other) and self.segments == other.segments
    )

  __hash__ = Resource.__hash__

  def serialize(self) -> dict:
    return {**super().serialize(), "segments": [asdict(s) for s in self.segments]}

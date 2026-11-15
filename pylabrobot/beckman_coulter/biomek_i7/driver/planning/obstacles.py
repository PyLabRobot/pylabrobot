"""What a pod's moves must clear, as Biomek lists it for a pod: the instrument's structure (each
face widened by its buffer), one box per deck position (from its ALP's base up to the higher of
its `min_safe_height` and what stands on it) and each position's own obstacles (a trash's walls),
all in Biomek's frame (cm). The device builds one list per pod (`BiomekI7Device.obstacles`); an
operation that works in positions leaves their boxes out (`Obstacles.without`), their own
obstacles staying in."""

from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.planning.pod_model import (
  OPEN,
  Bounds,
  Part,
  _box,
)


def obstacle_part(
  name: str,
  x: Tuple[Optional[float], Optional[float]],
  y: Tuple[Optional[float], Optional[float]],
  z: Tuple[Optional[float], Optional[float]],
  buffer: float = 0.0,
) -> Part:
  """A fixed box in Biomek's frame (cm), a bound that is None open on that side, each face
  widened by `buffer`, as Biomek makes an obstacle."""

  def lo(v: Optional[float]) -> float:
    return -OPEN if v is None else v - buffer

  def hi(v: Optional[float]) -> float:
    return OPEN if v is None else v + buffer

  return Part(name, _box(lo(x[0]), hi(x[1]), lo(y[0]), hi(y[1])), lo(z[0]), hi(z[1]))


def bounds_of(parts: Sequence[Part]) -> Bounds:
  """The bounds of `parts`: x0, x1, y0, y1, z0, z1 (cm)."""
  xs = [px for part in parts for px, _ in part.outline]
  ys = [py for part in parts for _, py in part.outline]
  return (
    min(xs),
    max(xs),
    min(ys),
    max(ys),
    min(p.z_low for p in parts),
    max(p.z_high for p in parts),
  )


@dataclass(frozen=True)
class Obstacles:
  """What a pod's moves must clear, in Biomek's frame (cm): the instrument's obstacles (checked
  against the pod's body as well as its parts) and the deck's boxes (positions and their own
  obstacles, checked against the pipettor's and the gripper's parts, what it holds included)."""

  structure: Tuple[Part, ...] = ()
  deck: Tuple[Part, ...] = ()
  # The names of the deck's parts that are positions' boxes (the others are their own obstacles).
  holders: Tuple[str, ...] = ()

  @cached_property
  def ceilings(self) -> Tuple[Tuple[Part, Bounds], ...]:
    """The structure's parts with no top (checked against the pod's body too), with bounds."""
    return tuple((p, bounds_of([p])) for p in self.structure if p.z_high >= OPEN)

  @cached_property
  def everything(self) -> Tuple[Tuple[Part, Bounds], ...]:
    """Every part, with bounds."""
    return tuple((p, bounds_of([p])) for p in (*self.structure, *self.deck))

  def without(self, holders: Iterable[str]) -> "Obstacles":
    """These obstacles with the boxes of the positions named in `holders` left out (an operation
    working in them); their own obstacles and the structure stay. A name that is not a position's
    box leaves nothing out."""
    out = {h for h in holders if h in self.holders}
    if not out:
      return self
    return Obstacles(
      self.structure,
      tuple(p for p in self.deck if p.name not in out),
      tuple(n for n in self.holders if n not in out),
    )

"""The instrument's own structure around the deck, as resources: the towers, shields, I-beam, pump
bank, cameras, floor and the light curtain's plane.

Biomek keeps them as boxes in its frame (cm; Y towards the front, Z up from the deck surface), a
bound that is not given open on that side, each face widened by a buffer. A `BiomekObstacle` keeps
those bounds as Biomek gives them, which is what the deck check uses, and is sized as the part of
that box within the instrument, which is what it draws as. Made from the declared configuration's
obstacle list (the instrument file's), they are ordinary resources: one can be added or moved by
hand like any other.
"""

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import Obstacle
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .units import MM_PER_CM

# How far an open bound is drawn, cm in Biomek's frame: to the instrument's edges (left, right,
# back, front), from below the deck surface, and up to above the pods' tops. The deck check uses
# the open bound itself.
Extent = Tuple[float, float, float, float, float, float]


class BiomekObstacle(Resource):
  """A fixed part of the instrument as Biomek keeps it: `x1`..`z2` its box in Biomek's frame (cm;
  None open on that side), each face widened by `buffer` (cm)."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    buffer: float = 0.0,
    x1: Optional[float] = None,
    x2: Optional[float] = None,
    y1: Optional[float] = None,
    y2: Optional[float] = None,
    z1: Optional[float] = None,
    z2: Optional[float] = None,
    category: str = "instrument_structure",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )
    self.buffer = buffer
    self.x1, self.x2, self.y1, self.y2, self.z1, self.z2 = x1, x2, y1, y2, z1, z2

  def obstacle(self) -> Obstacle:
    """The box as Biomek keeps it."""
    return Obstacle(self.name, self.buffer, self.x1, self.x2, self.y1, self.y2, self.z1, self.z2)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "buffer": self.buffer,
      "x1": self.x1,
      "x2": self.x2,
      "y1": self.y1,
      "y2": self.y2,
      "z1": self.z1,
      "z2": self.z2,
    }


def merge_obstacles(per_pod: Iterable[Sequence[Obstacle]]) -> List[Obstacle]:
  """One obstacle per name from each pod's list (Biomek keeps a list per pod, the same parts with a
  few edges measured per pod): where they differ, the larger box, and the larger buffer."""

  def low(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else min(a, b)

  def high(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else max(a, b)

  merged: Dict[str, Obstacle] = {}
  for obstacles in per_pod:
    for o in obstacles:
      m = merged.get(o.name)
      merged[o.name] = (
        o
        if m is None
        else Obstacle(
          o.name,
          max(m.buffer, o.buffer),
          low(m.x1, o.x1),
          high(m.x2, o.x2),
          low(m.y1, o.y1),
          high(m.y2, o.y2),
          low(m.z1, o.z1),
          high(m.z2, o.z2),
        )
      )
  return list(merged.values())


def structure_resources(
  obstacles: Sequence[Obstacle], extent: Extent, to_device: "FrameToDevice"
) -> List[Tuple[BiomekObstacle, Coordinate]]:
  """Each obstacle as a `BiomekObstacle` and where it goes in the device: drawn as the part of its
  box (unwidened) within `extent` (Biomek cm: x0, x1, y0, y1, z0, z1); one wholly outside it is
  left out."""
  out = []
  x0, x1, y0, y1, z0, z1 = extent

  def clip(lo: Optional[float], hi: Optional[float], a: float, b: float) -> Tuple[float, float]:
    return max(a, -math.inf if lo is None else lo), min(b, math.inf if hi is None else hi)

  for o in obstacles:
    bx = clip(o.x1, o.x2, x0, x1)
    by = clip(o.y1, o.y2, y0, y1)
    bz = clip(o.z1, o.z2, z0, z1)
    if bx[1] <= bx[0] or by[1] <= by[0] or bz[1] <= bz[0]:
      continue
    resource = BiomekObstacle(
      name=f"structure_{o.name}",
      size_x=(bx[1] - bx[0]) * MM_PER_CM,
      size_y=(by[1] - by[0]) * MM_PER_CM,
      size_z=(bz[1] - bz[0]) * MM_PER_CM,
      buffer=o.buffer,
      x1=o.x1,
      x2=o.x2,
      y1=o.y1,
      y2=o.y2,
      z1=o.z1,
      z2=o.z2,
      model=o.name,
    )
    # Its front left bottom corner: Biomek's Y runs to the front, so the box's front is its high Y.
    out.append((resource, to_device(bx[0], by[1], bz[0])))
  return out


class FrameToDevice:
  """A point in Biomek's frame (cm) as a location in the device (mm)."""

  def __init__(self, deck_location: Coordinate, frame_x: float, frame_y: float):
    self.deck_location, self.frame_x, self.frame_y = deck_location, frame_x, frame_y

  def __call__(self, x: float, y: float, z: float) -> Coordinate:
    return self.deck_location + Coordinate(
      x * MM_PER_CM + self.frame_x, self.frame_y - y * MM_PER_CM, z * MM_PER_CM
    )

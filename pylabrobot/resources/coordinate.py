from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Coordinate:
  """Represents coordinates. This is often used to represent the location of a :class:`~Resource`,
  relative to its parent resource.
  """

  x: float = 0
  y: float = 0
  z: float = 0

  def __post_init__(self):
    # Round to 4 decimal places to minimize floating point errors (100nm)
    self.x = round(self.x, 4)
    self.y = round(self.y, 4)
    self.z = round(self.z, 4)

  @staticmethod
  def zero() -> Coordinate:
    return Coordinate(0, 0, 0)

  def __add__(self, other) -> Coordinate:
    return Coordinate(
      x=(self.x or 0) + (other.x or 0),
      y=(self.y or 0) + (other.y or 0),
      z=(self.z or 0) + (other.z or 0),
    )

  def __sub__(self, other) -> Coordinate:
    return Coordinate(
      x=(self.x or 0) - (other.x or 0),
      y=(self.y or 0) - (other.y or 0),
      z=(self.z or 0) - (other.z or 0),
    )

  def __str__(self) -> str:
    return f"Coordinate({self.x:07.3f}, {self.y:07.3f}, {self.z:07.3f})"

  def __neg__(self) -> Coordinate:
    return Coordinate(-self.x, -self.y, -self.z)

  def vector(self) -> list[float]:
    return [self.x, self.y, self.z]

  def __iter__(self):
    return iter((self.x, self.y, self.z))

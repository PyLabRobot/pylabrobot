from __future__ import annotations


class Coordinate:
  """ Represents coordinates. This is often used to represent the location of a :class:`~Resource`,
  relative to its parent resource.
  """

  def __init__(self, x: float, y: float, z: float):
    # Round to 4 decimal places to minimize floating point errors (100nm)
    self.x = round(x, 4)
    self.y = round(y, 4)
    self.z = round(z, 4)

  @classmethod
  def zero(cls) -> Coordinate:
    return Coordinate(0, 0, 0)

  def __add__(self, other) -> Coordinate:
    return Coordinate(
      x=(self.x or 0) + (other.x or 0),
      y=(self.y or 0) + (other.y or 0),
      z=(self.z or 0) + (other.z or 0)
    )

  def __sub__(self, other) -> Coordinate:
    return Coordinate(
      x=(self.x or 0) - (other.x or 0),
      y=(self.y or 0) - (other.y or 0),
      z=(self.z or 0) - (other.z or 0)
    )

  def __eq__(self, other) -> bool:
    return (isinstance(other, Coordinate) and \
            self.x == other.x and self.y == other.y and self.z == other.z)

  def __str__(self) -> str:
    if self.x is not None and self.y is not None and self.z is not None:
      return f"({self.x:07.3f}, {self.y:07.3f}, {self.z:07.3f})"
    return "(None, None, None)"

  def __repr__(self) -> str:
    return f"Coordinate({self.x}, {self.y}, {self.z})"

  def __hash__(self) -> int:
    return hash((self.x, self.y, self.z))

  def serialize(self) -> dict:
    return {"x": self.x, "y": self.y, "z": self.z}

  @staticmethod
  def deserialize(d: dict) -> Coordinate:
    return Coordinate(x=d["x"], y=d["y"], z=d["z"])

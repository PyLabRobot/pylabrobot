

class Coordinate:
  """ Represents coordinates, often relative to either a liquid handler or container """

  def __init__(self, x, y, z):
    self.x = x
    self.y = y
    self.z = z

  def __add__(self, other):
    return Coordinate(
      x=(self.x or 0) + (other.x or 0),
      y=(self.y or 0) + (other.y or 0),
      z=(self.z or 0) + (other.z or 0)
    )

  def __sub__(self, other):
    return Coordinate(
      x=(self.x or 0) - (other.x or 0),
      y=(self.y or 0) - (other.y or 0),
      z=(self.z or 0) - (other.z or 0)
    )

  def __eq__(self, other):
    return self.x == other.x and self.y == other.y and self.z == other.z

  def __str__(self):
    if self.x is not None and self.y is not None and self.z is not None:
      return f"({self.x:07.3f}, {self.y:07.3f}, {self.z:07.3f})"
    return "(None, None, None)"

  def __repr__(self):
    return f"Coordinate({self.x}, {self.y}, {self.z})"

  def serialize(self) -> dict:
    return dict(x=self.x, y=self.y, z=self.z)

  @staticmethod
  def deserialize(d: dict):
    return Coordinate(x=d["x"], y=d["y"], z=d["z"])

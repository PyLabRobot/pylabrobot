class Coordinate:
  """ Represents coordinates, often relative to either a liquid handler or container """

  def __init__(self, x, y, z):
    self.x = x
    self.y = y
    self.z = z

  def __add__(self, other):
    return Coordinate(self.x + other.x, self.y + other.y, self.z + other.z)

  def __sub__(self, other):
    return Coordinate(self.x + other.x, self.y + other.y, self.z + other.z)

  def __eq__(self, other):
    return self.x == other.x and self.y == other.y and self.z == other.z

  def __str__(self):
    if self.x is not None and self.y is not None and self.z is not None:
      return f"({self.x:07.3f}, {self.y:07.3f}, {self.z:07.3f})"
    return "(None, None, None)"

  def __repr__(self):
    return f"Coordinate({self.x}, {self.y}, {self.z})"

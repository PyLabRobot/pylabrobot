""" Abstract base class for Plate resources. """

from abc import ABCMeta

from .resource import Resource, Coordinate


class Plate(Resource, metaclass=ABCMeta):
  """ Abstract base class for Plate resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    dx: float,
    dy: float,
    dz: float,
    one_dot_max: float,
    location: Coordinate = Coordinate(None, None, None)
  ):
    super().__init__(name, size_x, size_y, size_z, location=location + Coordinate(dx, dy, dz),
                     category="plate")
    self.dx = dx
    self.dy = dy
    self.dz = dz
    self.one_dot_max = one_dot_max

  def serialize(self):
    return dict(
      **super().serialize(),
      dx=self.dx,
      dy=self.dy,
      dz=self.dz,
      one_dot_max=self.one_dot_max,
    )

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(size_x={self.size_x}, size_y={self.size_y}, "
            f"size_z={self.size_z}, dx={self.dx}, dy={self.dy}, dz={self.dz}, "
            f"location={self.location}, one_dot_max={self.one_dot_max})")

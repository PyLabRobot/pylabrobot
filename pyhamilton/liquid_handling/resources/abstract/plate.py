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
    location: Coordinate = Coordinate(None, None, None)
  ):
    super().__init__(name, size_x, size_y, size_z, location=location + Coordinate(dx, dy, dz),
                     category="plate")
    self.dx = dx
    self.dy = dy
    self.dz = dz

  def serialize(self):
    return dict(
      **super().serialize(),
      dx=self.dx,
      dy=self.dy,
      dz=self.dz
    )

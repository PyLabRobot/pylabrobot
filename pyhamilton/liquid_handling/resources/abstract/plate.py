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
    dz: float
  ):
    super().__init__(name, size_x, size_y, size_z, location=Coordinate(dx, dy, dz))
    self.dx = dx
    self.dy = dy
    self.dz = dz

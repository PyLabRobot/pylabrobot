""" Abstract base class for Tips resources. """

from abc import ABCMeta

from .resource import Resource, Coordinate


class Tips(Resource, metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    tip_type: str,
    dx: float,
    dy: float,
    dz: float,
    location: Coordinate = Coordinate(None, None, None)
  ):
    super().__init__(name, size_x, size_y, size_z, location=location + Coordinate(dx, dy, dz),
                     category="tips")
    self.tip_type = tip_type
    self.dx = dx
    self.dy = dy
    self.dz = dz

  def serialize(self):
    return dict(
      **super().serialize(),
      tip_type=self.tip_type.serialize(),
      dx=self.dx,
      dy=self.dy,
      dz=self.dz
    )

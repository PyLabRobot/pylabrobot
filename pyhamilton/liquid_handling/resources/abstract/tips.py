""" Abstract base class for Tips resources. """

from abc import ABCMeta

from .resource import Resource, Coordinate
from .tip_type import TipType


class Tips(Resource, metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    tip_type: TipType,
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

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self.size_x}, "
            f"size_y={self.size_y}, size_z={self.size_z}, tip_type={self.tip_type}, dx={self.dx}, "
            f"dy={self.dy}, dz={self.dz}, location={self.location})")

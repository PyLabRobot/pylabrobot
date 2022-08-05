""" Base classes for Plate and Lid resources. """

from __future__ import annotations

from typing import Optional

from .resource import Resource, Coordinate


class Lid(Resource):
  """ Lid for plates. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    location: Coordinate = Coordinate(None, None, None),
  ):
    """ Create a lid for a plate.

    Args:
      name: Name of the lid.
      size_x: Size of the lid in x-direction.
      size_y: Size of the lid in y-direction.
      size_z: Size of the lid in z-direction.
      location: Location of the lid.
    """
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z,
      location=location + Coordinate(0, 0, size_z), category="lid")


class Plate(Resource):
  """ Base class for Plate resources. """

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
    location: Coordinate = Coordinate(None, None, None),
    lid_height: Optional[float] = None,
  ):
    """ Initialize a Plate resource.

    Args:
      name: Name of the plate.
      size_x: Size of the plate in the x direction.
      size_y: Size of the plate in the y direction.
      size_z: Size of the plate in the z direction.
      dx: The position shift in the x direction. Defined by Hamilton.
      dy: The position shift in the y direction. Defined by Hamilton.
      dz: The position shift in the z direction. Defined by Hamilton.
      one_dot_max: I don't know.
      location: Coordinate of the plate.
      lid_height: Height of the lid in mm.
    """

    super().__init__(name, size_x, size_y, size_z, location=location + Coordinate(dx, dy, dz),
                     category="plate")
    self.dx = dx
    self.dy = dy
    self.dz = dz
    self.one_dot_max = one_dot_max
    self.lid: Optional[Lid] = None

    if lid_height is not None:
      # TODO: Coordinate(0, 0, size_z)
      lid = Lid(name + "_lid", location=Coordinate(0, 0, 0),
        size_x=size_x, size_y=size_y, size_z=lid_height)
      self.assign_child_resource(lid)

  def assign_child_resource(self, resource, **kwargs):
    if isinstance(resource, Lid):
      if self.lid is not None:
        raise ValueError(f"Plate '{self.name}' already has a lid.")
      self.lid = resource
    return super().assign_child_resource(resource, **kwargs)

  def unassign_child_resource(self, resource, **kwargs):
    if isinstance(resource, Lid) and self.lid is not None:
      self.lid = None
    return super().unassign_child_resource(resource, **kwargs)

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

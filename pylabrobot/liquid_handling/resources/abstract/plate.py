""" Base classes for Plate and Lid resources. """

from __future__ import annotations

from typing import Optional, Union, List

import pylabrobot.utils

from .resource import Resource, Coordinate
from .well import Well
from .itemized_resource import ItemizedResource


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


class Plate(ItemizedResource[Well]):
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
    num_wells_x: int,
    num_wells_y: int,
    well_size_x: float,
    well_size_y: float,
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
      dx: The distance between the start of the plate and the start of the first well in the x
        direction.
      dy: The distance between the start of the plate and the start of the first well in the y
        direction.
      dz: The distance between the start of the plate and the start of the first well in the z
        direction.
      num_wells_x: Number of wells in the x direction.
      num_wells_y: Number of wells in the y direction.
      well_size_x: Size of the wells in the x direction.
      well_size_y: Size of the wells in the y direction.
      one_dot_max: I don't know.
      location: Coordinate of the plate.
      lid_height: Height of the lid in mm.
    """

    # TODO: remove location here and add them on wells instead?
    super().__init__(name, size_x, size_y, size_z, location=location + Coordinate(dx, dy, dz),
                     category="plate",
                     num_items_x=num_wells_x, num_items_y=num_wells_y,
                     create_item=lambda i, j: Well(name + f"_well_{i}_{j}",
                      location=Coordinate(
                        x=i * well_size_x, y=j * -well_size_y, z=0)))
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

    self.well_size_x = well_size_x
    self.well_size_y = well_size_y

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
    # TODO(serializer): serialize wells and lid as well
    return dict(
      **super().serialize(),
      dx=self.dx,
      dy=self.dy,
      dz=self.dz,
      one_dot_max=self.one_dot_max,
    )

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(size_x={self._size_x}, size_y={self._size_y}, "
            f"size_z={self._size_z}, dx={self.dx}, dy={self.dy}, dz={self.dz}, "
            f"location={self.location}, one_dot_max={self.one_dot_max})")

  def get_well(self, identifier: Union[str, int]) -> Well:
    """ Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier)

  def get_wells(self, identifier: Union[str, List[int]]) -> List[Well]:
    """ Get the wells with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return super().get_items(identifier)

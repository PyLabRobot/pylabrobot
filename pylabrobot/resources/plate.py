""" Base classes for Plate and Lid resources. """

from __future__ import annotations

from typing import Callable, Optional, Union, List, Sequence, cast

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
    category: str = "lid"
  ):
    """ Create a lid for a plate.

    Args:
      name: Name of the lid.
      size_x: Size of the lid in x-direction.
      size_y: Size of the lid in y-direction.
      size_z: Size of the lid in z-direction.
    """
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category)


class Plate(ItemizedResource[Well]):
  """ Base class for Plate resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    items: Optional[List[List[Well]]] = None,
    num_items_x: Optional[int] = None,
    num_items_y: Optional[int] = None,
    one_dot_max: Optional[float] = 0,
    category: str = "plate",
    lid_height: float = 0,
    with_lid: bool = False,
    compute_volume_from_height: Optional[Callable[[float], float]] = None,
    model: Optional[str] = None
  ):
    """ Initialize a Plate resource.

    Args:
      name: Name of the plate.
      size_x: Size of the plate in the x direction.
      size_y: Size of the plate in the y direction.
      size_z: Size of the plate in the z direction.
      dx: The distance between the start of the plate and the center of the first well (A1) in the x
        direction.
      dy: The distance between the start of the plate and the center of the first well (A1) in the y
        direction.
      dz: The distance between the start of the plate and the center of the first well (A1) in the z
        direction.
      num_items_x: Number of wells in the x direction.
      num_items_y: Number of wells in the y direction.
      well_size_x: Size of the wells in the x direction.
      well_size_y: Size of the wells in the y direction.
      one_dot_max: I don't know. Hamilton specific.
      lid_height: Height of the lid in mm, only used if `with_lid` is True.
      with_lid: Whether the plate has a lid.
    """

    super().__init__(name, size_x, size_y, size_z, items=items, num_items_x=num_items_x,
      num_items_y=num_items_y, category=category, model=model)
    self.one_dot_max = one_dot_max
    self.lid: Optional[Lid] = None
    self.lid_height = lid_height
    self._compute_volume_from_height = compute_volume_from_height

    if with_lid:
      assert lid_height > 0, "Lid height must be greater than 0 if with_lid == True."

      lid = Lid(name + "_lid", size_x=size_x, size_y=size_y, size_z=lid_height)
      self.assign_child_resource(lid, location=Coordinate(0, 0, self.get_size_z() - lid_height))

  def compute_volume_from_height(self, height: float) -> float:
    """ Compute the volume of liquid in a well from the height of the liquid.

    Args:
      height: Height of the liquid in the well.

    Returns:
      The volume of liquid in the well.

    Raises:
      NotImplementedError: If the plate does not have a volume computation function.
    """

    if self._compute_volume_from_height is None:
      raise NotImplementedError("compute_volume_from_height not implemented.")

    return self._compute_volume_from_height(height)

  def assign_child_resource(self, resource: Resource, location: Optional[Coordinate]):
    if isinstance(resource, Lid):
      if self.has_lid():
        raise ValueError(f"Plate '{self.name}' already has a lid.")
      self.lid = resource
    return super().assign_child_resource(resource, location=location)

  def unassign_child_resource(self, resource):
    if isinstance(resource, Lid) and self.has_lid():
      self.lid = None
    return super().unassign_child_resource(resource)

  def serialize(self):
    return {
      **super().serialize(),
      "one_dot_max": self.one_dot_max,
    }

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location}, "
            f"one_dot_max={self.one_dot_max})")

  def get_well(self, identifier: Union[str, int]) -> Well:
    """ Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier)

  def get_wells(self,
    identifier: Union[str, Sequence[int]]) -> List[Well]:
    """ Get the wells with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return super().get_items(identifier)

  def has_lid(self) -> bool:
    return self.lid is not None

  def set_well_volumes(self, volumes: Union[List[List[float]], List[float], float]) -> None:
    """ Update the volume in the volume tracker of each well in the plate.

    Args:
      volumes: A list of volumes, one for each well in the plate. The list can be a list of lists,
        where each inner list contains the volumes for each well in a column.  If a single float is
        given, the volume is assumed to be the same for all wells. Volumes are in uL.

    Raises:
      ValueError: If the number of volumes does not match the number of wells in the plate.

    Example:
      Set the volume of each well in a 96-well plate to 10 uL.

      >>> plate = Plate("plate", 127.0, 86.0, 14.5, num_items_x=12, num_items_y=8)
      >>> plate.update_well_volumes(10)
    """

    if isinstance(volumes, float):
      volumes = [volumes] * self.num_items
    elif isinstance(volumes, list) and all(isinstance(column, list) for column in volumes):
      volumes = cast(List[List[float]], volumes) # mypy doesn't know that all() checks the type
      volumes = [list(column) for column in zip(*volumes)] # transpose the list of lists
      volumes = [volume for column in volumes for volume in column] # flatten the list of lists

    volumes = cast(List[float], volumes) # mypy doesn't know type is correct at this point.

    if len(volumes) != self.num_items:
      raise ValueError(f"Number of volumes ({len(volumes)}) does not match number of wells "
                      f"({self.num_items}) in plate '{self.name}'.")

    for i, volume in enumerate(volumes):
      well = self.get_well(i)
      well.tracker.set_used_volume(volume)

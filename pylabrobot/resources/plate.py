""" Base classes for Plate and Lid resources. """

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple, Union, cast


from .liquid import Liquid
from .itemized_resource import ItemizedResource
from .resource import Resource, Coordinate
from .well import Well



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
    category: str = "plate",
    lid_height: float = 0,
    with_lid: bool = False,
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
      lid_height: Height of the lid in mm, only used if `with_lid` is True.
      with_lid: Whether the plate has a lid.
    """

    super().__init__(name, size_x, size_y, size_z, items=items, num_items_x=num_items_x,
      num_items_y=num_items_y, category=category, model=model)
    self.lid: Optional[Lid] = None
    self.lid_height = lid_height

    if with_lid:
      assert lid_height > 0, "Lid height must be greater than 0 if with_lid == True."

      lid = Lid(name + "_lid", size_x=size_x, size_y=size_y, size_z=lid_height)
      self.assign_child_resource(lid, location=Coordinate(0, 0, self.get_size_z() - lid_height))

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True
  ):
    if isinstance(resource, Lid):
      if self.has_lid():
        raise ValueError(f"Plate '{self.name}' already has a lid.")
      self.lid = resource
    return super().assign_child_resource(resource, location=location, reassign=reassign)

  def unassign_child_resource(self, resource):
    if isinstance(resource, Lid) and self.has_lid():
      self.lid = None
    return super().unassign_child_resource(resource)

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})")

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

  def set_well_liquids(
    self,
    liquids: Union[
      List[List[Tuple[Optional["Liquid"], Union[int, float]]]],
      List[Tuple[Optional["Liquid"], Union[int, float]]],
      Tuple[Optional["Liquid"], Union[int, float]]]
  ) -> None:

    """ Update the liquid in the volume tracker for each well in the plate.

    Args:
      liquids: A list of liquids, one for each well in the plate. The list can be a list of lists,
        where each inner list contains the liquids for each well in a column. If a single tuple is
        given, the volume is assumed to be the same for all wells. Liquids are in uL.

    Raises:
      ValueError: If the number of liquids does not match the number of wells in the plate.

    Example:
      Set the volume of each well in a 96-well plate to 10 uL.

      >>> plate = Plate("plate", 127.0, 86.0, 14.5, num_items_x=12, num_items_y=8)
      >>> plate.set_well_liquids((Liquid.WATER, 10))
    """

    if isinstance(liquids, tuple):
      liquids = [liquids] * self.num_items
    elif isinstance(liquids, list) and all(isinstance(column, list) for column in liquids):
      # mypy doesn't know that all() checks the type
      liquids = cast(List[List[Tuple[Optional["Liquid"], float]]], liquids)
      liquids = [list(column) for column in zip(*liquids)] # transpose the list of lists
      liquids = [volume for column in liquids for volume in column] # flatten the list of lists

    if len(liquids) != self.num_items:
      raise ValueError(f"Number of liquids ({len(liquids)}) does not match number of wells "
                      f"({self.num_items}) in plate '{self.name}'.")

    for i, (liquid, volume) in enumerate(liquids):
      well = self.get_well(i)
      well.tracker.set_liquids([(liquid, volume)]) # type: ignore

  def disable_volume_trackers(self) -> None:
    """ Disable volume tracking for all wells in the plate. """

    for well in self.get_all_items():
      well.tracker.disable()

  def enable_volume_trackers(self) -> None:
    """ Enable volume tracking for all wells in the plate. """

    for well in self.get_all_items():
      well.tracker.enable()

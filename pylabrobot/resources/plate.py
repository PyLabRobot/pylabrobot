""" Base classes for Plate and Lid resources. """

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple, Union, cast, Literal

from pylabrobot.resources.resource_holder import ResourceHolderMixin


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
    nesting_z_height: float,
    category: str = "lid",
    model: Optional[str] = None
  ):
    """ Create a lid for a plate.

    Args:
      name: Name of the lid.
      size_x: Size of the lid in x-direction.
      size_y: Size of the lid in y-direction.
      size_z: Size of the lid in z-direction.
      nesting_z_height: the overlap in mm between the lid and its parent plate (in the z-direction).
    """
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z,category=category,
                     model=model)
    self.nesting_z_height = nesting_z_height
    if nesting_z_height == 0:
      print(f"{self.name}: Are you certain that the lid nests 0 mm with its parent plate?")

  def serialize(self) -> dict:
    return {**super().serialize(), "nesting_z_height": self.nesting_z_height}


class Plate(ResourceHolderMixin, ItemizedResource[Well]):
  """ Base class for Plate resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, Well]] = None,
    ordering: Optional[List[str]] = None,
    category: str = "plate",
    lid: Optional[Lid] = None,
    model: Optional[str] = None,
    plate_type: Literal["skirted", "semi-skirted", "non-skirted"] = "skirted",
  ):
    """ Initialize a Plate resource.

    Args:
      well_size_x: Size of the wells in the x direction.
      well_size_y: Size of the wells in the y direction.
      lid: Immediately assign a lid to the plate.
      plate_type: Type of the plate. One of "skirted", "semi-skirted", or "non-skirted". A
        WIP: https://github.com/PyLabRobot/pylabrobot/pull/152#discussion_r1625831517
    """

    super().__init__(name, size_x, size_y, size_z, ordered_items=ordered_items, ordering=ordering,
                     category=category, model=model)
    self._lid: Optional[Lid] = None
    self.plate_type = plate_type

    if lid is not None:
      self.assign_child_resource(lid)

  @property
  def lid(self) -> Optional[Lid]:
    return self._lid

  @lid.setter
  def lid(self, lid: Optional[Lid]) -> None:
    if lid is None:
      self.unassign_child_resource(self._lid)
    else:
      self.assign_child_resource(lid)
    self._lid = lid

  def _get_lid_location(self, lid: Lid) -> Coordinate:
    return Coordinate(0, 0, self.get_size_z() - lid.nesting_z_height)

  def get_default_child_location(self, resource: Resource) -> Coordinate:
    child_location = super().get_default_child_location(resource)
    if isinstance(resource, Lid):
      child_location += self._get_lid_location(resource)
    return child_location

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    if isinstance(resource, Lid):
      if self.has_lid():
        raise ValueError(f"Plate '{self.name}' already has a lid.")
      self._lid = resource
    else:
      assert location is not None, "Location must be specified for if resource is not a lid."
    return super().assign_child_resource(resource, location=location, reassign=reassign)

  def unassign_child_resource(self, resource):
    if isinstance(resource, Lid) and resource == self.lid:
      self._lid = None
    return super().unassign_child_resource(resource)

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})")

  def get_well(self, identifier: Union[str, int, Tuple[int, int]]) -> Well:
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

      >>> plate = Plate("plate", 127.76, 85.48, 14.5, num_items_x=12, num_items_y=8)
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

# TODO: add quadrant definition for 96-well plates & specify current
# quadrant definition is only for 384-well plates
  def get_quadrant(self, quadrant: int) -> List[Well]:
    """ Return the wells in the specified quadrant. Quadrants are overlapping and refer to
    alternating rows and columns of the plate. Quadrant 1 contains A1, A3, C1, etc. Quadrant 2
    contains A2, quadrant 3 contains B1, and quadrant 4 contains B2. """

    if quadrant == 1:
      return [self.get_well((row, column))
                for row in range(0, self.num_items_y, 2)
                for column in range(0, self.num_items_x, 2)]
    elif quadrant == 2:
      return [self.get_well((row, column))
                for row in range(0, self.num_items_y, 2)
                for column in range(1, self.num_items_x, 2)]
    elif quadrant == 3:
      return [self.get_well((row, column))
                for row in range(1, self.num_items_y, 2)
                for column in range(0, self.num_items_x, 2)]
    elif quadrant == 4:
      return [self.get_well((row, column))
                for row in range(1, self.num_items_y, 2)
                for column in range(1, self.num_items_x, 2)]
    else:
      raise ValueError(f"Invalid quadrant number: {quadrant}. Quadrant must be 1, 2, 3, or 4.")

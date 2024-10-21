from typing import Dict, List, Optional, Sequence, Tuple, Union, cast

from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.tube import Tube

from .itemized_resource import ItemizedResource
from .resource import Resource, Coordinate
from .liquid import Liquid

class TubeRack(ItemizedResource[Tube]):
  """ Tube rack resource. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, Tube]] = None,
    model: Optional[str] = None,
  ):
    """ Initialize a TubeRack resource.

    Args:
      name: Name of the tube rack.
      size_x: Size of the tube rack in the x direction.
      size_y: Size of the tube rack in the y direction.
      size_z: Size of the tube rack in the z direction.
      items: List of lists of wells.
      model: Model of the tube rack.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      ordered_items=ordered_items,
      model=model)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    assert location is not None, "Location must be specified for resource."
    return super().assign_child_resource(resource, location=location, reassign=reassign)

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})")

  def get_tube(self, identifier: Union[str, int, Tuple[int, int]]) -> Tube:
    """ Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier)

  def get_tubes(self,
    identifier: Union[str, Sequence[int]]) -> List[Tube]:
    """ Get the tubes with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return super().get_items(identifier)

  def set_tube_liquids(
    self,
    liquids: Union[
      List[List[Tuple[Optional["Liquid"], Union[int, float]]]],
      List[Tuple[Optional["Liquid"], Union[int, float]]],
      Tuple[Optional["Liquid"], Union[int, float]]]
  ) -> None:

    """ Update the liquid in the volume tracker for each tube in the rack.

    Args:
      liquids: A list of liquids, one for each tube in the rack. The list can be a list of lists,
        where each inner list contains the liquids for each tube in a column. If a single tuple is
        given, the volume is assumed to be the same for all tubes. Liquids are in uL.

    Raises:
      ValueError: If the number of liquids does not match the number of tubes in the rack.

    Example:
      Set the volume of each tube in a 4x6 rack to 1000 uL.

      >>> rack = TubeRack("rack", 127.76, 85.48, 14.5, num_items_x=6, num_items_y=4)
      >>> rack.set_tube_liquids((Liquid.WATER, 1000))
    """

    if isinstance(liquids, tuple):
      liquids = [liquids] * self.num_items
    elif isinstance(liquids, list) and all(isinstance(column, list) for column in liquids):
      # mypy doesn't know that all() checks the type
      liquids = cast(List[List[Tuple[Optional["Liquid"], float]]], liquids)
      liquids = [list(column) for column in zip(*liquids)] # transpose the list of lists
      liquids = [volume for column in liquids for volume in column] # flatten the list of lists

    if len(liquids) != self.num_items:
      raise ValueError(f"Number of liquids ({len(liquids)}) does not match number of tubes "
                      f"({self.num_items}) in rack '{self.name}'.")

    for i, (liquid, volume) in enumerate(liquids):
      tube = self.get_tube(i)
      tube.tracker.set_liquids([(liquid, volume)]) # type: ignore


  def disable_volume_trackers(self) -> None:
    """ Disable volume tracking for all tubes in the rack. """

    for tube in self.get_all_items():
      tube.tracker.disable()

  def enable_volume_trackers(self) -> None:
    """ Enable volume tracking for all tubes in the rack. """

    for tube in self.get_all_items():
      tube.tracker.enable()

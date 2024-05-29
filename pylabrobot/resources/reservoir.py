from typing import List, Optional, Sequence, Tuple, Union, cast

from .liquid import Liquid
from .itemized_resource import ItemizedResource
from .resource import Resource, Coordinate
from .well import Well

class Reservoir(ItemizedResource[Well]):
  """ A Reservoir is a container, particularly useful for multichannel liquid handling operations.
      Also particularly useful for storing large amounts of liquids"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    items: Optional[List[List[Well]]] = None,
    num_items_x: Optional[int] = None,
    num_items_y: Optional[int] = None,
    category: Optional[str] = "reservoir",
    model: Optional[str] = None
  ):
    """ Initialize a Reservoir resource.

    Args:
      name: Name of the reservoir.
      size_x: Size of the reservoir in the x direction.
      size_y: Size of the reservoir in the y direction.
      size_z: Size of the reservoir in the z direction.
      dx: The distance between the start of the reservoir
        and the center of the first well (A1) in the x direction.
      dy: The distance between the start of the reservoir
        and the center of the first well (A1) in the y direction.
      dz: The distance between the start of the reservoir
        and the center of the first well (A1) in the z direction.
      num_items_x: Number of wells in the x direction.
      num_items_y: Number of wells in the y direction.
      well_size_x: Size of the wells in the x direction.
      well_size_y: Size of the wells in the y direction.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      items=items,
      num_items_x=num_items_x,
      num_items_y=num_items_y,
      category=category,
      model=model
    )

  def serialize(self) -> dict:
    return {**super().serialize()}

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    assert location is not None, "Location must be specified."
    return super().assign_child_resource(resource, location=location, reassign=reassign)

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

  def set_well_liquids(
    self,
    liquids: Union[
      List[List[Tuple[Optional["Liquid"], Union[int, float]]]],
      List[Tuple[Optional["Liquid"], Union[int, float]]],
      Tuple[Optional["Liquid"], Union[int, float]]]
  ) -> None:

    """ Update the liquid in the volume tracker for each well in the reservoir.

    Args:
      liquids: A list of liquids, one for each well in the reservoir. The list can be a list of lists,
        where each inner list contains the liquids for each well in a column. If a single tuple is
        given, the volume is assumed to be the same for all wells. Liquids are in uL.

    Raises:
      ValueError: If the number of liquids does not match the number of wells in the reservoir.

    Example:
      Set the volume of each well in a 12-well reservoir to 10 mL.

      >>> reservoir = Reservoir("reservoir", 127.0, 86.0, 14.5, num_items_x=12, num_items_y=1)
      >>> reservoir.set_well_liquids((Liquid.WATER, 10000))
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
                      f"({self.num_items}) in reservoir '{self.name}'.")

    for i, (liquid, volume) in enumerate(liquids):
      well = self.get_well(i)
      well.tracker.set_liquids([(liquid, volume)]) # type: ignore

  def disable_volume_trackers(self) -> None:
    """ Disable volume tracking for all wells in the reservoir. """

    for well in self.get_all_items():
      well.tracker.disable()

  def enable_volume_trackers(self) -> None:
    """ Enable volume tracking for all wells in the reservoir. """

    for well in self.get_all_items():
      well.tracker.enable()

from typing import Dict, Optional, Union, cast

from .coordinate import Coordinate
from .itemized_resource import ItemizedResource
from .resource import Resource
from .resource_holder import ResourceHolder
from .tube import Tube


class TubeRack(ItemizedResource[ResourceHolder]):
  """Tube rack resource."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, ResourceHolder]] = None,
    model: Optional[str] = None,
  ):
    """Initialize a TubeRack resource.

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
      model=model,
    )

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    assert location is not None, "Location must be specified for resource."
    return super().assign_child_resource(resource, location=location, reassign=reassign)

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(name={self.name!r}, size_x={self._size_x}, "
      f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})"
    )

  def __setitem__(self, key: Union[int, str], value: Tube) -> None:
    if not isinstance(value, Tube):
      raise ValueError("Only Tubes resources can be added to a TubeRack.")
    self.get_item(key).resource = value

  def get_tube(self, key: Union[int, str]) -> Optional[Tube]:
    """Get the tube at the given position.

    Args:
      key: Position of the tube to get. Can be an integer index or a string name.

    Returns:
      The tube at the given position, or None if there is no tube at that position.
    """
    holder = self.get_item(key)
    return cast(Optional[Tube], holder.resource) if holder.resource is not None else None

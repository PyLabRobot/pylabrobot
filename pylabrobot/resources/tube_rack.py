from typing import ClassVar, Dict, Optional, Union

from .container_rack import ContainerRack
from .coordinate import Coordinate
from .resource import Resource
from .resource_holder import ResourceHolder
from .tube import Tube


class TubeRack(ContainerRack[Tube]):
  """A rack of tubes.

  Specialization of :class:`ContainerRack` that restricts slot contents to
  :class:`Tube` resources (statically via ``ContainerRack[Tube]`` and at
  runtime via ``_content_type``).
  """

  _content_type: ClassVar[type] = Tube

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, ResourceHolder]] = None,
    model: Optional[str] = None,
    category: str = "tube_rack",
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      ordered_items=ordered_items,
      category=category,
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

  def get_tube(self, key: Union[int, str]) -> Optional[Tube]:
    """Get the tube at the given position, or None if the position is empty."""
    if not self.has_container(key):
      return None
    return self.get_container(key)

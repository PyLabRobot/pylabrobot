from typing import Optional
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.utils import get_child_location


class ResourceHolderMixin:
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs
  ):
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      rotation=rotation,
      category=category,
      model=model,
      **kwargs
    )

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    location = get_child_location(resource) + (location or Coordinate.zero())
    return super().assign_child_resource(resource, location, reassign)
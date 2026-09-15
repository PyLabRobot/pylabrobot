from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder, get_child_location
from pylabrobot.resources.tip_rack import EmbeddedTipRack


class EmbeddedTipRackHolder(ResourceHolder):
  def __init__(
    self,
    name,
    size_x,
    size_y,
    size_z,
    rotation=None,
    category="embedded_tip_rack_holder",
    model=None,
    child_location: Coordinate = Coordinate.zero(),
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
      child_location=child_location,
    )

  def get_default_child_location(self, resource: Resource) -> Coordinate:
    if not isinstance(resource, EmbeddedTipRack):
      raise ValueError("Can only hold EmbeddedTipRack resources.")
    return get_child_location(resource) + Coordinate(x=0, y=0, z=-resource.sinking_depth)

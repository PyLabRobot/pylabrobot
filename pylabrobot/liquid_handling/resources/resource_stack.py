from typing import List, Optional

from pylabrobot.liquid_handling.resources.abstract.resource import Resource
from pylabrobot.liquid_handling.resources.abstract.coordinate import Coordinate


class ResourceStack(Resource):
  """ ResourceStack represent a group of resources that are stacked together and act as a single
  unit. Stacks can grow be configured to be able to grow in x, y, or z direction.  Stacks growing
  in the x direction are from left to right. Stacks growing in the y direction are from front to
  back. Stacks growing in the z direction are from top to bottom.

  Attributes:
    name: The name of the resource group.
    location: The location of the resource group. This will be the location of the first resource in
      the group.
    category: The category of the resource group.
    resources: The resources in the resource group.

  Examples:
    Making a resource group containing a plate on top of a lid:

    >>> stack = ResourceStack(“patched_plate”, "z", [
    ...   Resource("plate", size_x=1, size_y=1, size_z=10, location=Coordinate(0, 0, 0)),
    ...   Resource("lid", size_x=1, size_y=1, size_z=20, location=Coordinate(0, 0, 1))
    ... ])
    >>> stack.get_size_x()
    1
    >>> stack.get_size_z()
    30
  """

  def __init__(
    self,
    name: str,
    direction: str,
    resources: Optional[List[Resource]] = None,
    location: Coordinate = Coordinate(None, None, None),
  ):
    super().__init__(name, size_x=0, size_y=0, size_z=0,
      location=location, category="resource_group")
    assert direction in ["x", "y", "z"], "Direction must be one of 'x', 'y', or 'z'"
    self.direction = direction
    for resource in (resources or []):
      self.assign_child_resource(resource)

  def __str__(self) -> str:
    return f"ResourceGroup({self.name})"

  def get_size_x(self) -> float:
    if len(self.children) == 0:
      return 0
    if self.direction == "x":
      smallest_x = min(resource.location.x for resource in self.children)
      largest_x = max(resource.location.x + resource.get_size_x() for resource in self.children)
      return largest_x - smallest_x
    return max(resource.get_size_x() for resource in self.children)

  def get_size_y(self) -> float:
    if len(self.children) == 0:
      return 0
    if self.direction == "y":
      smallest_y = min(resource.location.y for resource in self.children)
      largest_y = max(resource.location.y + resource.get_size_y() for resource in self.children)
      return largest_y - smallest_y
    return max(resource.get_size_y() for resource in self.children)

  def get_size_z(self) -> float:
    if len(self.children) == 0:
      return 0
    if self.direction == "z":
      smallest_z = min(resource.location.z for resource in self.children)
      largest_z = max(resource.location.z + resource.get_size_z() for resource in self.children)
      return largest_z - smallest_z
    return max(resource.get_size_z() for resource in self.children)

  def assign_child_resource(self, resource, **kwargs):
    # update child location (relative to self): we place the child after the last child in the stack
    if self.direction == "x":
      resource.location += Coordinate(self.get_size_x(), 0, 0)
    elif self.direction == "y":
      resource.location += Coordinate(0, self.get_size_y(), 0)
    elif self.direction == "z":
      # top z > bottom z, so we need to move the resources down
      resource.location += Coordinate(0, 0, 0)
      for r in self.children:
        r.location.z += resource.get_size_z()

    super().assign_child_resource(resource, **kwargs)

from typing import List, Optional

from pylabrobot.resources.resource import Resource
from pylabrobot.resources.coordinate import Coordinate


class ResourceStack(Resource):
  """ ResourceStack represent a group of resources that are stacked together and act as a single
  unit. Stacks can grow be configured to be able to grow in x, y, or z direction. Stacks growing
  in the x direction are from left to right. Stacks growing in the y direction are from front to
  back. Stacks growing in the z direction are from bottom to top, and function as the
  `stack data type <https://en.wikipedia.org/wiki/Stack_(abstract_data_type)>`.

  Attributes:
    name: The name of the resource group.
    location: The location of the resource group. This will be the location of the first resource in
      the group.
    category: The category of the resource group.
    resources: The resources in the resource group.

  Examples:
    Making a resource group containing a plate on top of a lid:

    >>> stack = ResourceStack(“patched_plate”, "z", [
    ...   Resource("lid", size_x=1, size_y=1, size_z=20),
    ...   Resource("plate", size_x=1, size_y=1, size_z=10),
    ... ])
    >>> stack.get_size_x()
    1
    >>> stack.get_size_z()
    30

    :meth:`Moving <pyhamilton.liquid_handling.LiquidHandler.move_plate>` a plate to the a stacking
    area.

    >>> lh.move_plate(plate, stacking_area)

    :meth:`Moving <pyhamilton.liquid_handling.LiquidHandler.move_lid>` a lid to the stacking area.

    >>> lh.move_lid(plate.lid, stacking_area)

    Getting a plate from the stacking area and moving it to a :class:`~abstract.PlateCarrier`.

    >>> lh.move_plate(stacking_area.get_top_item(), plt_car[0])
  """

  def __init__(
    self,
    name: str,
    direction: str,
    resources: Optional[List[Resource]] = None,
  ):
    super().__init__(name, size_x=0, size_y=0, size_z=0, category="resource_group")
    assert direction in ["x", "y", "z"], "Direction must be one of 'x', 'y', or 'z'"
    self.direction = direction
    resources = resources or []
    if direction == "z": # top to bottom
      resources = list(reversed(resources))
    for resource in resources:
      self.assign_child_resource(resource)

  def __str__(self) -> str:
    return f"ResourceGroup({self.name})"

  def get_size_x(self) -> float:
    if len(self.children) == 0:
      return 0
    if self.direction == "x":
      return sum(child.get_size_x() for child in self.children)
    return max(resource.get_size_x() for resource in self.children)

  def get_size_y(self) -> float:
    if len(self.children) == 0:
      return 0
    if self.direction == "y":
      return sum(child.get_size_y() for child in self.children)
    return max(resource.get_size_y() for resource in self.children)

  def get_size_z(self) -> float:
    if len(self.children) == 0:
      return 0
    if self.direction == "z":
      return sum(child.get_size_z() for child in self.children)
    return max(child.get_size_z() for child in self.children)

  def assign_child_resource(self, resource):
    # update child location (relative to self): we place the child after the last child in the stack
    if self.direction == "x":
      resource_location = Coordinate(self.get_size_x(), 0, 0)
    elif self.direction == "y":
      resource_location = Coordinate(0, self.get_size_y(), 0)
    elif self.direction == "z":
      resource_location = Coordinate(0, 0, self.get_size_z())

    super().assign_child_resource(resource, location=resource_location)

  def unassign_child_resource(self, resource: Resource):
    if self.direction == "z" and resource != self.children[-1]: # no floating resources
      raise ValueError("Resource is not the top item in this z-growing stack, cannot unassign")
    return super().unassign_child_resource(resource)

  def get_top_item(self) -> Resource:
    """ Get the top item in the stack.

    For stacks growing in the x, y or z direction, this is the rightmost, frontmost, or topmost
    item in the stack, respectively.

    Returns:
      The top item in the stack.

    Raises:
      ValueError: If the stack is empty.
    """

    if len(self.children) == 0:
      raise ValueError("Stack is empty")

    return self.children[-1]

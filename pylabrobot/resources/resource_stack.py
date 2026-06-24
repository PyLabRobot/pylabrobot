import logging
from typing import List, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import get_child_location

logger = logging.getLogger("pylabrobot")


class ResourceStack(Resource):
  """ResourceStack represent a group of resources that are stacked together and act as a single
  unit. Stacks can grow be configured to be able to grow in x, y, or z direction. Stacks growing
  in the x direction are from left to right. Stacks growing in the y direction are from front to
  back. Stacks growing in the z direction are from bottom to top, and function as the
  `stack data type <https://en.wikipedia.org/wiki/Stack_(abstract_data_type)>`.

  When stacking in the z direction, bare plates nest into one another: if a plate defines a
  ``stacking_z_height`` (the vertical pitch it adds to a stack) and is placed directly on top of
  another bare plate, it sinks in by ``size_z - stacking_z_height`` instead of resting at the
  lower plate's full height. A stack of ``N`` identical such plates is therefore
  ``size_z + (N - 1) * stacking_z_height`` tall. Plates without a ``stacking_z_height``, and plates
  wearing a lid, do not nest.

  Attributes:
    name: The name of the resource group.
    location: The location of the resource group. This will be the location of the first resource in
      the group.
    category: The category of the resource group.
    resources: The resources in the resource group.

  Examples:
    Making a resource group containing two resources:

    >>> stack = ResourceStack(“patched_plate”, "z", [
    ...   Resource("plate1", size_x=1, size_y=1, size_z=10),
    ...   Resource("plate2", size_x=1, size_y=1, size_z=10),
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
    assert direction in [
      "x",
      "y",
      "z",
    ], "Direction must be one of 'x', 'y', or 'z'"
    self.direction = direction
    resources = resources or []
    if direction == "z":  # top to bottom
      resources = list(reversed(resources))
    for resource in resources:
      self.assign_child_resource(resource)

  def get_size_x(self) -> float:
    """Get local size in the x direction."""
    if len(self.children) == 0:
      return 0
    if self.direction == "x":
      return sum(child.get_size_x() for child in self.children)
    return max(resource.get_size_x() for resource in self.children)

  def get_size_y(self) -> float:
    """Get local size in the y direction."""
    if len(self.children) == 0:
      return 0
    if self.direction == "y":
      return sum(child.get_size_y() for child in self.children)
    return max(resource.get_size_y() for resource in self.children)

  @staticmethod
  def _actual_resource_height(resource: Resource) -> float:
    """The height a resource occupies on its own, accounting for the lid nesting height if the
    resource is a plate with a lid."""
    if isinstance(resource, Plate) and resource.lid is not None:
      return resource.get_size_z() + resource.lid.get_size_z() - resource.lid.nesting_z_height
    return resource.get_size_z()

  def _nesting_overlap(self, upper: Resource, lower: Optional[Resource]) -> float:
    """How far ``upper`` sinks into ``lower`` when stacked in the z direction (``0`` if they do not
    nest). Only a bare plate stacked on a bare plate with a known ``stacking_z_height`` nests; the
    overlap is then ``size_z - stacking_z_height`` (i.e. the plate adds only its stacking pitch to
    the stack instead of its full height)."""
    if (
      self.direction == "z"
      and isinstance(upper, Plate)
      and upper.stacking_z_height is not None
      and isinstance(lower, Plate)
      and lower.lid is None
    ):
      return upper.get_size_z() - upper.stacking_z_height
    return 0.0

  def get_size_z(self) -> float:
    """Get local size in the z direction."""

    if len(self.children) == 0:
      return 0

    if self.direction != "z":
      return max(self._actual_resource_height(child) for child in self.children)

    # Sum bottom -> top, letting bare plates nest into one another by their stacking pitch.
    total = 0.0
    prev: Optional[Resource] = None
    for child in self.children:
      total += self._actual_resource_height(child) - self._nesting_overlap(child, prev)
      prev = child
    return total

  def get_resource_stack_edge(self) -> Coordinate:
    if self.direction == "x":
      resource_location = Coordinate(self.get_size_x(), 0, 0)
    elif self.direction == "y":
      resource_location = Coordinate(0, self.get_size_y(), 0)
    elif self.direction == "z":
      resource_location = Coordinate(0, 0, self.get_size_z())
    else:
      raise ValueError("self.direction must be one of 'x', 'y', or 'z'")

    return resource_location

  def get_new_child_location(self, resource: Resource) -> Coordinate:
    """Get the location where a new child resource should be placed in the stack."""
    lower = self.children[-1] if len(self.children) > 0 else None
    overlap = Coordinate(0, 0, self._nesting_overlap(resource, lower))
    return get_child_location(resource) + self.get_resource_stack_edge() - overlap

  def assign_child_resource(
    self, resource: Resource, location: Optional[Coordinate] = None, reassign: bool = True
  ):
    location = location or self.get_new_child_location(resource)
    return super().assign_child_resource(resource=resource, location=location, reassign=reassign)

  def unassign_child_resource(self, resource: Resource):
    if self.direction == "z" and resource != self.children[-1]:  # no floating resources
      raise ValueError("Resource is not the top item in this z-growing stack, cannot unassign")
    return super().unassign_child_resource(resource)

  def get_top_item(self) -> Resource:
    """Get the top item in the stack.

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

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    # for now, any resource can be dropped onto a stack.
    pass

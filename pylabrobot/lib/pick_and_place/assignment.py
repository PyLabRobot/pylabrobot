"""Where the resource model puts a resource once it is placed."""

from typing import Union

from pylabrobot.lib.pick_and_place.locations import get_rotation_wrt, get_rotation_wrt_destination
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash


def assign_placed_resource(
  resource: Resource,
  destination: Union[Resource, Coordinate],
  deck: Resource,
  *,
  location: Coordinate,
  rotation_applied_by_move: float = 0,
) -> None:
  """Reassign `resource` into `destination` at `location`, where it was placed.

  `location` is what `get_place_location` aimed it at, so the model seats it exactly where the
  move put it. A `Trash` discards it; a `Coordinate` puts it on the deck; a stack adds it on top.
  The caller checks the destination first, with `check_can_drop_resource_here` and
  `get_place_location`, before any motion: nothing is checked here.

  Args:
    resource: the resource placed.
    destination: the resource it goes into, or its origin's location in `deck`'s frame.
    deck: the frame `location` is in, and what a `Coordinate` puts it on.
    location: where its origin was placed in `deck`'s frame, from `get_place_location`.
    rotation_applied_by_move: how far the move turned it about z, in degrees.
  """
  rotation = get_rotation_wrt_destination(resource, destination, deck, rotation_applied_by_move)
  # `rotate` turns on top of its rotation, so the one it has is taken off.
  resource.rotate(z=rotation - resource.rotation.z)
  resource.unassign()
  if isinstance(destination, Coordinate):
    deck.assign_child_resource(resource, location=location)
  elif isinstance(destination, Trash):
    # Discarded: `unassign` above leaves it out of the tree.
    pass
  elif isinstance(destination, ResourceStack):
    # A stack keeps its own order, so it adds the resource on top itself.
    destination.assign_child_resource(resource)
  else:
    # Where the move put it, along the destination's own axes.
    frame = get_rotation_wrt(destination, deck)
    child = (location - destination.get_location_wrt(deck)).rotated(
      Rotation(x=-frame.x, y=-frame.y, z=-frame.z)
    )
    destination.assign_child_resource(resource, location=child)

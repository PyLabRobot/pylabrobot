"""Where a resource is picked up from and placed at, and where a gripper holds it."""

from typing import Optional, Union

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.lid import Lid, Liddable
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash


def get_rotation_wrt(resource: Resource, other: Resource) -> Rotation:
  """`resource`'s rotation along `other`'s axes, as `get_location_wrt` gives its location.

  Args:
    resource: the resource whose rotation is wanted.
    other: the resource whose axes it is expressed along.
  """
  turned, frame = resource.get_absolute_rotation(), other.get_absolute_rotation()
  return Rotation(x=turned.x - frame.x, y=turned.y - frame.y, z=turned.z - frame.z)


def get_pick_location(resource: Resource, deck: Resource) -> Coordinate:
  """Where `resource`'s origin is in `deck`'s frame, as it is picked up.

  Args:
    resource: the resource picked up.
    deck: the frame the location is in.

  Raises:
    ValueError: If `resource` is not on `deck`.
  """
  return resource.get_location_wrt(deck)


def get_rotation_wrt_destination(
  resource: Resource,
  destination: Union[Resource, Coordinate],
  deck: Resource,
  rotation_applied_by_move: float = 0,
) -> float:
  """The z rotation `resource` has relative to `destination` once placed there, in degrees.

  Args:
    resource: the resource placed.
    destination: where it is placed; a `Coordinate` is along `deck`'s axes.
    deck: the frame a `Coordinate` is in.
    rotation_applied_by_move: how far the move turns it about z, in degrees.
  """
  frame = deck if isinstance(destination, Coordinate) else destination
  return get_rotation_wrt(resource, frame).z + rotation_applied_by_move


def get_place_location(
  resource: Resource,
  destination: Union[Resource, Coordinate],
  deck: Resource,
  *,
  rotation_applied_by_move: float = 0,
) -> Coordinate:
  """Where `resource`'s origin goes in `deck`'s frame when placed into `destination`.

  Each destination places it as it seats a child itself, turned by the move. A `Coordinate` is
  the location itself; a `Trash` and any other resource take it at their own origin. The caller
  checks first that `destination` takes it, with `check_can_drop_resource_here`.

  Args:
    resource: the resource placed.
    destination: the resource it goes into, or its origin's location in `deck`'s frame.
    deck: the frame the location is in.
    rotation_applied_by_move: how far the move turns it about z, in degrees.

  Raises:
    ValueError: If a stack grows other than in z or is turned to by other than a multiple of 180
      degrees, anything but a plate goes onto a plate adapter, or `destination` is not on `deck`.
  """
  if isinstance(destination, Coordinate):
    return destination
  rotation = get_rotation_wrt_destination(resource, destination, deck, rotation_applied_by_move)
  # A child is seated by its own rotation, so the copy carries the one it will have there.
  turn = rotation - resource.rotation.z
  turned = resource if turn == 0 else resource.rotated(z=turn)
  # Along the deck's axes, not absolute: the device carrying the deck may itself be turned.
  frame = get_rotation_wrt(destination, deck)
  if isinstance(destination, ResourceStack):
    if destination.direction != "z":
      raise ValueError(
        f"stack '{destination.name}' grows in '{destination.direction}': a resource is placed only "
        "onto a stack that grows in 'z'"
      )
    if rotation % 180 != 0:
      raise ValueError(
        f"'{resource.name}' would be turned {rotation} degrees against stack '{destination.name}': "
        "a stack takes it only turned by a multiple of 180 degrees"
      )
    child = destination.get_new_child_location(turned).rotated(frame)
  elif isinstance(destination, Trash):
    child = Coordinate.zero()
  elif isinstance(destination, ResourceHolder):
    child = destination.get_default_child_location(turned).rotated(frame)
  elif isinstance(destination, PlateAdapter):
    if not isinstance(turned, Plate):
      raise ValueError(
        f"'{resource.name}' is not a plate: only a plate goes onto plate adapter "
        f"'{destination.name}'"
      )
    child = destination.compute_plate_location(turned).rotated(frame)
  elif isinstance(destination, Liddable) and isinstance(turned, Lid):
    child = destination.get_lid_location(turned).rotated(frame)
  else:
    child = Coordinate.zero()
  return destination.get_location_wrt(deck) + child


def get_grip_height(
  resource: Resource,
  *,
  pickup_distance_from_top: Optional[float] = None,
  pickup_distance_from_bottom: Optional[float] = None,
) -> float:
  """How far above `resource`'s bottom the fingers close on it, in mm.

  Exactly one of the two distances is given; the other stays None.

  Args:
    resource: the resource gripped.
    pickup_distance_from_top: how far below its top the fingers close, in mm.
    pickup_distance_from_bottom: how far above its bottom the fingers close, in mm.

  Raises:
    ValueError: If neither or both are given, or the grip lies outside the resource's height.
  """
  size_z = resource.get_absolute_size_z()
  if pickup_distance_from_top is not None and pickup_distance_from_bottom is None:
    grip_height = size_z - pickup_distance_from_top
  elif pickup_distance_from_bottom is not None and pickup_distance_from_top is None:
    grip_height = pickup_distance_from_bottom
  else:
    raise ValueError(
      "exactly one of pickup_distance_from_top and pickup_distance_from_bottom must be given, "
      f"got {pickup_distance_from_top} and {pickup_distance_from_bottom}"
    )
  if not 0 <= grip_height <= size_z:
    raise ValueError(
      f"the grip must lie within {resource.name}'s height, 0 to {size_z} mm above its bottom, "
      f"is {grip_height} mm"
    )
  return grip_height


def get_grip_location(
  resource: Resource, location: Coordinate, deck: Resource, grip_height: float
) -> Coordinate:
  """`resource`'s centre at `grip_height` in `deck`'s frame, with its origin at `location`.

  Args:
    resource: the resource gripped.
    location: its origin in `deck`'s frame, from `get_pick_location` or `get_place_location`.
    deck: the frame the locations are in.
    grip_height: how far above its bottom the fingers close, in mm, from `get_grip_height`.
  """
  center = resource.center().rotated(get_rotation_wrt(resource, deck))
  grip = location + center
  return Coordinate(grip.x, grip.y, grip.z + grip_height)

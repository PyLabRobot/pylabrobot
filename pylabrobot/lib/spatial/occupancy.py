"""Where resources are in the tree's space: which one a point falls into."""

import math
from typing import Collection, List, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


def get_resource_at_location(
  location: Coordinate,
  reference_frame: Resource,
  exclude: Collection[Resource] = (),
) -> Optional[Resource]:
  """The deepest resource under `reference_frame` whose bounding box holds `location`.

  A point inside a plate on a carrier site is the plate; inside the carrier beside it, the carrier
  or its site. A box holds its left, front and bottom faces but not its right, back and top ones,
  so a point on the deck, or resting on top of something, is in nothing.

  This is a bounding-box lookup, not collision detection: each resource is its axis-aligned
  bounding box in `reference_frame`, whatever its shape. A resource rotated by a multiple of 90
  degrees about z is exactly its rotated footprint; any other rotation covers the whole box around
  it, so a point just outside the resource can still be reported as inside it.

  Args:
    location: the point, in mm, from `reference_frame`'s left front bottom corner, along the same
      axes as :meth:`Resource.get_location_wrt`.
    reference_frame: the resource `location` is measured from, whose subtree is searched, e.g. a
      deck.
    exclude: resources to leave out, with everything under them, e.g. what a moving arm carries.

  Returns:
    The resource, or None if the point falls into none.
  """

  def count_parents_between(resource: Resource, ancestor: Resource) -> int:
    """How many parents lie between `resource` and `ancestor`: 0 for a child of `ancestor`."""
    count, current = 0, resource.parent
    while current is not None and current is not ancestor:
      count, current = count + 1, current.parent
    return count

  def is_location_inside(resource: Resource) -> bool:
    """Whether `location` is in `resource`'s bounding box, right, back and top faces excluded."""
    corners: List[Coordinate] = [
      resource.get_location_wrt(reference_frame, x, y, z)
      for x in ("l", "r")
      for y in ("f", "b")
      for z in ("b", "t")
    ]
    return (
      min(c.x for c in corners) <= location.x < max(c.x for c in corners)
      and min(c.y for c in corners) <= location.y < max(c.y for c in corners)
      and min(c.z for c in corners) <= location.z < max(c.z for c in corners)
    )

  found: Optional[Resource] = None
  found_parents = -1
  for resource in reference_frame.get_all_children():
    if any(resource.is_in_subtree_of(excluded) for excluded in exclude):
      continue
    if not is_location_inside(resource):
      continue
    parents = count_parents_between(resource, reference_frame)
    if parents > found_parents:
      found, found_parents = resource, parents
  return found


def get_resource_below_location(
  location: Coordinate,
  reference_frame: Resource,
  exclude: Collection[Resource] = (),
) -> Optional[Resource]:
  """The resource a point descending from `location` is in or meets first.

  Of the resources whose footprint holds the point's x and y and whose bottom is at or below it,
  only the deepest of each branch counts: a plate on a site is the plate, a well in it the well.
  Of those, the one with the highest top is met: one that holds the point outranks any below it.
  Footprints follow :func:`get_resource_at_location`: left and front faces in, right and back ones
  out, bounding boxes in `reference_frame`.

  Args:
    location: the point, in mm, from `reference_frame`'s left front bottom corner, along the same
      axes as :meth:`Resource.get_location_wrt`.
    reference_frame: the resource `location` is measured from, whose subtree is searched, e.g. a
      deck.
    exclude: resources to leave out, with everything under them, e.g. what a moving arm carries.

  Returns:
    The resource, or None if nothing is at or below the point.
  """

  def get_corners(resource: Resource) -> List[Coordinate]:
    """The corners of `resource`'s bounding box in `reference_frame`."""
    return [
      resource.get_location_wrt(reference_frame, x=x, y=y, z=z)
      for x in ("l", "r")
      for y in ("f", "b")
      for z in ("b", "t")
    ]

  holding: List[Tuple[Resource, float]] = []
  for resource in reference_frame.get_all_children():
    if any(resource.is_in_subtree_of(other=excluded) for excluded in exclude):
      continue
    # No corner lies farther from the origin corner than the box's diagonal.
    origin = resource.get_location_wrt(reference_frame)
    diagonal = math.hypot(resource.get_size_x(), resource.get_size_y(), resource.get_size_z())
    if math.hypot(location.x - origin.x, location.y - origin.y) > diagonal:
      continue
    corners = get_corners(resource)
    if (
      min(c.x for c in corners) <= location.x < max(c.x for c in corners)
      and min(c.y for c in corners) <= location.y < max(c.y for c in corners)
      and min(c.z for c in corners) <= location.z
    ):
      holding.append((resource, max(c.z for c in corners)))
  deepest = [
    (resource, top)
    for resource, top in holding
    if not any(
      other is not resource and other.is_in_subtree_of(other=resource) for other, _ in holding
    )
  ]
  if not deepest:
    return None
  return max(deepest, key=lambda held: held[1])[0]

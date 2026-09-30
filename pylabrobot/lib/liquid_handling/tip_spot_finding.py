"""Select tip spots from tracked tip inventory without changing trackers."""

from itertools import groupby
from typing import List, Optional, Tuple

from pylabrobot.resources import Coordinate, Resource, Rotation, TipRack, TipSpot
from pylabrobot.resources.errors import NoLocationError
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3


def _matches_tip_filters(
  tip_spot: TipSpot,
  has_tip: Optional[bool],
  volume: Optional[float],
  has_filter: Optional[bool],
) -> bool:
  """Whether a tip spot passes the presence, volume and filter checks.

  Volume and filter are read from the configured tip (make_tip) when has_tip is False, and from
  the present tip otherwise, so an empty spot never matches them unless has_tip is False.
  """
  if has_tip is not None and tip_spot.has_tip() != has_tip:
    return False
  if volume is None and has_filter is None:
    return True
  if has_tip is False:
    tip = tip_spot.make_tip()
  elif tip_spot.has_tip():
    tip = tip_spot.get_tip()
  else:
    return False
  if volume is not None and tip.nominal_volume != volume:
    return False
  return has_filter is None or tip.has_filter == has_filter


Frame = Tuple[Coordinate, List[List[float]]]
"""A resource's origin and rotation matrix, in the root's frame."""


def _get_tip_racks(root: Resource) -> List[Tuple[TipRack, Frame]]:
  """Tip racks under root, root included, depth first, each with its frame in root's frame.

  The frames are accumulated on the way down, as `get_absolute_location` does from the top of the
  tree, so every rack costs one step of the walk. Racks are not descended into.

  Raises:
    NoLocationError: If a resource between root and a rack has no location.
  """
  racks: List[Tuple[TipRack, Frame]] = []

  def walk(resource: Resource, origin: Coordinate, rotation: Rotation) -> None:
    matrix = rotation.get_rotation_matrix()
    if isinstance(resource, TipRack):
      racks.append((resource, (origin, matrix)))
      return
    for child in resource.children:
      if child.location is None:
        raise NoLocationError(f"Resource '{child.name}' has no location.")
      offset = Coordinate(*matrix_vector_multiply_3x3(matrix, child.location.vector()))
      walk(child, origin + offset, rotation + child.rotation)

  walk(root, Coordinate.zero(), Rotation())
  return racks


def _get_centred_spots(frame: Frame, spots: List[TipSpot]) -> List[Tuple[TipSpot, float, float]]:
  """Each spot with its centre x (rounded to 0.001 mm) and y in the root's frame."""
  origin, matrix = frame
  centred: List[Tuple[TipSpot, float, float]] = []
  for spot in spots:
    assert spot.location is not None
    anchor = spot.get_anchor(x="c", y="c")
    if spot.rotation.x or spot.rotation.y or spot.rotation.z:
      anchor = Coordinate(
        *matrix_vector_multiply_3x3(spot.rotation.get_rotation_matrix(), anchor.vector())
      )
    local = spot.location + anchor
    x = origin.x + matrix[0][0] * local.x + matrix[0][1] * local.y + matrix[0][2] * local.z
    y = origin.y + matrix[1][0] * local.x + matrix[1][1] * local.y + matrix[1][2] * local.z
    centred.append((spot, round(x, 3), y))
  return centred


def _get_rack_rank(rack: TipRack, frame: Frame, index: int) -> Tuple[bool, int, float, float, int]:
  """Sort key for racks: racks with an empty spot first, then fewest tips left, left-most, rear-most.

  Position is the rack's centre in the root's frame; `index` (tree order) only separates racks at
  the same position.
  """
  spots = rack.get_all_items()
  tips_left = sum(spot.has_tip() for spot in spots)
  origin, matrix = frame
  centre = rack.get_anchor(x="c", y="c")
  x = origin.x + matrix[0][0] * centre.x + matrix[0][1] * centre.y + matrix[0][2] * centre.z
  y = origin.y + matrix[1][0] * centre.x + matrix[1][1] * centre.y + matrix[1][2] * centre.z
  return (tips_left == len(spots), tips_left, round(x, 3), -y, index)


def find_tip_spots(
  root: Resource,
  has_tip: Optional[bool] = None,
  volume: Optional[float] = None,
  has_filter: Optional[bool] = None,
  count: Optional[int] = None,
  x_aligned: bool = False,
) -> List[TipSpot]:
  """Find tip spots in consumption order.

  Searches every tip rack in the tree under root, root included: a deck, a carrier, a bench
  resource holding several racks, a whole facility, or a single rack. Racks with an empty spot
  come first; among them, and then among full racks, the one with the fewest tips left, then the
  left-most, then the rear-most. Within a rack, spots run left to right, each column back to
  front, so a rack is opened at its left-most column. This suits channels on one X arm that
  cannot pass each other.

  Args:
    root: Resource whose tree is searched for tip racks. Positions are compared in root's own
      frame, so a deck gives the channels' order however the device sits above it.
    has_tip: True for spots holding a tip, False for empty spots, None for both.
    volume: Tip nominal volume in uL. Read from the present tip, or from the configured tip when
      has_tip is False; with has_tip None, only spots holding a tip match.
    has_filter: Tip filter state, matched like volume.
    count: Return exactly this many spots, sorted back to front, or an empty list if fewer
      match. None returns every match.
    x_aligned: Return spots from a single rack column: the first column in consumption order
      holding at least count spots. A shorter column is skipped. Falls back to the unaligned
      batch when no column can serve count. Without count, returns the first column.

  Returns:
    Matching tip spots.

  Raises:
    ValueError: If count is not positive.
    NoLocationError: If a resource between root and a rack has no location.
  """
  if count is not None and count <= 0:
    raise ValueError(f"count must be positive, got {count}")

  racks = _get_tip_racks(root)

  rack_order = sorted(range(len(racks)), key=lambda index: _get_rack_rank(*racks[index], index))

  # Racks are visited in consumption order, so a batch is complete as soon as it is found.
  batch: List[Tuple[TipSpot, float, float]] = []
  for rack_index in rack_order:
    rack, frame = racks[rack_index]
    matching = [
      s for s in rack.get_all_items() if _matches_tip_filters(s, has_tip, volume, has_filter)
    ]
    centred = sorted(_get_centred_spots(frame, matching), key=lambda item: (item[1], -item[2]))

    if x_aligned:
      for _, column_items in groupby(centred, key=lambda item: item[1]):
        column = [spot for spot, _, _ in column_items]
        if count is None:
          return column
        if len(column) >= count:
          return column[:count]

    batch.extend(centred)
    if count is not None and len(batch) >= count and not x_aligned:
      break

  if count is None:
    return [spot for spot, _, _ in batch]
  if len(batch) < count:
    return []
  # A batch crossing from one rack's last column into the next rack's first is not back to front.
  return [spot for spot, _, _ in sorted(batch[:count], key=lambda item: item[2], reverse=True)]

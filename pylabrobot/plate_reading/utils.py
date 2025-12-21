from typing import Iterable, List, Tuple

from pylabrobot.resources import Plate, Well


def _non_overlapping_rectangles(
  points: Iterable[Tuple[int, int]],
) -> List[Tuple[int, int, int, int]]:
  """Find non-overlapping rectangles that cover all given points.

  Example:
    >>> points = [
    >>>   (1, 1),
    >>>   (2, 2), (2, 3), (2, 4),
    >>>   (3, 2), (3, 3), (3, 4),
    >>>   (4, 2), (4, 3), (4, 4), (4, 5),
    >>>   (5, 2), (5, 3), (5, 4), (5, 5),
    >>>   (6, 2), (6, 3), (6, 4), (6, 5),
    >>>   (7, 2), (7, 3), (7, 4),
    >>> ]
    >>> non_overlapping_rectangles(points)
    [
      (1, 1, 1, 1),
      (2, 2, 7, 4),
      (4, 5, 6, 5),
    ]
  """

  pts = set(points)
  rects = []

  while pts:
    # start a rectangle from one arbitrary point
    r0, c0 = min(pts)
    # expand right
    c1 = c0
    while (r0, c1 + 1) in pts:
      c1 += 1
    # expand downward as long as entire row segment is filled
    r1 = r0
    while all((r1 + 1, c) in pts for c in range(c0, c1 + 1)):
      r1 += 1

    rects.append((r0, c0, r1, c1))
    # remove covered points
    for r in range(r0, r1 + 1):
      for c in range(c0, c1 + 1):
        pts.discard((r, c))

  rects.sort()
  return rects


def _get_min_max_row_col_tuples(wells: List[Well], plate: Plate) -> List[Tuple[int, int, int, int]]:
  """Get a list of (min_row, min_col, max_row, max_col) tuples for the given wells."""
  plates = set(well.parent for well in wells)
  if len(plates) != 1 or plates.pop() != plate:
    raise ValueError("All wells must be in the specified plate")
  return _non_overlapping_rectangles((well.get_row(), well.get_column()) for well in wells)

"""The 3x3 deck grid model.

Provides spatial queries over the nine deck locations: adjacency, bounding
regions, and inter-location distance scoring.
"""

from __future__ import annotations

from ..types import (
  MAX_COLS,
  MAX_ROWS,
  location_to_row_col,
  row_col_to_location,
)


class DeckLayout:
  """Spatial model for the 3x3 deck grid (locations 1-9).

  Location numbering::

      1  2  3      row 0
      4  5  6      row 1
      7  8  9      row 2
  """

  @staticmethod
  def get_row_col(location: int) -> tuple[int, int]:
    """Return the ``(row, col)`` tuple for a 1-based location."""
    return location_to_row_col(location)

  @staticmethod
  def get_location(row: int, col: int) -> int:
    """Return the 1-based location for a ``(row, col)`` pair."""
    return row_col_to_location(row, col)

  @staticmethod
  def get_adjacent_locations(location: int) -> list[int]:
    """Return all locations neighbouring *location* (including diagonals)."""
    row, col = location_to_row_col(location)
    neighbours: list[int] = []
    for dr in (-1, 0, 1):
      for dc in (-1, 0, 1):
        if dr == 0 and dc == 0:
          continue
        nr, nc = row + dr, col + dc
        if 0 <= nr < MAX_ROWS and 0 <= nc < MAX_COLS:
          neighbours.append(row_col_to_location(nr, nc))
    return neighbours

  @staticmethod
  def get_region(
    start_locations: list[int],
    end_locations: list[int],
  ) -> set[int]:
    """Return the set of locations inside the bounding rectangle.

    The bounding rectangle is the smallest rectangle that encloses all
    *start_locations* and *end_locations*.
    """
    all_locs = start_locations + end_locations
    if not all_locs:
      return set()

    rows_cols = [location_to_row_col(loc) for loc in all_locs]
    min_row = min(r for r, _ in rows_cols)
    max_row = max(r for r, _ in rows_cols)
    min_col = min(c for _, c in rows_cols)
    max_col = max(c for _, c in rows_cols)

    region: set[int] = set()
    for r in range(min_row, max_row + 1):
      for c in range(min_col, max_col + 1):
        region.add(row_col_to_location(r, c))
    return region

  @staticmethod
  def get_distance(loc_a: int, loc_b: int) -> int:
    """Return a proximity score (0-6) between two locations.

    Scoring follows the CConcurrentLocationManager scale:
    - 0: same location
    - 1: horizontally adjacent (same row, +-1 col)
    - 2: vertically adjacent (same col, +-1 row)
    - 3: diagonally adjacent
    - 4: two steps in one axis
    - 5: two in one axis + one in the other
    - 6: opposite corners (max distance)
    """
    if loc_a == loc_b:
      return 0
    ra, ca = location_to_row_col(loc_a)
    rb, cb = location_to_row_col(loc_b)
    dr = abs(ra - rb)
    dc = abs(ca - cb)

    if dr == 0 and dc == 1:
      return 1
    if dr == 1 and dc == 0:
      return 2
    if dr == 1 and dc == 1:
      return 3
    if (dr == 0 and dc == 2) or (dr == 2 and dc == 0):
      return 4
    if (dr == 2 and dc == 1) or (dr == 1 and dc == 2):
      return 5
    return 6

"""Translate PyLabRobot items into an Agilent Bravo head block.

Every operation the Bravo pipetting head can perform -- a single barrel, a
full row or column, a rectangle, or the whole head -- is a contiguous
rectangular block of barrels anchored at one of the head's four corners
(see :mod:`.head_mode`). PyLabRobot identifies the wells or tip spots a
caller wants to work with as a set of resources inside an
:class:`~pylabrobot.resources.itemized_resource.ItemizedResource` (a
:class:`~pylabrobot.resources.plate.Plate` or
:class:`~pylabrobot.resources.tip_rack.TipRack`), each carrying a string
identifier such as ``"A1"`` from
:meth:`~pylabrobot.resources.itemized_resource.ItemizedResource.get_child_identifier`.

This module bridges the two: given the identifiers a caller selected, it
either returns the single rectangular :class:`HeadBlock` they describe, or
raises :class:`HeadBlockError` explaining why they cannot be one and what
selection would work instead. Whether that block's *shape* fits a
particular installed head -- and which of the head's four corners it
should be anchored at -- is a further decision this module deliberately
leaves to its caller (see :class:`HeadBlock.fits_within`), since it has no
notion of installed hardware at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from pylabrobot.resources.utils import label_to_row_index, row_index_to_label, split_identifier

_MAX_LISTED_IDENTIFIERS = 8
"""Cap on how many identifiers an error message spells out by name, past
which it falls back to a count -- so a rejection on a 384-position
selection stays readable instead of dumping the whole list."""


class HeadBlockError(ValueError):
  """A set of items cannot be translated into a single Bravo head block."""


@dataclass(frozen=True)
class HeadBlock:
  """A contiguous rectangular block of head barrels.

  Row/column bounds are zero-based and half-open, in the same (row, col)
  frame :func:`parse_item_identifier` produces: row 0 is an item grid's
  first row ("A"), column 0 its first column ("1"). Always the output of
  :func:`head_block_for_identifiers` -- never construct one directly.

  Attributes:
    row_start: First active row.
    row_stop: One past the last active row.
    col_start: First active column.
    col_stop: One past the last active column.
  """

  row_start: int
  row_stop: int
  col_start: int
  col_stop: int

  @property
  def num_rows(self) -> int:
    """Number of active rows."""
    return self.row_stop - self.row_start

  @property
  def num_columns(self) -> int:
    """Number of active columns."""
    return self.col_stop - self.col_start

  @property
  def num_barrels(self) -> int:
    """Total number of active barrels."""
    return self.num_rows * self.num_columns

  def fits_within(self, rows: int, columns: int) -> bool:
    """Return whether this block fits within a *rows* x *columns* head.

    Args:
      rows: Number of barrel rows the head has.
      columns: Number of barrel columns the head has.

    Returns:
      True if this block's own row count and column count are each no
      larger than the head's.
    """
    return self.num_rows <= rows and self.num_columns <= columns


def parse_item_identifier(identifier: str) -> Tuple[int, int]:
  """Parse an item identifier like ``"A1"`` into a zero-based (row, col) pair.

  Args:
    identifier: A transposed Excel-style identifier, e.g. ``"A1"`` for the
      first row/column, or ``"AF48"`` for row 32 of a 1536-position head.

  Returns:
    The zero-based ``(row, col)`` position the identifier names.

  Raises:
    HeadBlockError: If *identifier* is not a valid item identifier -- not
      in ``<letters><digits>`` form, or with a row label longer than two
      letters.
  """
  try:
    row_label, col_label = split_identifier(identifier)
    return label_to_row_index(row_label), int(col_label) - 1
  except ValueError as exc:
    raise HeadBlockError(f"'{identifier}' is not a valid item identifier: {exc}") from exc


def _identifier(row: int, col: int) -> str:
  """Return the item identifier for a zero-based (row, col) position."""
  return f"{row_index_to_label(row)}{col + 1}"


def _format_identifier_list(cells: List[Tuple[int, int]]) -> str:
  """Format (row, col) cells as a readable, length-capped identifier list."""
  identifiers = [_identifier(row, col) for row, col in cells]
  if len(identifiers) <= _MAX_LISTED_IDENTIFIERS:
    return ", ".join(identifiers)
  shown = identifiers[:_MAX_LISTED_IDENTIFIERS]
  remaining = len(identifiers) - _MAX_LISTED_IDENTIFIERS
  return f"{', '.join(shown)}, and {remaining} more"


def head_block_for_identifiers(identifiers: Iterable[str]) -> HeadBlock:
  """Return the :class:`HeadBlock` a set of item identifiers forms.

  Computes the bounding box of every identifier and checks that every
  position inside that box was also selected -- the only way a set of grid
  positions can be a contiguous rectangle. This single check catches an
  L-shape, a diagonal pair, and a rectangle with a hole in it alike: each
  leaves at least one bounding-box position unselected.

  Args:
    identifiers: The item identifiers selected, e.g. ``["A1", "B1", "C1"]``
      for a 3-row column. Duplicates are ignored; order does not matter.

  Returns:
    The block covering every given identifier.

  Raises:
    HeadBlockError: If *identifiers* is empty, contains an invalid
      identifier (see :func:`parse_item_identifier`), or does not describe
      a contiguous rectangle.
  """
  ids = list(identifiers)
  if not ids:
    raise HeadBlockError(
      "No items were selected; the Bravo head needs at least one active barrel to operate."
    )
  cells = {parse_item_identifier(identifier) for identifier in ids}
  rows = [row for row, _ in cells]
  cols = [col for _, col in cells]
  row_start, row_stop = min(rows), max(rows) + 1
  col_start, col_stop = min(cols), max(cols) + 1
  expected = {
    (row, col) for row in range(row_start, row_stop) for col in range(col_start, col_stop)
  }
  missing = sorted(expected - cells)
  if missing:
    bounding_box = f"{_identifier(row_start, col_start)}:{_identifier(row_stop - 1, col_stop - 1)}"
    raise HeadBlockError(
      "The selected items do not form a contiguous rectangular block: they span "
      f"{bounding_box} ({len(expected)} positions) but only {len(cells)} were selected, "
      f"missing {_format_identifier_list(missing)}. Select every item in {bounding_box}, "
      "or a smaller selection that is itself a complete rectangle."
    )
  return HeadBlock(row_start=row_start, row_stop=row_stop, col_start=col_start, col_stop=col_stop)

"""Head-mode geometry: which barrels of the pipetting head are active, and where.

The Bravo head is a fixed rectangular grid of barrels (8x1, 16x1, 8x12, or
16x24 depending on the installed :class:`~pylabrobot.agilent.bravo.types.HeadType`).
Every operation the head performs — full-plate aspirate, a single column,
one barrel — is a contiguous rectangular block of that grid anchored at one
of its four corners (``back_left``, ``back_right``, ``front_left``,
``front_right``). This module is the single source of truth for that
geometry: normalising a caller's requested subset into a concrete
:class:`HeadMode`, computing which barrels are active and where they sit
relative to a tipbox or plate, and enumerating which tipbox/plate anchor
positions are physically reachable given tips or wells already consumed.

Row 0 is the physical back row and column 0 is the physical left column,
matching the deck's coordinate frame; "front"/"back" and "left"/"right" in
this module always refer to that physical orientation, not to array index
direction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Union

from .types import HeadType, head_type_channels

_FRONT_ORIENTATIONS = {"front_left", "front_right"}
_LEFT_ORIENTATIONS = {"front_left", "back_left"}

# 384-family heads: on a 384-pitch plate the barrel-to-well mapping is 1:1,
# so plate-phase counting treats them differently from the 96-family heads.
_HEAD_384_FAMILY = frozenset(
  {
    "384_d_70",
    "384_d_70_s2",
    "384_f_50",
    "384_pintool",
  }
)


@dataclass(frozen=True)
class HeadGeometry:
  """The physical barrel grid of an installed head.

  Attributes:
    rows: Number of barrel rows.
    columns: Number of barrel columns.
    pitch_x_mm: Barrel-to-barrel spacing along columns, in millimetres.
    pitch_y_mm: Barrel-to-barrel spacing along rows, in millimetres.
  """

  rows: int
  columns: int
  pitch_x_mm: float
  pitch_y_mm: float


@dataclass(frozen=True)
class HeadMode:
  """A normalised description of which head barrels are active.

  Always the output of :func:`normalize_head_mode` — never construct one
  directly, since ``row_count``/``column_count`` must already be clamped to
  the installed head's geometry for the rest of this module to behave
  correctly.

  Attributes:
    subset_type: One of ``"all_barrels"``, ``"row"``, ``"column"``,
      ``"rectangle"``, or ``"single_barrel"``.
    subset_config: The anchor corner: ``"front_left"``, ``"front_right"``,
      ``"back_left"``, or ``"back_right"``.
    row_count: Number of active barrel rows.
    column_count: Number of active barrel columns.
  """

  subset_type: str = "all_barrels"
  subset_config: str = "front_left"
  row_count: int = 0
  column_count: int = 0

  @property
  def num_channels(self) -> int:
    """Return the number of active barrels."""
    return int(self.row_count) * int(self.column_count)

  def to_dict(self) -> dict[str, object]:
    """Return this mode as a plain dict, for logging or serialization.

    Returns:
      The dataclass fields plus ``num_channels`` and a human-readable
      ``display_text``.
    """
    data = asdict(self)
    data["num_channels"] = self.num_channels
    data["display_text"] = describe_head_mode(self)
    return data


@dataclass(frozen=True)
class TipSelection:
  """A tipbox anchor position paired with the head mode picking from it.

  Attributes:
    location: The deck location of the tipbox.
    row: Zero-based row of the anchor cell in the tipbox.
    col: Zero-based column of the anchor cell in the tipbox.
    row_count: Number of tipbox rows the selection spans.
    column_count: Number of tipbox columns the selection spans.
    mirror_corner: The tipbox corner the selection is measured from.
    head_anchor: The head corner that aligns with the tipbox anchor cell.
  """

  location: int
  row: int
  col: int
  row_count: int = 1
  column_count: int = 1
  mirror_corner: str = "back_left"
  head_anchor: str = "back_left"

  def to_dict(self) -> dict[str, Union[int, str]]:
    """Return this selection as a plain dict, including the resolved anchor cell.

    Returns:
      The dataclass fields plus ``anchor_row``/``anchor_col``, the tipbox
      cell that aligns with the active head anchor.
    """
    anchor_row, anchor_col = tipbox_anchor_cell(self)
    return {
      "location": self.location,
      "row": self.row,
      "col": self.col,
      "row_count": self.row_count,
      "column_count": self.column_count,
      "mirror_corner": self.mirror_corner,
      "head_anchor": self.head_anchor,
      "anchor_row": anchor_row,
      "anchor_col": anchor_col,
    }


@dataclass(frozen=True)
class PlateSelection:
  """A single anchor cell on a plate.

  Attributes:
    location: The deck location of the plate.
    row: Zero-based row of the anchor well.
    col: Zero-based column of the anchor well.
  """

  location: int
  row: int
  col: int

  def to_dict(self) -> dict[str, int]:
    """Return this selection as a plain dict.

    Returns:
      The dataclass fields as a dict.
    """
    return {
      "location": self.location,
      "row": self.row,
      "col": self.col,
    }


@dataclass(frozen=True)
class TipAnchor:
  """A legal tipbox anchor position, without a specific deck location.

  Attributes:
    row: Zero-based row of the anchor cell in the tipbox.
    col: Zero-based column of the anchor cell in the tipbox.
    row_count: Number of tipbox rows the selection spans.
    column_count: Number of tipbox columns the selection spans.
    mirror_corner: The tipbox corner the selection is measured from.
    head_anchor: The head corner that aligns with the tipbox anchor cell.
  """

  row: int
  col: int

  row_count: int
  column_count: int
  mirror_corner: str
  head_anchor: str = "back_left"

  def to_dict(self) -> dict[str, Union[int, str]]:
    """Return this anchor as a plain dict, including the resolved anchor cell.

    Returns:
      The dataclass fields plus ``anchor_row``/``anchor_col``, the tipbox
      cell that aligns with the active head anchor.
    """
    anchor_row, anchor_col = tipbox_anchor_cell(
      TipSelection(
        location=0,
        row=self.row,
        col=self.col,
        row_count=self.row_count,
        column_count=self.column_count,
        mirror_corner=self.mirror_corner,
        head_anchor=self.head_anchor,
      )
    )
    return {
      "row": self.row,
      "col": self.col,
      "row_count": self.row_count,
      "column_count": self.column_count,
      "mirror_corner": self.mirror_corner,
      "head_anchor": self.head_anchor,
      "anchor_row": anchor_row,
      "anchor_col": anchor_col,
    }


def head_geometry_for_type(head_type: HeadType) -> HeadGeometry:
  """Return the physical barrel grid for an installed head type.

  Args:
    head_type: The installed head type.

  Returns:
    The head's row/column count and barrel pitch. Every 96-channel head
    (including ``"unknown"``, which is treated as a 96-head default) shares
    the same 8x12, 9 mm grid.
  """
  if head_type in _HEAD_384_FAMILY:
    return HeadGeometry(rows=16, columns=24, pitch_x_mm=4.5, pitch_y_mm=4.5)
  if head_type == "1536_pintool":
    return HeadGeometry(rows=32, columns=48, pitch_x_mm=2.25, pitch_y_mm=2.25)
  if head_type == "16_d_st":
    return HeadGeometry(rows=16, columns=1, pitch_x_mm=4.5, pitch_y_mm=4.5)
  if head_type == "8_d_lt":
    return HeadGeometry(rows=8, columns=1, pitch_x_mm=9.0, pitch_y_mm=9.0)
  return HeadGeometry(rows=8, columns=12, pitch_x_mm=9.0, pitch_y_mm=9.0)


def normalize_head_mode(
  head_type: HeadType,
  subset_type: Optional[str],
  subset_config: Optional[str],
  row_count: Optional[int] = None,
  column_count: Optional[int] = None,
) -> HeadMode:
  """Resolve a caller's requested head subset into a valid :class:`HeadMode`.

  Unrecognised or missing values fall back to sensible defaults rather than
  raising, since this is the boundary where free-form input (a web request,
  a saved protocol) becomes a value the rest of this module can trust:
  ``quadrant`` becomes a half-size ``rectangle``; a subset type the current
  head cannot support (e.g. ``row`` on a single-column head) falls back to
  ``all_barrels``; and row/column counts are clamped to the head's geometry.

  Args:
    head_type: The installed head type.
    subset_type: The requested subset kind, e.g. ``"row"``, ``"column"``,
      ``"rectangle"``, ``"single_barrel"``, ``"quadrant"``, or
      ``"all_barrels"``. Anything else falls back to ``"all_barrels"``.
    subset_config: The requested anchor corner. Anything other than
      ``"front_left"``, ``"front_right"``, ``"back_left"``, or
      ``"back_right"`` falls back to ``"back_left"``.
    row_count: Requested active row count, for ``"row"``/``"rectangle"``.
    column_count: Requested active column count, for
      ``"column"``/``"rectangle"``.

  Returns:
    A :class:`HeadMode` valid for ``head_type``.
  """
  geometry = head_geometry_for_type(head_type)
  normalized_type = str(subset_type or "all_barrels").strip().lower()
  normalized_config = str(subset_config or "back_left").strip().lower()
  if normalized_config not in {"front_left", "front_right", "back_left", "back_right"}:
    normalized_config = "back_left"

  if normalized_type == "quadrant":
    normalized_type = "rectangle"
    if row_count is None:
      row_count = max(1, geometry.rows // 2)
    if column_count is None:
      column_count = max(1, geometry.columns // 2)

  if geometry.rows <= 1 and normalized_type in {"row", "rectangle"}:
    normalized_type = "all_barrels"
  if geometry.columns <= 1 and normalized_type in {"column", "rectangle"}:
    normalized_type = "all_barrels"
  if normalized_type not in {"all_barrels", "row", "column", "single_barrel", "rectangle"}:
    normalized_type = "all_barrels"
  if normalized_type == "all_barrels":
    normalized_config = "back_left"

  selected_rows = geometry.rows
  selected_columns = geometry.columns
  if normalized_type == "row":
    selected_rows = max(1, min(geometry.rows, int(row_count or 1)))
  elif normalized_type == "column":
    selected_columns = max(1, min(geometry.columns, int(column_count or 1)))
  elif normalized_type == "rectangle":
    selected_rows = max(1, min(geometry.rows, int(row_count or 1)))
    selected_columns = max(1, min(geometry.columns, int(column_count or 1)))
  elif normalized_type == "single_barrel":
    selected_rows = 1
    selected_columns = 1

  return HeadMode(
    subset_type=normalized_type,
    subset_config=normalized_config,
    row_count=selected_rows,
    column_count=selected_columns,
  )


def head_selected_ranges(
  head_type: HeadType, mode: HeadMode
) -> tuple[tuple[int, int], tuple[int, int]]:
  """Return the active barrel range as ``(row_start, row_stop), (col_start, col_stop)``.

  Args:
    head_type: The installed head type.
    mode: A normalised head mode.

  Returns:
    Half-open ``(start, stop)`` ranges for rows and for columns.
  """
  geometry = head_geometry_for_type(head_type)
  row_start, row_stop = _selected_range(
    geometry.rows,
    mode.row_count,
    front_selected=mode.subset_config not in _FRONT_ORIENTATIONS,
  )
  col_start, col_stop = _selected_range(
    geometry.columns,
    mode.column_count,
    front_selected=mode.subset_config in _LEFT_ORIENTATIONS,
  )
  return (row_start, row_stop), (col_start, col_stop)


def head_anchor_cell(head_type: HeadType, mode: HeadMode) -> tuple[int, int]:
  """Return the (row, col) of the head's single reference barrel for this mode.

  Args:
    head_type: The installed head type.
    mode: A normalised head mode.

  Returns:
    The active block's corner barrel closest to ``mode.subset_config``.
  """
  if mode.subset_type == "all_barrels":
    return 0, 0
  (row_start, row_stop), (col_start, col_stop) = head_selected_ranges(head_type, mode)
  if mode.subset_type == "column":
    row = 0
    col = col_start if mode.subset_config in _LEFT_ORIENTATIONS else col_stop - 1
    return row, col
  if mode.subset_type == "row":
    row = row_stop - 1 if mode.subset_config in _FRONT_ORIENTATIONS else row_start
    col = 0
    return row, col
  row = row_stop - 1 if mode.subset_config in _FRONT_ORIENTATIONS else row_start
  col = col_start if mode.subset_config in _LEFT_ORIENTATIONS else col_stop - 1
  return row, col


def head_mode_offsets_mm(head_type: HeadType, mode: HeadMode) -> tuple[float, float]:
  """Return the (x, y) offset from the head's origin barrel to the active block's origin.

  Args:
    head_type: The installed head type.
    mode: A normalised head mode.

  Returns:
    The offset in millimetres.
  """
  geometry = head_geometry_for_type(head_type)
  (row_start, _), (col_start, _) = head_selected_ranges(head_type, mode)
  return col_start * geometry.pitch_x_mm, row_start * geometry.pitch_y_mm


def active_head_wells(head_type: HeadType, mode: HeadMode) -> list[tuple[int, int]]:
  """Return every (row, col) barrel position active under this mode.

  Args:
    head_type: The installed head type.
    mode: A normalised head mode.

  Returns:
    All active barrel positions.
  """
  (row_start, row_stop), (col_start, col_stop) = head_selected_ranges(head_type, mode)
  return [(row, col) for row in range(row_start, row_stop) for col in range(col_start, col_stop)]


def tipbox_mirror_corner(mode: HeadMode) -> str:
  """Return the tipbox corner a head mode should pick tips from.

  The tipbox side is the mirror image of the head's own anchor corner: a
  head anchored at its own left picks from the tipbox's right, and so on,
  since the head reaches across to the tips rather than starting flush
  against them.

  Args:
    mode: A normalised head mode.

  Returns:
    One of ``"front_left"``, ``"front_right"``, ``"back_left"``, or
    ``"back_right"``.
  """
  if mode.subset_type == "all_barrels":
    return "back_left"
  if mode.subset_type == "column":
    # Left head -> pick from right tipbox side, Right head -> pick from left
    return "back_left" if mode.subset_config.endswith("right") else "back_right"
  if mode.subset_type == "row":
    return "front_left" if mode.subset_config.startswith("back") else "back_left"
  front = mode.subset_config in _FRONT_ORIENTATIONS
  left = mode.subset_config in _LEFT_ORIENTATIONS
  tipbox_front = not front
  tipbox_left = not left
  return f"{'front' if tipbox_front else 'back'}_{'left' if tipbox_left else 'right'}"


def head_anchor_corner(mode: HeadMode) -> str:
  """Return the tipbox corner where the head's reference barrel aligns.

  Args:
    mode: A normalised head mode.

  Returns:
    One of ``"front_left"``, ``"front_right"``, ``"back_left"``, or
    ``"back_right"``.
  """
  if mode.subset_type == "all_barrels":
    return "back_left"
  if mode.subset_type == "column":
    return "back_left" if mode.subset_config.endswith("left") else "back_right"
  if mode.subset_type == "row":
    return "back_left" if mode.subset_config.startswith("back") else "front_left"
  front = mode.subset_config in _FRONT_ORIENTATIONS
  left = mode.subset_config in _LEFT_ORIENTATIONS
  return f"{'front' if front else 'back'}_{'left' if left else 'right'}"


def tipbox_selection(
  location: int,
  row: int,
  col: int,
  mode: HeadMode,
) -> TipSelection:
  """Build a :class:`TipSelection` for picking tips at a tipbox anchor cell.

  Args:
    location: The deck location of the tipbox.
    row: Zero-based row of the anchor cell in the tipbox.
    col: Zero-based column of the anchor cell in the tipbox.
    mode: A normalised head mode.

  Returns:
    The selection, sized and oriented to match ``mode``.
  """
  return TipSelection(
    location=location,
    row=int(row),
    col=int(col),
    row_count=max(1, int(mode.row_count)),
    column_count=max(1, int(mode.column_count)),
    mirror_corner=tipbox_mirror_corner(mode),
    head_anchor=head_anchor_corner(mode),
  )


def plate_selection(
  location: int,
  row: int,
  col: int,
) -> PlateSelection:
  """Build a :class:`PlateSelection` for a plate anchor cell.

  Args:
    location: The deck location of the plate.
    row: Zero-based row of the anchor well.
    col: Zero-based column of the anchor well.

  Returns:
    The selection.
  """
  return PlateSelection(location=location, row=int(row), col=int(col))


def selected_anchor_ranges(
  total_rows: int,
  total_cols: int,
  selection: TipSelection,
) -> tuple[tuple[int, int], tuple[int, int]]:
  """Return the tipbox cell range a selection covers, clamped to the tipbox.

  Args:
    total_rows: Number of rows in the tipbox.
    total_cols: Number of columns in the tipbox.
    selection: The anchor and span to resolve.

  Returns:
    Half-open ``(row_start, row_stop), (col_start, col_stop)`` ranges.
  """
  row_start = max(0, min(total_rows - selection.row_count, int(selection.row)))
  col_start = max(0, min(total_cols - selection.column_count, int(selection.col)))
  return (row_start, row_start + selection.row_count), (
    col_start,
    col_start + selection.column_count,
  )


def tipbox_anchor_cell(selection: TipSelection) -> tuple[int, int]:
  """Return the physical tipbox cell that aligns with the active head anchor.

  Args:
    selection: The tip selection to resolve.

  Returns:
    The (row, col) of the tipbox cell under the head's reference barrel.
  """
  anchor_row = (
    selection.row + selection.row_count - 1
    if selection.head_anchor.startswith("front")
    else selection.row
  )
  anchor_col = (
    selection.col + selection.column_count - 1
    if selection.head_anchor.endswith("right")
    else selection.col
  )
  return anchor_row, anchor_col


def selected_tip_wells(
  total_rows: int,
  total_cols: int,
  selection: TipSelection,
) -> list[tuple[int, int]]:
  """Return every tipbox cell a selection covers.

  Args:
    total_rows: Number of rows in the tipbox.
    total_cols: Number of columns in the tipbox.
    selection: The anchor and span to resolve.

  Returns:
    All covered (row, col) cells.
  """
  (row_start, row_stop), (col_start, col_stop) = selected_anchor_ranges(
    total_rows,
    total_cols,
    selection,
  )
  return [(row, col) for row in range(row_start, row_stop) for col in range(col_start, col_stop)]


def describe_head_mode(mode: HeadMode) -> str:
  """Return a short human-readable description of a head mode.

  Args:
    mode: The head mode to describe.

  Returns:
    A string such as ``"Rectangle (Back Left, 4x6)"``.
  """
  label_map = {
    "all_barrels": "All barrels",
    "row": "Full row",
    "column": "Full column",
    "rectangle": "Rectangle",
    "single_barrel": "Single barrel",
  }
  orientation = mode.subset_config.replace("_", " ").title()
  if mode.subset_type == "all_barrels":
    return "All barrels"
  if mode.subset_type == "row":
    row_word = "row" if mode.row_count == 1 else "rows"
    return (
      f"{label_map.get(mode.subset_type, mode.subset_type)} "
      f"({orientation}, {mode.row_count} {row_word})"
    )
  if mode.subset_type == "column":
    col_word = "column" if mode.column_count == 1 else "columns"
    return (
      f"{label_map.get(mode.subset_type, mode.subset_type)} "
      f"({orientation}, {mode.column_count} {col_word})"
    )
  if mode.subset_type == "rectangle":
    return (
      f"{label_map.get(mode.subset_type, mode.subset_type)} "
      f"({orientation}, {mode.row_count}x{mode.column_count})"
    )
  return f"{label_map.get(mode.subset_type, mode.subset_type)} ({orientation})"


def suggested_head_mode(head_type: HeadType, wells: Optional[int]) -> HeadMode:
  """Suggest a head mode that covers a target well count on a plate.

  Args:
    head_type: The installed head type.
    wells: The number of wells the operation targets, if known.

  Returns:
    ``"all_barrels"`` when ``wells`` is unknown or matches the head's own
    channel count; a partial-head mode sized to reach a denser well grid
    (e.g. a 96 head striping a 384 plate); ``"all_barrels"`` as the
    fallback for any combination not otherwise handled.
  """
  channel_count = head_type_channels(head_type)
  if not wells or wells <= 0:
    return normalize_head_mode(head_type, "all_barrels", "front_left")
  if wells == channel_count:
    return normalize_head_mode(head_type, "all_barrels", "front_left")
  if channel_count == 96 and wells == 384:
    return normalize_head_mode(head_type, "rectangle", "front_left", row_count=8, column_count=12)
  if channel_count == 8 and wells in {96, 384}:
    return normalize_head_mode(head_type, "column", "front_left")
  if channel_count == 16 and wells == 384:
    return normalize_head_mode(head_type, "row", "front_left")
  return normalize_head_mode(head_type, "all_barrels", "front_left")


def _selected_range(total: int, selected: int, *, front_selected: bool) -> tuple[int, int]:
  """Return the half-open range of ``selected`` items out of ``total``, from one end.

  Args:
    total: The full size along this axis.
    selected: How many of ``total`` are active.
    front_selected: If True, the range starts at index 0; otherwise it ends
      at ``total``.

  Returns:
    A half-open ``(start, stop)`` range.
  """
  if selected >= total:
    return 0, total
  if front_selected:
    return 0, selected
  return total - selected, total


def legal_plate_anchors(
  head_type: HeadType,
  mode: HeadMode,
  plate_rows: int,
  plate_cols: int,
  pitch_x_mm: float,
  pitch_y_mm: float,
) -> list[PlateSelection]:
  """Return every plate anchor cell where this head mode's footprint fits the plate.

  Args:
    head_type: The installed head type.
    mode: A normalised head mode.
    plate_rows: Number of well rows on the plate.
    plate_cols: Number of well columns on the plate.
    pitch_x_mm: Plate well spacing along columns, in millimetres.
    pitch_y_mm: Plate well spacing along rows, in millimetres.

  Returns:
    Every legal anchor, as a :class:`PlateSelection` with ``location=0``.
  """
  if plate_rows <= 0 or plate_cols <= 0:
    return []
  anchors: list[PlateSelection] = []
  for row in range(plate_rows):
    for col in range(plate_cols):
      if is_legal_plate_anchor(
        head_type,
        mode,
        plate_rows,
        plate_cols,
        pitch_x_mm,
        pitch_y_mm,
        row,
        col,
      ):
        anchors.append(PlateSelection(location=0, row=row, col=col))
  return anchors


def is_legal_plate_anchor(
  head_type: HeadType,
  mode: HeadMode,
  plate_rows: int,
  plate_cols: int,
  pitch_x_mm: float,
  pitch_y_mm: float,
  anchor_row: int,
  anchor_col: int,
  *,
  tolerance: float = 1e-6,
) -> bool:
  """Return whether a head mode's footprint fits the plate when anchored at a cell.

  Args:
    head_type: The installed head type.
    mode: A normalised head mode.
    plate_rows: Number of well rows on the plate.
    plate_cols: Number of well columns on the plate.
    pitch_x_mm: Plate well spacing along columns, in millimetres.
    pitch_y_mm: Plate well spacing along rows, in millimetres.
    anchor_row: Zero-based row of the candidate anchor well.
    anchor_col: Zero-based column of the candidate anchor well.
    tolerance: Maximum deviation allowed when checking that the head pitch
      is an integer multiple of the plate pitch.

  Returns:
    True if every active barrel maps onto a well within the plate.
  """
  return bool(
    plate_footprint_wells(
      head_type,
      mode,
      plate_rows,
      plate_cols,
      pitch_x_mm,
      pitch_y_mm,
      anchor_row,
      anchor_col,
      tolerance=tolerance,
    )
  )


def plate_footprint_wells(
  head_type: HeadType,
  mode: HeadMode,
  plate_rows: int,
  plate_cols: int,
  pitch_x_mm: float,
  pitch_y_mm: float,
  anchor_row: int,
  anchor_col: int,
  *,
  tolerance: float = 1e-6,
) -> list[tuple[int, int]]:
  """Map a head mode's active barrels onto plate wells, anchored at a cell.

  Args:
    head_type: The installed head type.
    mode: A normalised head mode.
    plate_rows: Number of well rows on the plate.
    plate_cols: Number of well columns on the plate.
    pitch_x_mm: Plate well spacing along columns, in millimetres.
    pitch_y_mm: Plate well spacing along rows, in millimetres.
    anchor_row: Zero-based row of the anchor well.
    anchor_col: Zero-based column of the anchor well.
    tolerance: Maximum deviation allowed when checking that the head pitch
      is an integer multiple of the plate pitch.

  Returns:
    The mapped (row, col) well for every active barrel, in the same order
    as :func:`active_head_wells`. Empty if the head pitch is not an integer
    multiple of the plate pitch, or if any mapped well would fall outside
    the plate.
  """
  if plate_rows <= 0 or plate_cols <= 0 or pitch_x_mm <= 0 or pitch_y_mm <= 0:
    return []
  geometry = head_geometry_for_type(head_type)
  step_row = _near_integer(geometry.pitch_y_mm / pitch_y_mm, tolerance)
  step_col = _near_integer(geometry.pitch_x_mm / pitch_x_mm, tolerance)
  if step_row is None or step_col is None or step_row <= 0 or step_col <= 0:
    return []
  (sel_row_start, _), (sel_col_start, _) = head_selected_ranges(head_type, mode)
  mapped: list[tuple[int, int]] = []
  for barrel_row, barrel_col in active_head_wells(head_type, mode):
    mapped_row = int(anchor_row) + (barrel_row - sel_row_start) * step_row
    mapped_col = int(anchor_col) + (barrel_col - sel_col_start) * step_col
    if mapped_row < 0 or mapped_row >= plate_rows or mapped_col < 0 or mapped_col >= plate_cols:
      return []
    mapped.append((mapped_row, mapped_col))
  return mapped


def _near_integer(value: float, tolerance: float) -> Optional[int]:
  """Round ``value`` to the nearest integer if it is within ``tolerance`` of one.

  Args:
    value: The value to check.
    tolerance: Maximum allowed deviation from the nearest integer.

  Returns:
    The rounded integer, or None if ``value`` is not close enough to one.
  """
  rounded = int(round(value))
  if abs(value - rounded) > tolerance:
    return None
  return rounded


def legal_tipbox_anchors(
  total_rows: int,
  total_cols: int,
  mode: HeadMode,
  occupied_wells: set[tuple[int, int]],
  *,
  purpose: str,
) -> list[TipAnchor]:
  """Return every legal tipbox anchor for picking up or returning tips.

  Iterates from the mode's mirror corner inward, so the first legal anchor
  in the returned list is the one closest to that corner.

  Args:
    total_rows: Number of rows in the tipbox.
    total_cols: Number of columns in the tipbox.
    mode: A normalised head mode.
    occupied_wells: The tipbox cells that currently hold a tip.
    purpose: ``"pickup"`` to find anchors where every covered cell already
      holds a tip, or ``"return"`` to find anchors where every covered cell
      is empty.

  Returns:
    Every legal anchor, as a :class:`TipAnchor`.

  Raises:
    ValueError: If ``purpose`` is neither ``"pickup"`` nor ``"return"``.
  """
  if total_rows <= 0 or total_cols <= 0:
    return []
  occupied = set(occupied_wells)
  anchors: list[TipAnchor] = []
  max_row = max(0, total_rows - mode.row_count)
  max_col = max(0, total_cols - mode.column_count)

  # Determine iteration order based on the mirror corner so the first
  # legal anchor is on the correct side of the tipbox.
  mirror = tipbox_mirror_corner(mode)
  col_range = range(max_col, -1, -1) if mirror.endswith("right") else range(max_col + 1)
  row_range = range(max_row, -1, -1) if mirror.startswith("front") else range(max_row + 1)

  for row in row_range:
    for col in col_range:
      selection = tipbox_selection(0, row, col, mode)
      if _is_legal_tipbox_anchor(total_rows, total_cols, occupied, selection, purpose=purpose):
        anchors.append(
          TipAnchor(
            row=row,
            col=col,
            row_count=selection.row_count,
            column_count=selection.column_count,
            mirror_corner=selection.mirror_corner,
            head_anchor=selection.head_anchor,
          )
        )
  return anchors


def is_legal_tipbox_anchor(
  total_rows: int,
  total_cols: int,
  mode: HeadMode,
  occupied_wells: set[tuple[int, int]],
  selection_row: int,
  selection_col: int,
  *,
  purpose: str,
) -> bool:
  """Return whether a specific tipbox anchor is legal for pickup or return.

  Args:
    total_rows: Number of rows in the tipbox.
    total_cols: Number of columns in the tipbox.
    mode: A normalised head mode.
    occupied_wells: The tipbox cells that currently hold a tip.
    selection_row: Zero-based row of the candidate anchor cell.
    selection_col: Zero-based column of the candidate anchor cell.
    purpose: ``"pickup"`` or ``"return"``; see :func:`legal_tipbox_anchors`.

  Returns:
    True if the anchor is legal.
  """
  if total_rows <= 0 or total_cols <= 0:
    return False
  selection = tipbox_selection(0, selection_row, selection_col, mode)
  return _is_legal_tipbox_anchor(
    total_rows,
    total_cols,
    set(occupied_wells),
    selection,
    purpose=purpose,
  )


def _is_legal_tipbox_anchor(
  total_rows: int,
  total_cols: int,
  occupied: set[tuple[int, int]],
  selection: TipSelection,
  *,
  purpose: str,
) -> bool:
  """Return whether a resolved tipbox selection is legal for pickup or return.

  Args:
    total_rows: Number of rows in the tipbox.
    total_cols: Number of columns in the tipbox.
    occupied: The tipbox cells that currently hold a tip.
    selection: The anchor and span to check.
    purpose: ``"pickup"`` or ``"return"``; see :func:`legal_tipbox_anchors`.

  Returns:
    True if the anchor is legal.

  Raises:
    ValueError: If ``purpose`` is neither ``"pickup"`` nor ``"return"``.
  """
  (row_start, row_stop), (col_start, col_stop) = selected_anchor_ranges(
    total_rows,
    total_cols,
    selection,
  )
  selected = {
    (row, col) for row in range(row_start, row_stop) for col in range(col_start, col_stop)
  }
  if not selected:
    return False
  if purpose == "pickup":
    if any(well not in occupied for well in selected):
      return False
  elif purpose == "return":
    if any(well in occupied for well in selected):
      return False
  else:
    raise ValueError(f"Unknown tipbox anchor purpose: {purpose}")

  if purpose == "return":
    # Returns are anchored by the operator, not by the box. The first
    # ejection into an empty box may go anywhere, and every later one has
    # to sit flush against what is already there — so the head walks
    # steadily across the box in whichever direction that first choice
    # implied, and the filled region stays contiguous. The pickup rule below
    # (outboard side must be empty) does not apply here: demanding an empty
    # outboard side on every return would reject all but the very first
    # return into a box, since every later return has occupied cells
    # outboard of it by construction.
    if not occupied:
      return True
    # Step along whichever axis the block does not already span: a
    # full-column head walks across columns, a full-row head down rows.
    if (row_stop - row_start) >= total_rows:
      band = {col for _, col in occupied}
      start, stop = col_start, col_stop
    else:
      band = {row for row, _ in occupied}
      start, stop = row_start, row_stop
    return stop == min(band) or start == max(band) + 1

  # Pickup: consume from the mirror corner inward, so everything outboard of
  # the block must already be empty. Keeps the head taking the outermost
  # remaining block rather than orphaning tips behind it.
  if selection.mirror_corner.endswith("left"):
    boundary_cols = range(0, col_start)
  else:
    boundary_cols = range(col_stop, total_cols)
  if any((row, col) in occupied for row in range(row_start, row_stop) for col in boundary_cols):
    return False

  if selection.mirror_corner.startswith("front"):
    boundary_rows = range(row_stop, total_rows)
  else:
    boundary_rows = range(0, row_start)
  if any((row, col) in occupied for row in boundary_rows for col in range(col_start, col_stop)):
    return False

  return True

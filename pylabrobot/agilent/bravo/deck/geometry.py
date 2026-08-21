"""Well geometry derived from labware metadata.

Converts a labware definition's rows/cols/spacing/offset metadata into a
:class:`WellGeometry`, and resolves the millimetre offset from a location's
taught teachpoint (the labware's back-left corner) to a specific well or
tipbox cell center.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..head_mode import TipSelection


@dataclass(frozen=True)
class WellGeometry:
  """The row/column grid and pitch/offset of a labware's wells.

  Attributes:
    rows: Number of well rows.
    cols: Number of well columns.
    pitch_x_mm: Well-to-well spacing along columns, in millimetres.
    pitch_y_mm: Well-to-well spacing along rows, in millimetres.
    offset_x_mm: X offset from the labware's teachpoint to well A1's center.
    offset_y_mm: Y offset from the labware's teachpoint to well A1's center.
  """

  rows: int
  cols: int
  pitch_x_mm: float
  pitch_y_mm: float
  offset_x_mm: float
  offset_y_mm: float


def _rows_cols_from_metadata(metadata: dict[str, Any]) -> tuple[int, int]:
  """Resolve a row/column count from labware metadata.

  Args:
    metadata: A labware's well-dimension metadata (or its full metadata,
      when ``rows``/``cols``/``wells`` live at the top level).

  Returns:
    The ``(rows, cols)`` pair. If ``rows``/``cols`` are not present, they are
    inferred from a ``wells`` total for the standard 96/384/1536 well plate
    grids; otherwise ``(0, 0)``.
  """
  rows = int(metadata.get("rows") or 0)
  cols = int(metadata.get("cols") or 0)
  if rows > 0 and cols > 0:
    return rows, cols
  wells = int(metadata.get("wells") or 0)
  if wells == 96:
    return 8, 12
  if wells == 384:
    return 16, 24
  if wells == 1536:
    return 32, 48
  return rows, cols


def _default_pitch_mm(count: int) -> float:
  """Return the standard SBS well pitch for a given row or column count.

  Args:
    count: The number of rows or columns along one axis.

  Returns:
    2.25 mm for a 1536-density axis, 4.5 mm for a 384-density axis, and 9.0
    mm otherwise (96-density and below).
  """
  if count >= 32:
    return 2.25
  if count >= 16:
    return 4.5
  return 9.0


def _default_offset_mm(
  rows: int, cols: int, pitch_x_mm: float, pitch_y_mm: float
) -> tuple[float, float]:
  """Return the standard SBS teachpoint-to-A1 offset for a well grid.

  Args:
    rows: Number of well rows.
    cols: Number of well columns.
    pitch_x_mm: Well-to-well spacing along columns, in millimetres.
    pitch_y_mm: Well-to-well spacing along rows, in millimetres.

  Returns:
    ``(3.375, 3.375)`` for a 1536-density plate, ``(2.25, 2.25)`` for a
    384-density plate, and ``(0.0, 0.0)`` otherwise.
  """
  if rows >= 32 or cols >= 48:
    return 3.375, 3.375
  if rows >= 16 or cols >= 24:
    return 2.25, 2.25
  return 0.0, 0.0


def well_geometry_from_metadata(metadata: Optional[dict[str, Any]]) -> WellGeometry:
  """Build a :class:`WellGeometry` from a labware definition's metadata.

  Explicit ``spacing_x_mm``/``spacing_y_mm``/``offset_x_mm``/``offset_y_mm``
  values win; anything missing falls back to the standard SBS defaults for
  the plate's well density.

  Args:
    metadata: The labware's metadata dict, or a nested ``well_dimensions_mm``
      sub-dict. ``None`` is treated as empty.

  Returns:
    The resolved well geometry.
  """
  raw = dict(metadata or {})
  well_dims = dict(raw.get("well_dimensions_mm") or raw)
  rows, cols = _rows_cols_from_metadata(well_dims or raw)
  pitch_x_mm = float(well_dims.get("spacing_x_mm") or _default_pitch_mm(cols))
  pitch_y_mm = float(well_dims.get("spacing_y_mm") or _default_pitch_mm(rows))
  default_offset_x_mm, default_offset_y_mm = _default_offset_mm(rows, cols, pitch_x_mm, pitch_y_mm)
  raw_offset_x_mm = well_dims.get("offset_x_mm")
  raw_offset_y_mm = well_dims.get("offset_y_mm")
  offset_x_mm = float(raw_offset_x_mm) if raw_offset_x_mm is not None else default_offset_x_mm
  offset_y_mm = float(raw_offset_y_mm) if raw_offset_y_mm is not None else default_offset_y_mm
  if offset_x_mm == 0.0 and offset_y_mm == 0.0 and (rows >= 16 or cols >= 24):
    offset_x_mm = default_offset_x_mm
    offset_y_mm = default_offset_y_mm
  return WellGeometry(
    rows=rows,
    cols=cols,
    pitch_x_mm=pitch_x_mm,
    pitch_y_mm=pitch_y_mm,
    offset_x_mm=offset_x_mm,
    offset_y_mm=offset_y_mm,
  )


def a1_center_offset_from_teachpoint_mm(metadata: Optional[dict[str, Any]]) -> tuple[float, float]:
  """Return the (x, y) offset from a labware's teachpoint to well A1's center.

  Args:
    metadata: The labware's metadata dict.

  Returns:
    The offset in millimetres.
  """
  geometry = well_geometry_from_metadata(metadata)
  return -geometry.offset_x_mm, -geometry.offset_y_mm


def well_center_offset_from_teachpoint_mm(
  metadata: Optional[dict[str, Any]],
  *,
  row: int,
  col: int,
) -> tuple[float, float]:
  """Return the (x, y) offset from a labware's teachpoint to a well's center.

  Args:
    metadata: The labware's metadata dict.
    row: Zero-based well row.
    col: Zero-based well column.

  Returns:
    The offset in millimetres.
  """
  geometry = well_geometry_from_metadata(metadata)
  base_x_mm, base_y_mm = a1_center_offset_from_teachpoint_mm(metadata)
  return (
    base_x_mm + int(col) * geometry.pitch_x_mm,
    base_y_mm + int(row) * geometry.pitch_y_mm,
  )


def tipbox_anchor_offset_from_teachpoint_mm(
  metadata: Optional[dict[str, Any]],
  selection: TipSelection,
) -> tuple[float, float]:
  """Return the (x, y) offset from a tipbox's teachpoint to a selection's anchor cell.

  Args:
    metadata: The tipbox's metadata dict.
    selection: The tip selection whose anchor cell to resolve.

  Returns:
    The offset in millimetres.
  """
  return well_center_offset_from_teachpoint_mm(
    metadata,
    row=int(selection.row),
    col=int(selection.col),
  )

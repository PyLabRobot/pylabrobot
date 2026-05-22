from __future__ import annotations

from dataclasses import asdict, dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.types import HeadType

_FRONT_ORIENTATIONS = {"front_left", "front_right"}
_LEFT_ORIENTATIONS = {"front_left", "back_left"}


@dataclass(frozen=True)
class HeadGeometry:
    rows: int
    columns: int
    pitch_x_mm: float
    pitch_y_mm: float


@dataclass(frozen=True)
class HeadMode:
    subset_type: str = "all_barrels"
    subset_config: str = "front_left"
    row_count: int = 0
    column_count: int = 0

    @property
    def num_channels(self) -> int:
        return int(self.row_count) * int(self.column_count)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["num_channels"] = self.num_channels
        data["display_text"] = describe_head_mode(self)
        return data


@dataclass(frozen=True)
class TipSelection:
    location: int
    row: int
    col: int
    row_count: int = 1
    column_count: int = 1
    mirror_corner: str = "back_left"
    head_anchor: str = "back_left"

    def to_dict(self) -> dict[str, int | str]:
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
    location: int
    row: int
    col: int

    def to_dict(self) -> dict[str, int]:
        return {
            "location": self.location,
            "row": self.row,
            "col": self.col,
        }


@dataclass(frozen=True)
class TipAnchor:
    row: int
    col: int

    row_count: int
    column_count: int
    mirror_corner: str
    head_anchor: str = "back_left"

    def to_dict(self) -> dict[str, int | str]:
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
    if head_type in {
        HeadType.HT_384_D_70,
        HeadType.HT_384_D_70_S2,
        HeadType.HT_384_F_50,
        HeadType.HT_384_PINTOOL,
    }:
        return HeadGeometry(rows=16, columns=24, pitch_x_mm=4.5, pitch_y_mm=4.5)
    if head_type == HeadType.HT_1536_PINTOOL:
        return HeadGeometry(rows=32, columns=48, pitch_x_mm=2.25, pitch_y_mm=2.25)
    if head_type == HeadType.HT_16_D_ST:
        return HeadGeometry(rows=16, columns=1, pitch_x_mm=4.5, pitch_y_mm=4.5)
    if head_type == HeadType.HT_8_D_LT:
        return HeadGeometry(rows=8, columns=1, pitch_x_mm=9.0, pitch_y_mm=9.0)
    return HeadGeometry(rows=8, columns=12, pitch_x_mm=9.0, pitch_y_mm=9.0)


def normalize_head_mode(
    head_type: HeadType,
    subset_type: str | None,
    subset_config: str | None,
    row_count: int | None = None,
    column_count: int | None = None,
) -> HeadMode:
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


def head_selected_ranges(head_type: HeadType, mode: HeadMode) -> tuple[tuple[int, int], tuple[int, int]]:
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
    geometry = head_geometry_for_type(head_type)
    (row_start, _), (col_start, _) = head_selected_ranges(head_type, mode)
    return col_start * geometry.pitch_x_mm, row_start * geometry.pitch_y_mm


def active_head_wells(head_type: HeadType, mode: HeadMode) -> list[tuple[int, int]]:
    (row_start, row_stop), (col_start, col_stop) = head_selected_ranges(head_type, mode)
    return [
        (row, col)
        for row in range(row_start, row_stop)
        for col in range(col_start, col_stop)
    ]


def tip_task_head_offsets_mm(head_type: HeadType, mode: HeadMode) -> tuple[float, float]:
    geometry = head_geometry_for_type(head_type)
    (row_start, _), (col_start, _) = head_selected_ranges(head_type, mode)
    return col_start * geometry.pitch_x_mm, row_start * geometry.pitch_y_mm


def tipbox_mirror_corner(mode: HeadMode) -> str:
    if mode.subset_type == "all_barrels":
        return "back_left"
    if mode.subset_type == "column":
        # Left head → pick from right tipbox side, Right head → pick from left
        return "back_left" if mode.subset_config.endswith("right") else "back_right"
    if mode.subset_type == "row":
        return "front_left" if mode.subset_config.startswith("back") else "back_left"
    front = mode.subset_config in _FRONT_ORIENTATIONS
    left = mode.subset_config in _LEFT_ORIENTATIONS
    tipbox_front = not front
    tipbox_left = not left
    return f"{'front' if tipbox_front else 'back'}_{'left' if tipbox_left else 'right'}"


def head_anchor_corner(mode: HeadMode) -> str:
    """Return the tipbox corner where the head's reference barrel aligns."""
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
    return PlateSelection(location=location, row=int(row), col=int(col))


def selected_anchor_ranges(
    total_rows: int,
    total_cols: int,
    selection: TipSelection,
) -> tuple[tuple[int, int], tuple[int, int]]:
    row_start = max(0, min(total_rows - selection.row_count, int(selection.row)))
    col_start = max(0, min(total_cols - selection.column_count, int(selection.col)))
    return (row_start, row_start + selection.row_count), (col_start, col_start + selection.column_count)


def tipbox_anchor_cell(selection: TipSelection) -> tuple[int, int]:
    """Return the physical tipbox cell that aligns with the active head anchor."""
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
    (row_start, row_stop), (col_start, col_stop) = selected_anchor_ranges(
        total_rows,
        total_cols,
        selection,
    )
    return [
        (row, col)
        for row in range(row_start, row_stop)
        for col in range(col_start, col_stop)
    ]


def selected_tip_center_offset_mm(
    total_rows: int,
    total_cols: int,
    pitch_x_mm: float,
    pitch_y_mm: float,
    selection: TipSelection,
) -> tuple[float, float]:
    (row_start, row_stop), (col_start, col_stop) = selected_anchor_ranges(
        total_rows,
        total_cols,
        selection,
    )
    center_x = _subset_center_mm(col_start, col_stop, total_cols, pitch_x_mm)
    center_y = _subset_center_mm(row_start, row_stop, total_rows, pitch_y_mm)
    return center_x, center_y


def selected_tip_anchor_offset_mm(
    offset_x_mm: float,
    offset_y_mm: float,
    pitch_x_mm: float,
    pitch_y_mm: float,
    selection: TipSelection,
) -> tuple[float, float]:
    anchor_row, anchor_col = tipbox_anchor_cell(selection)
    return offset_x_mm + anchor_col * pitch_x_mm, offset_y_mm + anchor_row * pitch_y_mm


def describe_head_mode(mode: HeadMode) -> str:
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
        return f"{label_map.get(mode.subset_type, mode.subset_type)} ({orientation}, {mode.row_count} row{'s' if mode.row_count != 1 else ''})"
    if mode.subset_type == "column":
        return f"{label_map.get(mode.subset_type, mode.subset_type)} ({orientation}, {mode.column_count} column{'s' if mode.column_count != 1 else ''})"
    if mode.subset_type == "rectangle":
        return f"{label_map.get(mode.subset_type, mode.subset_type)} ({orientation}, {mode.row_count}x{mode.column_count})"
    return f"{label_map.get(mode.subset_type, mode.subset_type)} ({orientation})"


def suggested_head_mode(head_type: HeadType, wells: int | None) -> HeadMode:
    channel_count = head_type.channels
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
    if selected >= total:
        return 0, total
    if front_selected:
        return 0, selected
    return total - selected, total


def _anchor_start(anchor: int, selected: int, total: int, *, front_selected: bool) -> int:
    if total <= selected:
        return 0
    start = anchor if front_selected else anchor - selected + 1
    return max(0, min(total - selected, start))


def _subset_center_mm(start: int, stop: int, total: int, pitch_mm: float) -> float:
    if stop <= start:
        return 0.0
    first = start - (total - 1) / 2.0
    last = (stop - 1) - (total - 1) / 2.0
    return ((first + last) / 2.0) * pitch_mm


def legal_plate_anchors(
    head_type: HeadType,
    mode: HeadMode,
    plate_rows: int,
    plate_cols: int,
    pitch_x_mm: float,
    pitch_y_mm: float,
) -> list[PlateSelection]:
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


def _near_integer(value: float, tolerance: float) -> int | None:
    rounded = int(round(value))
    if abs(value - rounded) > tolerance:
        return None
    return rounded


def _plate_phase_count(head_type: HeadType, mode: HeadMode, head_pitch_mm: float, plate_pitch_mm: float) -> int:
    if plate_pitch_mm <= 0:
        return 1
    base_phase = max(1, int(round(9.0 / plate_pitch_mm)))
    if head_type in {
        HeadType.HT_384_D_70,
        HeadType.HT_384_D_70_S2,
        HeadType.HT_384_F_50,
        HeadType.HT_384_PINTOOL,
    }:
        # 384-family heads on 384-pitch plates have a 1:1 barrel-to-well mapping,
        # so only a single phase is needed regardless of subset mode.
        # On 1536 plates the pitch halves, giving 2 dense phases.
        if abs(plate_pitch_mm - 4.5) < 1e-6:
            return 1
        return min(base_phase, 2)
    return base_phase


def legal_tipbox_anchors(
    total_rows: int,
    total_cols: int,
    mode: HeadMode,
    occupied_wells: set[tuple[int, int]],
    *,
    purpose: str,
) -> list[TipAnchor]:
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
    (row_start, row_stop), (col_start, col_stop) = selected_anchor_ranges(
        total_rows,
        total_cols,
        selection,
    )
    selected = {
        (row, col)
        for row in range(row_start, row_stop)
        for col in range(col_start, col_stop)
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

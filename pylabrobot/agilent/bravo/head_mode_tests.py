import unittest

from pylabrobot.agilent.bravo.head_mode import (
  HeadGeometry,
  HeadMode,
  TipSelection,
  active_head_wells,
  describe_head_mode,
  head_anchor_cell,
  head_geometry_for_type,
  head_selected_ranges,
  is_legal_tipbox_anchor,
  legal_tipbox_anchors,
  normalize_head_mode,
  plate_footprint_wells,
  selected_anchor_ranges,
  suggested_head_mode,
  tipbox_anchor_cell,
  tipbox_mirror_corner,
)
from pylabrobot.agilent.bravo.types import HeadType


class HeadGeometryTests(unittest.TestCase):
  def test_96_head_is_8x12_at_9mm(self):
    self.assertEqual(
      head_geometry_for_type("96_d_70"),
      HeadGeometry(rows=8, columns=12, pitch_x_mm=9.0, pitch_y_mm=9.0),
    )

  def test_8_d_lt_is_8x1_at_9mm(self):
    self.assertEqual(
      head_geometry_for_type("8_d_lt"),
      HeadGeometry(rows=8, columns=1, pitch_x_mm=9.0, pitch_y_mm=9.0),
    )

  def test_16_d_st_is_16x1_at_4_5mm(self):
    self.assertEqual(
      head_geometry_for_type("16_d_st"),
      HeadGeometry(rows=16, columns=1, pitch_x_mm=4.5, pitch_y_mm=4.5),
    )

  def test_384_head_is_16x24(self):
    geometry = head_geometry_for_type("384_d_70")
    self.assertEqual(geometry.rows, 16)
    self.assertEqual(geometry.columns, 24)

  def test_384_family_all_share_the_same_geometry(self):
    expected = head_geometry_for_type("384_d_70")
    for head_type in ("384_d_70_s2", "384_f_50", "384_pintool"):
      self.assertEqual(head_geometry_for_type(head_type), expected)

  def test_unknown_head_falls_back_to_96_geometry(self):
    self.assertEqual(head_geometry_for_type("unknown"), head_geometry_for_type("96_d_70"))


class NormalizeHeadModeTests(unittest.TestCase):
  def test_all_barrels_selects_the_full_head(self):
    mode = normalize_head_mode("96_d_70", "all_barrels", "front_left")
    self.assertEqual(mode.subset_type, "all_barrels")
    self.assertEqual(mode.row_count, 8)
    self.assertEqual(mode.column_count, 12)
    # all_barrels always normalizes to back_left regardless of the request.
    self.assertEqual(mode.subset_config, "back_left")

  def test_single_barrel_is_1x1(self):
    mode = normalize_head_mode("96_d_70", "single_barrel", "front_left")
    self.assertEqual(mode.row_count, 1)
    self.assertEqual(mode.column_count, 1)

  def test_column_keeps_all_rows(self):
    mode = normalize_head_mode("96_d_70", "column", "front_left", column_count=3)
    self.assertEqual(mode.subset_type, "column")
    self.assertEqual(mode.row_count, 8)
    self.assertEqual(mode.column_count, 3)

  def test_row_keeps_all_columns(self):
    mode = normalize_head_mode("96_d_70", "row", "front_left", row_count=2)
    self.assertEqual(mode.subset_type, "row")
    self.assertEqual(mode.row_count, 2)
    self.assertEqual(mode.column_count, 12)

  def test_quadrant_normalizes_to_half_size_rectangle(self):
    mode = normalize_head_mode("96_d_70", "quadrant", "front_left")
    self.assertEqual(mode.subset_type, "rectangle")
    self.assertEqual(mode.row_count, 4)
    self.assertEqual(mode.column_count, 6)

  def test_quadrant_respects_explicit_counts(self):
    mode = normalize_head_mode("96_d_70", "quadrant", "front_left", row_count=2, column_count=2)
    self.assertEqual(mode.subset_type, "rectangle")
    self.assertEqual(mode.row_count, 2)
    self.assertEqual(mode.column_count, 2)

  def test_unknown_subset_type_falls_back_to_all_barrels(self):
    mode = normalize_head_mode("96_d_70", "not-a-real-subset", "front_left")
    self.assertEqual(mode.subset_type, "all_barrels")

  def test_missing_subset_type_falls_back_to_all_barrels(self):
    mode = normalize_head_mode("96_d_70", None, None)
    self.assertEqual(mode.subset_type, "all_barrels")

  def test_column_on_single_column_head_falls_back_to_all_barrels(self):
    # 8_d_lt has 1 column, so a "column" subset (which selects among columns)
    # cannot be satisfied and collapses to the whole head.
    mode = normalize_head_mode("8_d_lt", "column", "front_left", column_count=1)
    self.assertEqual(mode.subset_type, "all_barrels")

  def test_row_on_single_column_head_stays_row(self):
    # 8_d_lt has 8 rows, so "row" (which selects among rows) is unaffected
    # by the single-column collapse rule, which only fires for
    # "column"/"rectangle".
    mode = normalize_head_mode("8_d_lt", "row", "front_left", row_count=2)
    self.assertEqual(mode.subset_type, "row")
    self.assertEqual(mode.row_count, 2)

  def test_unrecognized_subset_config_falls_back_to_back_left(self):
    mode = normalize_head_mode("96_d_70", "rectangle", "not-a-corner", row_count=2, column_count=2)
    self.assertEqual(mode.subset_config, "back_left")

  def test_row_count_is_clamped_to_head_geometry(self):
    mode = normalize_head_mode("96_d_70", "row", "front_left", row_count=99)
    self.assertEqual(mode.row_count, 8)

  def test_column_count_is_clamped_to_head_geometry(self):
    mode = normalize_head_mode("96_d_70", "column", "front_left", column_count=99)
    self.assertEqual(mode.column_count, 12)

  def test_zero_row_count_is_clamped_up_to_one(self):
    mode = normalize_head_mode("96_d_70", "row", "front_left", row_count=0)
    self.assertEqual(mode.row_count, 1)

  def test_negative_row_count_is_clamped_up_to_one(self):
    mode = normalize_head_mode("96_d_70", "row", "front_left", row_count=-3)
    self.assertEqual(mode.row_count, 1)

  def test_rectangle_row_and_column_counts_are_clamped_to_head_geometry(self):
    mode = normalize_head_mode("96_d_70", "rectangle", "back_left", row_count=99, column_count=99)
    self.assertEqual((mode.row_count, mode.column_count), (8, 12))


class AnchorTests(unittest.TestCase):
  """head_selected_ranges/head_anchor_cell for each of the four head corners."""

  HEAD_TYPE: HeadType = "96_d_70"

  def _mode(self, corner: str) -> HeadMode:
    return normalize_head_mode(self.HEAD_TYPE, "rectangle", corner, row_count=3, column_count=4)

  def test_back_left(self):
    mode = self._mode("back_left")
    self.assertEqual(head_selected_ranges(self.HEAD_TYPE, mode), ((0, 3), (0, 4)))
    self.assertEqual(head_anchor_cell(self.HEAD_TYPE, mode), (0, 0))

  def test_back_right(self):
    mode = self._mode("back_right")
    self.assertEqual(head_selected_ranges(self.HEAD_TYPE, mode), ((0, 3), (8, 12)))
    self.assertEqual(head_anchor_cell(self.HEAD_TYPE, mode), (0, 11))

  def test_front_left(self):
    mode = self._mode("front_left")
    self.assertEqual(head_selected_ranges(self.HEAD_TYPE, mode), ((5, 8), (0, 4)))
    self.assertEqual(head_anchor_cell(self.HEAD_TYPE, mode), (7, 0))

  def test_front_right(self):
    mode = self._mode("front_right")
    self.assertEqual(head_selected_ranges(self.HEAD_TYPE, mode), ((5, 8), (8, 12)))
    self.assertEqual(head_anchor_cell(self.HEAD_TYPE, mode), (7, 11))

  def test_all_barrels_anchor_is_origin(self):
    mode = normalize_head_mode(self.HEAD_TYPE, "all_barrels", "front_left")
    self.assertEqual(head_anchor_cell(self.HEAD_TYPE, mode), (0, 0))

  def test_column_anchor_row_is_always_zero(self):
    mode = normalize_head_mode(self.HEAD_TYPE, "column", "front_right", column_count=2)
    row, _ = head_anchor_cell(self.HEAD_TYPE, mode)
    self.assertEqual(row, 0)

  def test_row_anchor_col_is_always_zero(self):
    mode = normalize_head_mode(self.HEAD_TYPE, "row", "front_right", row_count=2)
    _, col = head_anchor_cell(self.HEAD_TYPE, mode)
    self.assertEqual(col, 0)


class ActiveHeadWellsTests(unittest.TestCase):
  def test_all_barrels_covers_the_whole_grid(self):
    mode = normalize_head_mode("8_d_lt", "all_barrels", "front_left")
    wells = active_head_wells("8_d_lt", mode)
    self.assertEqual(len(wells), 8)
    self.assertEqual(set(wells), {(r, 0) for r in range(8)})

  def test_rectangle_covers_exactly_its_block(self):
    mode = normalize_head_mode("96_d_70", "rectangle", "back_left", row_count=2, column_count=3)
    wells = active_head_wells("96_d_70", mode)
    self.assertEqual(set(wells), {(r, c) for r in range(2) for c in range(3)})


class TipboxMirrorAndAnchorTests(unittest.TestCase):
  def test_all_barrels_mirrors_to_back_left(self):
    mode = normalize_head_mode("96_d_70", "all_barrels", "front_left")
    self.assertEqual(tipbox_mirror_corner(mode), "back_left")

  def test_rectangle_mirror_is_opposite_corner(self):
    mode = normalize_head_mode("96_d_70", "rectangle", "back_left", row_count=2, column_count=2)
    self.assertEqual(tipbox_mirror_corner(mode), "front_right")

  def test_tipbox_anchor_cell_front_head_anchor(self):
    selection = TipSelection(
      location=1, row=2, col=3, row_count=2, column_count=2, head_anchor="front_right"
    )
    self.assertEqual(tipbox_anchor_cell(selection), (3, 4))

  def test_tipbox_anchor_cell_back_head_anchor(self):
    selection = TipSelection(
      location=1, row=2, col=3, row_count=2, column_count=2, head_anchor="back_left"
    )
    self.assertEqual(tipbox_anchor_cell(selection), (2, 3))


class SelectedAnchorRangesTests(unittest.TestCase):
  def test_clamps_to_stay_in_bounds(self):
    selection = TipSelection(location=0, row=10, col=10, row_count=2, column_count=2)
    row_range, col_range = selected_anchor_ranges(8, 12, selection)
    self.assertEqual(row_range, (6, 8))
    self.assertEqual(col_range, (10, 12))

  def test_negative_anchor_is_clamped_up_to_zero(self):
    selection = TipSelection(location=0, row=-5, col=-5, row_count=2, column_count=2)
    row_range, col_range = selected_anchor_ranges(8, 12, selection)
    self.assertEqual(row_range, (0, 2))
    self.assertEqual(col_range, (0, 2))


class DescribeHeadModeTests(unittest.TestCase):
  def test_all_barrels_description(self):
    mode = normalize_head_mode("96_d_70", "all_barrels", "front_left")
    self.assertEqual(describe_head_mode(mode), "All barrels")

  def test_rectangle_description_includes_dimensions(self):
    mode = normalize_head_mode("96_d_70", "rectangle", "back_left", row_count=2, column_count=3)
    self.assertIn("2x3", describe_head_mode(mode))


class SuggestedHeadModeTests(unittest.TestCase):
  def test_no_wells_selects_all_barrels(self):
    mode = suggested_head_mode("96_d_70", None)
    self.assertEqual(mode.subset_type, "all_barrels")

  def test_matching_well_count_selects_all_barrels(self):
    mode = suggested_head_mode("96_d_70", 96)
    self.assertEqual(mode.subset_type, "all_barrels")

  def test_96_head_striping_a_384_plate(self):
    mode = suggested_head_mode("96_d_70", 384)
    self.assertEqual(mode.subset_type, "rectangle")
    self.assertEqual((mode.row_count, mode.column_count), (8, 12))

  def test_8_head_on_a_96_plate_requests_a_column(self):
    # 8_d_lt is already a single column of 8, so the requested "column"
    # subset normalizes to all_barrels: there is nothing narrower than the
    # whole head to select among a head with only one column.
    mode = suggested_head_mode("8_d_lt", 96)
    self.assertEqual(mode.subset_type, "all_barrels")
    self.assertEqual((mode.row_count, mode.column_count), (8, 1))

  def test_16_head_on_a_384_plate_selects_a_row(self):
    mode = suggested_head_mode("16_d_st", 384)
    self.assertEqual(mode.subset_type, "row")


class LegalTipboxAnchorTests(unittest.TestCase):
  def test_pickup_requires_every_covered_cell_occupied(self):
    mode = normalize_head_mode("96_d_70", "column", "front_left", column_count=1)
    occupied = {(r, 0) for r in range(8)}
    self.assertTrue(is_legal_tipbox_anchor(8, 1, mode, occupied, 0, 0, purpose="pickup"))

  def test_pickup_rejects_anchor_missing_a_tip(self):
    mode = normalize_head_mode("96_d_70", "column", "front_left", column_count=1)
    occupied = {(r, 0) for r in range(8) if r != 3}
    self.assertFalse(is_legal_tipbox_anchor(8, 1, mode, occupied, 0, 0, purpose="pickup"))

  def test_return_to_empty_box_allows_any_anchor(self):
    mode = normalize_head_mode("96_d_70", "single_barrel", "back_left")
    anchors = legal_tipbox_anchors(8, 12, mode, set(), purpose="return")
    self.assertEqual(len(anchors), 96)

  def test_unknown_purpose_raises(self):
    mode = normalize_head_mode("96_d_70", "single_barrel", "back_left")
    with self.assertRaises(ValueError):
      is_legal_tipbox_anchor(8, 12, mode, set(), 0, 0, purpose="not-a-purpose")


class PlateFootprintTests(unittest.TestCase):
  def test_full_head_on_matching_plate_maps_1_to_1(self):
    mode = normalize_head_mode("96_d_70", "all_barrels", "front_left")
    wells = plate_footprint_wells("96_d_70", mode, 8, 12, 9.0, 9.0, 0, 0)
    self.assertEqual(set(wells), {(r, c) for r in range(8) for c in range(12)})

  def test_out_of_bounds_anchor_returns_empty(self):
    mode = normalize_head_mode("96_d_70", "all_barrels", "front_left")
    wells = plate_footprint_wells("96_d_70", mode, 8, 12, 9.0, 9.0, 5, 5)
    self.assertEqual(wells, [])

  def test_incompatible_pitch_returns_empty(self):
    mode = normalize_head_mode("96_d_70", "single_barrel", "back_left")
    wells = plate_footprint_wells("96_d_70", mode, 8, 12, 7.0, 7.0, 0, 0)
    self.assertEqual(wells, [])


if __name__ == "__main__":
  unittest.main()

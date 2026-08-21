import unittest

from pylabrobot.agilent.bravo.deck.geometry import (
  WellGeometry,
  a1_center_offset_from_teachpoint_mm,
  tipbox_anchor_offset_from_teachpoint_mm,
  well_center_offset_from_teachpoint_mm,
  well_geometry_from_metadata,
)
from pylabrobot.agilent.bravo.head_mode import TipSelection


class WellGeometryFromMetadataTests(unittest.TestCase):
  def test_96_well_plate_defaults(self):
    geometry = well_geometry_from_metadata({"rows": 8, "cols": 12})
    self.assertEqual(
      geometry,
      WellGeometry(
        rows=8, cols=12, pitch_x_mm=9.0, pitch_y_mm=9.0, offset_x_mm=0.0, offset_y_mm=0.0
      ),
    )

  def test_384_well_plate_defaults(self):
    geometry = well_geometry_from_metadata({"rows": 16, "cols": 24})
    self.assertEqual(
      geometry,
      WellGeometry(
        rows=16, cols=24, pitch_x_mm=4.5, pitch_y_mm=4.5, offset_x_mm=2.25, offset_y_mm=2.25
      ),
    )

  def test_1536_well_plate_defaults(self):
    geometry = well_geometry_from_metadata({"rows": 32, "cols": 48})
    self.assertEqual(
      geometry,
      WellGeometry(
        rows=32,
        cols=48,
        pitch_x_mm=2.25,
        pitch_y_mm=2.25,
        offset_x_mm=3.375,
        offset_y_mm=3.375,
      ),
    )

  def test_wells_count_infers_row_col_grid(self):
    self.assertEqual(well_geometry_from_metadata({"wells": 96}).rows, 8)
    self.assertEqual(well_geometry_from_metadata({"wells": 96}).cols, 12)
    self.assertEqual(well_geometry_from_metadata({"wells": 384}).rows, 16)
    self.assertEqual(well_geometry_from_metadata({"wells": 1536}).cols, 48)

  def test_nested_well_dimensions_mm_metadata_is_used(self):
    geometry = well_geometry_from_metadata({"well_dimensions_mm": {"rows": 8, "cols": 12}})
    self.assertEqual((geometry.rows, geometry.cols), (8, 12))

  def test_explicit_spacing_overrides_default_pitch(self):
    geometry = well_geometry_from_metadata(
      {"rows": 8, "cols": 12, "spacing_x_mm": 4.5, "spacing_y_mm": 4.5}
    )
    self.assertEqual((geometry.pitch_x_mm, geometry.pitch_y_mm), (4.5, 4.5))

  def test_explicit_offset_overrides_default_offset(self):
    geometry = well_geometry_from_metadata(
      {"rows": 16, "cols": 24, "offset_x_mm": 1.0, "offset_y_mm": 1.5}
    )
    self.assertEqual((geometry.offset_x_mm, geometry.offset_y_mm), (1.0, 1.5))

  def test_explicit_zero_offset_on_dense_plate_falls_back_to_default(self):
    # A 384-density plate with an explicit (0, 0) offset is treated as
    # "unset" and gets the standard 2.25 mm SBS offset instead.
    geometry = well_geometry_from_metadata(
      {"rows": 16, "cols": 24, "offset_x_mm": 0.0, "offset_y_mm": 0.0}
    )
    self.assertEqual((geometry.offset_x_mm, geometry.offset_y_mm), (2.25, 2.25))

  def test_none_metadata_yields_empty_geometry(self):
    geometry = well_geometry_from_metadata(None)
    self.assertEqual(geometry.rows, 0)
    self.assertEqual(geometry.cols, 0)


class A1CenterOffsetTests(unittest.TestCase):
  def test_96_well_a1_offset_is_zero(self):
    self.assertEqual(a1_center_offset_from_teachpoint_mm({"rows": 8, "cols": 12}), (0.0, 0.0))

  def test_384_well_a1_offset_is_negative_of_geometry_offset(self):
    offset = a1_center_offset_from_teachpoint_mm({"rows": 16, "cols": 24})
    self.assertEqual(offset, (-2.25, -2.25))


class WellCenterOffsetTests(unittest.TestCase):
  def test_row0_col0_equals_a1_offset(self):
    metadata = {"rows": 8, "cols": 12}
    self.assertEqual(
      well_center_offset_from_teachpoint_mm(metadata, row=0, col=0),
      a1_center_offset_from_teachpoint_mm(metadata),
    )

  def test_offset_advances_by_pitch_per_row_and_col(self):
    metadata = {"rows": 8, "cols": 12}
    base_x, base_y = a1_center_offset_from_teachpoint_mm(metadata)
    x, y = well_center_offset_from_teachpoint_mm(metadata, row=2, col=3)
    self.assertAlmostEqual(x, base_x + 3 * 9.0)
    self.assertAlmostEqual(y, base_y + 2 * 9.0)

  def test_384_well_offset_uses_384_pitch(self):
    metadata = {"rows": 16, "cols": 24}
    base_x, base_y = a1_center_offset_from_teachpoint_mm(metadata)
    x, y = well_center_offset_from_teachpoint_mm(metadata, row=1, col=1)
    self.assertAlmostEqual(x, base_x + 4.5)
    self.assertAlmostEqual(y, base_y + 4.5)


class TipboxAnchorOffsetTests(unittest.TestCase):
  def test_matches_well_center_offset_at_selection_row_col(self):
    metadata = {"rows": 8, "cols": 12}
    selection = TipSelection(location=1, row=2, col=3)
    self.assertEqual(
      tipbox_anchor_offset_from_teachpoint_mm(metadata, selection),
      well_center_offset_from_teachpoint_mm(metadata, row=2, col=3),
    )


if __name__ == "__main__":
  unittest.main()

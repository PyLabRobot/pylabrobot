import unittest

from pylabrobot.resources import (
  Revvity_384_wellplate_28ul_Ub,
  Thermo_TS_96_wellplate_1200ul_Rb,
  cellvis_24_wellplate_3600uL_Fb,
  cor_cos_6_wellplate_16800uL_Fb,
)

from .coordinate import Coordinate
from .plate import Lid, Plate


class TestLid(unittest.TestCase):
  def test_initialize_with_lid(self):
    lid = Lid("plate_lid", size_x=1, size_y=1, size_z=10, nesting_z_height=10)
    plate = Plate(
      "plate",
      size_x=1,
      size_y=1,
      size_z=15,
      ordered_items={},
      lid=lid,
    )
    plate.location = Coordinate.zero()

    assert plate.lid is not None
    self.assertEqual(plate.lid.name, "plate_lid")
    self.assertEqual(plate.lid.get_absolute_size_x(), 1)
    self.assertEqual(plate.lid.get_absolute_location(), Coordinate(0, 0, 5))

  def _create_plate_with_lid(self):
    """Helper to create a plate with a lid."""
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    lid = Lid(
      name="another_lid",
      size_x=plate.get_size_x(),
      size_y=plate.get_size_y(),
      size_z=plate.get_size_z(),
      nesting_z_height=plate.get_size_z(),
    )
    plate.assign_child_resource(lid, location=Coordinate(0, 0, 0))
    return plate

  def test_add_lid(self):
    plate = self._create_plate_with_lid()
    self.assertIsNotNone(plate.lid)

  def test_add_lid_with_existing_lid(self):
    plate = self._create_plate_with_lid()
    another_lid = Lid(
      name="another_lid",
      size_x=plate.get_size_x(),
      size_y=plate.get_size_y(),
      size_z=plate.get_size_z(),
      nesting_z_height=plate.get_size_z(),
    )
    with self.assertRaises(ValueError):
      plate.assign_child_resource(another_lid, location=Coordinate(0, 0, 0))

    plate = self._create_plate_with_lid()
    plate.unassign_child_resource(plate.lid)
    self.assertIsNone(plate.lid)


class TestStackingZHeight(unittest.TestCase):
  def test_default_is_none(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=15, ordered_items={})
    self.assertIsNone(plate.stacking_z_height)

  def test_stored(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=15, ordered_items={}, stacking_z_height=12.5)
    self.assertEqual(plate.stacking_z_height, 12.5)

  def test_serialize_round_trip(self):
    plate = Plate(
      "plate",
      size_x=1,
      size_y=1,
      size_z=15,
      ordered_items={},
      stacking_z_height=12.5,
      plate_type="non-skirted",
    )
    serialized = plate.serialize()
    self.assertEqual(serialized["stacking_z_height"], 12.5)
    self.assertEqual(serialized["plate_type"], "non-skirted")

    restored = Plate.deserialize(serialized)
    self.assertEqual(restored.stacking_z_height, 12.5)
    self.assertEqual(restored.plate_type, "non-skirted")

  def test_differs_in_equality(self):
    # `stacking_z_height` is a physical dimension, so plates that differ in it are not equal.
    def make(stacking_z_height):
      return Plate(
        "plate",
        size_x=1,
        size_y=1,
        size_z=15,
        ordered_items={},
        stacking_z_height=stacking_z_height,
      )

    self.assertEqual(make(12.5), make(12.5))
    self.assertNotEqual(make(12.5), make(13.0))
    self.assertNotEqual(make(12.5), make(None))

  def test_remains_hashable(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=15, ordered_items={}, stacking_z_height=12.5)
    # equal plates hash equally; the object stays usable in sets/dicts.
    other = Plate("plate", size_x=1, size_y=1, size_z=15, ordered_items={}, stacking_z_height=12.5)
    self.assertEqual(hash(plate), hash(other))
    self.assertIn(plate, {plate})


class TestQuadrants(unittest.TestCase):
  def setUp(self):
    self.example_6_wellplate = cor_cos_6_wellplate_16800uL_Fb(name="example_6_wellplate")
    self.example_24_wellplate = cellvis_24_wellplate_3600uL_Fb(name="example_24_wellplate")
    self.example_96_wellplate = Thermo_TS_96_wellplate_1200ul_Rb(name="example_96_wellplate")
    self.example_384_wellplate = Revvity_384_wellplate_28ul_Ub(name="example_384_wellplate")

  def test_checkerboard_column_major(self):
    self.assertEqual(
      [
        well.get_identifier()
        for well in self.example_96_wellplate.get_quadrant(
          quadrant="tl", quadrant_type="checkerboard", quadrant_internal_fill_order="column-major"
        )
      ],
      [
        "A1",
        "C1",
        "E1",
        "G1",
        "A3",
        "C3",
        "E3",
        "G3",
        "A5",
        "C5",
        "E5",
        "G5",
        "A7",
        "C7",
        "E7",
        "G7",
        "A9",
        "C9",
        "E9",
        "G9",
        "A11",
        "C11",
        "E11",
        "G11",
      ],
    )

  def test_checkerboard_row_major(self):
    self.assertEqual(
      [
        well.get_identifier()
        for well in self.example_96_wellplate.get_quadrant(
          quadrant="tl", quadrant_type="checkerboard", quadrant_internal_fill_order="row-major"
        )
      ],
      [
        "A1",
        "A3",
        "A5",
        "A7",
        "A9",
        "A11",
        "C1",
        "C3",
        "C5",
        "C7",
        "C9",
        "C11",
        "E1",
        "E3",
        "E5",
        "E7",
        "E9",
        "E11",
        "G1",
        "G3",
        "G5",
        "G7",
        "G9",
        "G11",
      ],
    )

  def test_block_column_major(self):
    self.assertEqual(
      [
        well.get_identifier()
        for well in self.example_96_wellplate.get_quadrant(
          quadrant="tl", quadrant_type="block", quadrant_internal_fill_order="column-major"
        )
      ],
      [
        "A1",
        "B1",
        "C1",
        "D1",
        "A2",
        "B2",
        "C2",
        "D2",
        "A3",
        "B3",
        "C3",
        "D3",
        "A4",
        "B4",
        "C4",
        "D4",
        "A5",
        "B5",
        "C5",
        "D5",
        "A6",
        "B6",
        "C6",
        "D6",
      ],
    )

  def test_block_row_major(self):
    self.assertEqual(
      [
        well.get_identifier()
        for well in self.example_96_wellplate.get_quadrant(
          quadrant="tl", quadrant_type="block", quadrant_internal_fill_order="row-major"
        )
      ],
      [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "B6",
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C6",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
      ],
    )

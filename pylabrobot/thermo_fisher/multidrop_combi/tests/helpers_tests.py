import unittest

from pylabrobot.resources import Plate, Well, create_ordered_items_2d
from pylabrobot.resources.well import CrossSectionType, WellBottomType
from pylabrobot.thermo_fisher.multidrop_combi.helpers import (
  plate_to_pla_params,
  plate_to_type_index,
  plate_well_count,
)


def _make_plate(
  num_items_x: int = 12,
  num_items_y: int = 8,
  size_z: float = 14.2,
  well_max_volume: float = 360.0,
  well_size_z: float = 10.67,
) -> Plate:
  """Create a test plate with the given parameters."""
  return Plate(
    name="test_plate",
    size_x=127.76,
    size_y=85.48,
    size_z=size_z,
    model="test",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=num_items_x,
      num_items_y=num_items_y,
      dx=10.0,
      dy=7.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.0,
      size_y=6.0,
      size_z=well_size_z,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=well_max_volume,
    ),
  )


class PlateToTypeIndexTests(unittest.TestCase):
  """Test factory plate type mapping."""

  def test_96_well_short(self):
    plate = _make_plate(num_items_x=12, num_items_y=8, size_z=14.0)
    self.assertEqual(plate_to_type_index(plate), 0)  # 15mm type

  def test_96_well_medium(self):
    plate = _make_plate(num_items_x=12, num_items_y=8, size_z=20.0)
    self.assertEqual(plate_to_type_index(plate), 1)  # 22mm type

  def test_96_well_tall(self):
    plate = _make_plate(num_items_x=12, num_items_y=8, size_z=40.0)
    self.assertEqual(plate_to_type_index(plate), 2)  # 44mm type

  def test_384_well_very_short(self):
    plate = _make_plate(num_items_x=24, num_items_y=16, size_z=7.0)
    self.assertEqual(plate_to_type_index(plate), 3)  # 7.5mm type

  def test_384_well_short(self):
    plate = _make_plate(num_items_x=24, num_items_y=16, size_z=10.0)
    self.assertEqual(plate_to_type_index(plate), 4)  # 10mm type

  def test_384_well_medium(self):
    plate = _make_plate(num_items_x=24, num_items_y=16, size_z=14.0)
    self.assertEqual(plate_to_type_index(plate), 5)  # 15mm type

  def test_384_well_tall(self):
    plate = _make_plate(num_items_x=24, num_items_y=16, size_z=25.0)
    self.assertEqual(plate_to_type_index(plate), 6)  # 22mm type

  def test_384_well_very_tall(self):
    plate = _make_plate(num_items_x=24, num_items_y=16, size_z=44.0)
    self.assertEqual(plate_to_type_index(plate), 7)  # 44mm type

  def test_1536_well_short(self):
    plate = _make_plate(num_items_x=48, num_items_y=32, size_z=5.0)
    self.assertEqual(plate_to_type_index(plate), 8)  # 5mm type

  def test_1536_well_tall(self):
    plate = _make_plate(num_items_x=48, num_items_y=32, size_z=10.0)
    self.assertEqual(plate_to_type_index(plate), 9)  # 10.5mm type

  def test_unsupported_well_count(self):
    plate = _make_plate(num_items_x=6, num_items_y=4)  # 24-well
    with self.assertRaises(ValueError) as ctx:
      plate_to_type_index(plate)
    self.assertIn("24", str(ctx.exception))

  def test_96_well_too_tall(self):
    plate = _make_plate(num_items_x=12, num_items_y=8, size_z=60.0)
    with self.assertRaises(ValueError):
      plate_to_type_index(plate)


class PlateToTypeIndexRealPlatesTests(unittest.TestCase):
  """Test with real PLR plate definitions."""

  def test_corning_96_well(self):
    from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

    plate = Cor_96_wellplate_360ul_Fb("test")
    self.assertEqual(plate_to_type_index(plate), 0)  # 14.2mm → type 0

  def test_biorad_384_well(self):
    from pylabrobot.resources.biorad.plates import BioRad_384_wellplate_50uL_Vb

    plate = BioRad_384_wellplate_50uL_Vb("test")
    self.assertEqual(plate_to_type_index(plate), 4)  # 10.4mm → type 4


class PlateToPlaParamsTests(unittest.TestCase):
  """Test PLA command parameter generation."""

  def test_96_well_params(self):
    plate = _make_plate(num_items_x=12, num_items_y=8, size_z=14.2, well_max_volume=360.0)
    params = plate_to_pla_params(plate)
    self.assertEqual(params["columns"], 12)
    self.assertEqual(params["rows"], 8)
    self.assertEqual(params["column_positions"], 12)
    self.assertEqual(params["row_positions"], 8)
    self.assertEqual(params["height"], 1420)  # 14.2mm * 100
    self.assertEqual(params["max_volume"], 360.0)

  def test_384_well_params(self):
    plate = _make_plate(num_items_x=24, num_items_y=16, size_z=10.4, well_max_volume=50.0)
    params = plate_to_pla_params(plate)
    self.assertEqual(params["columns"], 24)
    self.assertEqual(params["rows"], 16)
    self.assertEqual(params["height"], 1040)
    self.assertEqual(params["max_volume"], 50.0)

  def test_real_corning_96_well(self):
    from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

    plate = Cor_96_wellplate_360ul_Fb("test")
    params = plate_to_pla_params(plate)
    self.assertEqual(params["columns"], 12)
    self.assertEqual(params["rows"], 8)
    self.assertEqual(params["height"], 1420)
    self.assertEqual(params["max_volume"], 360.0)


class PlaParamsValidationTests(unittest.TestCase):
  """Test parameter validation in plate_to_pla_params."""

  def test_too_many_columns(self):
    plate = _make_plate(num_items_x=49, num_items_y=8, size_z=14.0)
    with self.assertRaises(ValueError) as ctx:
      plate_to_pla_params(plate)
    self.assertIn("49 columns", str(ctx.exception))
    self.assertIn("48", str(ctx.exception))

  def test_too_many_rows(self):
    plate = _make_plate(num_items_x=12, num_items_y=33, size_z=14.0)
    with self.assertRaises(ValueError) as ctx:
      plate_to_pla_params(plate)
    self.assertIn("33 rows", str(ctx.exception))
    self.assertIn("32", str(ctx.exception))

  def test_height_too_low(self):
    plate = _make_plate(size_z=4.0)  # 4mm < 5mm minimum
    with self.assertRaises(ValueError) as ctx:
      plate_to_pla_params(plate)
    self.assertIn("4.0mm", str(ctx.exception))
    self.assertIn("minimum", str(ctx.exception))

  def test_height_too_high(self):
    plate = _make_plate(size_z=60.0)  # 60mm > 55mm maximum
    with self.assertRaises(ValueError) as ctx:
      plate_to_pla_params(plate)
    self.assertIn("60.0mm", str(ctx.exception))
    self.assertIn("maximum", str(ctx.exception))

  def test_well_volume_too_high(self):
    plate = _make_plate(well_max_volume=3000.0)  # 3000uL > 2500uL max
    with self.assertRaises(ValueError) as ctx:
      plate_to_pla_params(plate)
    self.assertIn("3000", str(ctx.exception))
    self.assertIn("2500", str(ctx.exception))

  def test_height_at_minimum_boundary(self):
    plate = _make_plate(size_z=5.0)  # exactly 5mm = 500 hundredths
    params = plate_to_pla_params(plate)
    self.assertEqual(params["height"], 500)

  def test_height_at_maximum_boundary(self):
    plate = _make_plate(size_z=55.0)  # exactly 55mm = 5500 hundredths
    params = plate_to_pla_params(plate)
    self.assertEqual(params["height"], 5500)

  def test_volume_at_maximum_boundary(self):
    plate = _make_plate(well_max_volume=2500.0)  # exactly 2500uL
    params = plate_to_pla_params(plate)
    self.assertEqual(params["max_volume"], 2500.0)


class PlateWellCountTests(unittest.TestCase):
  def test_96_well(self):
    plate = _make_plate(num_items_x=12, num_items_y=8)
    self.assertEqual(plate_well_count(plate), 96)

  def test_384_well(self):
    plate = _make_plate(num_items_x=24, num_items_y=16)
    self.assertEqual(plate_well_count(plate), 384)

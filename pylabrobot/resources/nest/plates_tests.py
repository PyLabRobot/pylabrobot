import math
import unittest

from pylabrobot.resources import Coordinate, Plate, nest_96_wellplate_200uL_Fb
from pylabrobot.resources.well import CrossSectionType, WellBottomType


class TestNest96Wellplate200uLFb(unittest.TestCase):
  def setUp(self):
    self.plate = nest_96_wellplate_200uL_Fb(name="plate")
    self.plate.location = Coordinate.zero()

  def test_footprint(self):
    self.assertEqual(self.plate.get_size_x(), 127.56)
    self.assertEqual(self.plate.get_size_y(), 85.36)
    self.assertEqual(self.plate.get_size_z(), 14.30)

  def test_layout(self):
    self.assertEqual(self.plate.num_items_x, 12)
    self.assertEqual(self.plate.num_items_y, 8)
    self.assertEqual(len(self.plate.get_all_items()), 96)

  def test_well_centers(self):
    # The data sheet places the center of A1 14.28 mm from the left and 11.18 mm from the back edge,
    # at a pitch of 9.00 mm; H12 is 99.00 mm to the right of A1 and 63.00 mm in front of it.
    a1 = self.plate.get_well("A1").get_absolute_location("c", "c", "b")
    self.assertAlmostEqual(a1.x, 14.28)
    self.assertAlmostEqual(a1.y, 85.36 - 11.18)
    h12 = self.plate.get_well("H12").get_absolute_location("c", "c", "b")
    self.assertAlmostEqual(h12.x, 14.28 + 99.00)
    self.assertAlmostEqual(h12.y, 85.36 - 11.18 - 63.00)

  def test_well_geometry(self):
    well = self.plate.get_well("A1")
    self.assertEqual(well.get_size_x(), 6.85)
    self.assertEqual(well.get_size_y(), 6.85)
    self.assertEqual(well.bottom_type, WellBottomType.FLAT)
    self.assertEqual(well.cross_section_type, CrossSectionType.CIRCLE)
    self.assertEqual(well.max_volume, 372.4)

    # The cavity is 10.80 mm deep: the rim is at the plate height and the floor is 1.20 mm thick.
    rim = well.get_absolute_location("c", "c", "t").z
    cavity_bottom = well.get_absolute_location().z + well.material_z_thickness
    self.assertAlmostEqual(rim, 14.30)
    self.assertAlmostEqual(cavity_bottom, 3.50)
    self.assertAlmostEqual(rim - cavity_bottom, 10.80)

  def test_height_volume_data(self):
    well = self.plate.get_well("A1")
    cavity_depth = well.get_size_z() - well.material_z_thickness
    self.assertEqual(well.compute_volume_from_height(0.0), 0.0)
    assert well.height_volume_data is not None
    self.assertAlmostEqual(max(well.height_volume_data), cavity_depth)
    self.assertEqual(max(well.height_volume_data.values()), well.max_volume)

    # The well is a truncated cone, 6.40 mm across the floor and 6.85 mm across the rim.
    def cone_volume(h: float) -> float:
      r_floor, r_rim = 6.40 / 2, 6.85 / 2
      r = r_floor + (r_rim - r_floor) * h / cavity_depth
      return math.pi * h * (r_floor**2 + r_floor * r + r**2) / 3

    for h, volume in well.height_volume_data.items():
      self.assertAlmostEqual(volume, cone_volume(h), delta=0.05)
    self.assertAlmostEqual(well.compute_height_from_volume(200), 5.98, places=2)

  def test_serialize_round_trip(self):
    plate = nest_96_wellplate_200uL_Fb(name="another_plate")
    self.assertEqual(Plate.deserialize(plate.serialize()), plate)

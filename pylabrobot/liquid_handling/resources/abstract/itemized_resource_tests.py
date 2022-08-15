import unittest

from pylabrobot.liquid_handling.resources.abstract import Coordinate, Plate


class TestItemizedResource(unittest.TestCase):
  def test_initialize_with_wells(self):
    self.plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0,
      one_dot_max=1, lid_height=None, num_wells_x=1, num_wells_y=1, well_size_x=1, well_size_y=1)
    self.assertEqual(len(self.plate._items), 1)
    self.assertEqual(self.plate._items[0].name, "plate_well_0_0")

  def test_get_item_int(self):
    self.plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0,
      one_dot_max=1, lid_height=None, num_wells_x=1, num_wells_y=1, well_size_x=1, well_size_y=1)
    self.assertEqual(len(self.plate._items), 1)
    self.assertEqual(self.plate.get_item(0).name, "plate_well_0_0")

  def test_get_item_str(self):
    self.plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0,
      one_dot_max=1, lid_height=None, num_wells_x=12, num_wells_y=8, well_size_x=1, well_size_y=1)
    self.assertEqual(len(self.plate._items), 96)
    self.assertEqual(self.plate.get_item("A1").name, "plate_well_0_0")
    self.assertEqual(self.plate.get_item("B1").name, "plate_well_0_1")
    self.assertEqual(self.plate.get_item("A2").name, "plate_well_1_0")

  def test_well_get_absolute_location(self):
    self.plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0,
      one_dot_max=1, lid_height=None, num_wells_x=12, num_wells_y=8, well_size_x=9, well_size_y=9)
    self.assertEqual(self.plate.get_item(0).get_absolute_location(), Coordinate(0, 0, 0))
    self.assertEqual(self.plate.get_item(7).get_absolute_location(), Coordinate(0, -63, 0))
    self.assertEqual(self.plate.get_item(8).get_absolute_location(), Coordinate(9, 0, 0))
    self.assertEqual(self.plate.get_item(17).get_absolute_location(), Coordinate(18, -9, 0))
    self.assertEqual(self.plate.get_item(95).get_absolute_location(), Coordinate(99, -63, 0))


if __name__ == "__main__":
  unittest.main()

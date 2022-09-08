import unittest

from pylabrobot.liquid_handling.resources.abstract import Coordinate, Plate


class TestItemizedResource(unittest.TestCase):
  """ Tests for ItemizedResource """

  def setUp(self) -> None:
    self.plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0,
      one_dot_max=1, lid_height=None, num_items_x=12, num_items_y=8, well_size_x=9, well_size_y=9)
    return super().setUp()

  def test_initialize_with_wells(self):
    # pylint: disable=protected-access
    self.assertEqual(len(self.plate._items), 96)
    self.assertEqual(self.plate._items[0].name, "plate_well_0_0")
    self.assertEqual(self.plate._items[95].name, "plate_well_11_7")

  def test_get_item_int(self):
    self.assertEqual(self.plate.get_item(0).name, "plate_well_0_0")
    self.assertEqual(self.plate.get_item(95).name, "plate_well_11_7")

  def test_get_item_str(self):
    self.assertEqual(self.plate.get_item("A1").name, "plate_well_0_0")
    self.assertEqual(self.plate.get_item("B1").name, "plate_well_0_1")
    self.assertEqual(self.plate.get_item("A2").name, "plate_well_1_0")

  def test_well_get_absolute_location(self):
    self.assertEqual(self.plate.get_item(0).get_absolute_location(), Coordinate(0, 63, 0))
    self.assertEqual(self.plate.get_item(7).get_absolute_location(), Coordinate(0, 0, 0))
    self.assertEqual(self.plate.get_item(8).get_absolute_location(), Coordinate(9, 63, 0))
    self.assertEqual(self.plate.get_item(17).get_absolute_location(), Coordinate(18, 54, 0))
    self.assertEqual(self.plate.get_item(95).get_absolute_location(), Coordinate(99, 0, 0))

  def test_getitem_int(self):
    self.assertEqual(self.plate[0][0].name, "plate_well_0_0")

  def test_getitem_str(self):
    self.assertEqual(self.plate["A1"][0].name, "plate_well_0_0")

  def test_getitem_slice(self):
    self.assertEqual([w.name for w in self.plate[0:7]], ["plate_well_0_0", "plate_well_0_1",
      "plate_well_0_2", "plate_well_0_3", "plate_well_0_4", "plate_well_0_5", "plate_well_0_6"])

  def test_getitem_str_range(self):
    self.assertEqual([w.name for w in self.plate["A1:B2"]], ["plate_well_0_0", "plate_well_1_0",
      "plate_well_0_1", "plate_well_1_1"])

  def test_getitem_tuple_int(self):
    self.assertEqual([w.name for w in self.plate[0, 4, 1]], ["plate_well_0_0", "plate_well_0_4",
      "plate_well_0_1"])

  def test_getitem_tuple_str(self):
    self.assertEqual([w.name for w in self.plate["A1", "B2", "A2"]], ["plate_well_0_0",
      "plate_well_1_1", "plate_well_1_0"])


if __name__ == "__main__":
  unittest.main()

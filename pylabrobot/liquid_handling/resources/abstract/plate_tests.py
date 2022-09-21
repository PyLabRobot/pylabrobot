""" Tests for Resource """
# pylint: disable=missing-class-docstring

import unittest

from .coordinate import Coordinate
from .itemized_resource import create_equally_spaced
from .plate import Plate, Lid, Well


class TestLid(unittest.TestCase):
  def test_initialize_with_lid(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, one_dot_max=1, lid_height=10,
      items=create_equally_spaced(Well, dx=0, dy=0, dz=0,
        num_items_x=1, num_items_y=1, item_size_x=1, item_size_y=1),
      with_lid=True)

    self.assertIsNotNone(plate.lid)
    self.assertEqual(plate.lid.name, "plate_lid")
    self.assertEqual(plate.lid.get_size_x(), 1)
    # fix Coordinate(0, 0, 1) ?
    self.assertEqual(plate.lid.get_absolute_location(), Coordinate(0, 0, 10))

  def test_add_lid(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, one_dot_max=1, lid_height=10,
      items=create_equally_spaced(Well, dx=0, dy=0, dz=0,
        num_items_x=1, num_items_y=1, item_size_x=1, item_size_y=1))
    lid = Lid(name="another_lid", size_x=plate.get_size_x(), size_y=plate.get_size_y(),
      size_z=plate.get_size_z(), location=Coordinate(0, 0, 0))
    plate.assign_child_resource(lid)
    return plate

  def test_add_lid_with_existing_lid(self):
    plate = self.test_add_lid()
    another_lid = Lid(name="another_lid", size_x=plate.get_size_x(), size_y=plate.get_size_y(),
    size_z=plate.get_size_z(), location=Coordinate(0, 0, 0))
    with self.assertRaises(ValueError):
      plate.assign_child_resource(another_lid)

    plate = self.test_add_lid()
    plate.unassign_child_resource(plate.lid)
    self.assertIsNone(plate.lid)


if __name__ == "__main__":
  unittest.main()

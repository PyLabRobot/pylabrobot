""" Tests for Resource """
# pylint: disable=missing-class-docstring

import unittest

from .coordinate import Coordinate
from .plate import Plate, Lid


class TestLid(unittest.TestCase):
  def test_initialize_with_lid(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=15, lid_height=10, items=[],
      with_lid=True)
    plate.location = Coordinate.zero()

    assert plate.lid is not None
    self.assertEqual(plate.lid.name, "plate_lid")
    self.assertEqual(plate.lid.get_size_x(), 1)
    self.assertEqual(plate.lid.get_absolute_location(), Coordinate(0, 0, 5))

  def test_add_lid(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, lid_height=10, items=[])
    lid = Lid(name="another_lid", size_x=plate.get_size_x(), size_y=plate.get_size_y(),
      size_z=plate.get_size_z())
    plate.assign_child_resource(lid, location=Coordinate(0, 0, 0))
    return plate

  def test_add_lid_with_existing_lid(self):
    plate = self.test_add_lid()
    another_lid = Lid(name="another_lid", size_x=plate.get_size_x(), size_y=plate.get_size_y(),
    size_z=plate.get_size_z())
    with self.assertRaises(ValueError):
      plate.assign_child_resource(another_lid, location=Coordinate(0, 0, 0))

    plate = self.test_add_lid()
    plate.unassign_child_resource(plate.lid)
    self.assertIsNone(plate.lid)

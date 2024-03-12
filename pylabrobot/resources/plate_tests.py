# pylint: disable=missing-class-docstring

import unittest

from .coordinate import Coordinate
from .itemized_resource import create_equally_spaced
from .plate import Plate, Lid
from .well import Well


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

  def test_quadrant(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, items=create_equally_spaced(Well,
      num_items_x=24, num_items_y=16,
      dx=1, dy=1, dz=1,
      item_dx=1, item_dy=1,
      size_x=1, size_y=1, size_z=1,
    ))
    self.assertIn(plate.get_well("A1"), plate.get_quadrant(1))
    self.assertEqual(len(plate.get_quadrant(1)), 384//4)

    self.assertIn(plate.get_well("A2"), plate.get_quadrant(2))
    self.assertEqual(len(plate.get_quadrant(2)), 384//4)

    self.assertIn(plate.get_well("B1"), plate.get_quadrant(3))
    self.assertEqual(len(plate.get_quadrant(3)), 384//4)

    self.assertIn(plate.get_well("B2"), plate.get_quadrant(4))
    self.assertEqual(len(plate.get_quadrant(4)), 384//4)

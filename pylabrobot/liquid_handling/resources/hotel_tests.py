""" Tests for Resource """
# pylint: disable=missing-class-docstring

import unittest

from pylabrobot.liquid_handling.resources.abstract.coordinate import Coordinate
from pylabrobot.liquid_handling.resources.abstract.plate import Plate
from pylabrobot.liquid_handling.resources.hotel import Hotel


class TestHotel(unittest.TestCase):
  def test_add_item(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0,
      one_dot_max=1, lid_height=1, num_items_x=1, num_items_y=1, well_size_x=1, well_size_y=1)

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate)

    self.assertEqual(hotel.get_top_item(), plate)

  def test_get_absolute_location_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0,
      one_dot_max=1, lid_height=1, num_items_x=1, num_items_y=1, well_size_x=1, well_size_y=1)

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate)

    self.assertEqual(plate.get_absolute_location(), Coordinate(0, 0, 0))

  def test_get_absolute_location_lid(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0, one_dot_max=1,
      lid_height=1, with_lid=True, num_items_x=1, num_items_y=1, well_size_x=1, well_size_y=1)

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate.lid)

    self.assertEqual(hotel.get_top_item().get_absolute_location(), Coordinate(0, 0, 0))

  def test_get_absolute_location_stack_height(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0, one_dot_max=1,
      lid_height=1, with_lid=True, num_items_x=1, num_items_y=1, well_size_x=1, well_size_y=1)
    plate2 = Plate("plate2", size_x=1, size_y=1, size_z=1, dx=0, dy=0, dz=0, one_dot_max=1,
      lid_height=1, with_lid=True, num_items_x=1, num_items_y=1, well_size_x=1, well_size_y=1)

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate.lid)
    self.assertEqual(hotel.get_top_item().get_absolute_location(), Coordinate(0, 0, 0))

    hotel.assign_child_resource(plate2.lid)
    self.assertEqual(hotel.get_top_item().get_absolute_location(), Coordinate(0, 0, 1))


if __name__ == "__main__":
  unittest.main()

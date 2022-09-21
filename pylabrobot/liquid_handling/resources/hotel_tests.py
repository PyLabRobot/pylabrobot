""" Tests for Resource """
# pylint: disable=missing-class-docstring

import unittest

from pylabrobot.liquid_handling.resources.abstract.coordinate import Coordinate
from pylabrobot.liquid_handling.resources.abstract import Plate, Well, create_equally_spaced
from pylabrobot.liquid_handling.resources.hotel import Hotel


class TestHotel(unittest.TestCase):
  def test_add_item(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, one_dot_max=1, lid_height=1,
      items=create_equally_spaced(Well, dx=0, dy=0, dz=0,
        num_items_x=1, num_items_y=1, item_size_x=1, item_size_y=1))

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate)

    self.assertEqual(hotel.get_top_item(), plate)

  def test_get_absolute_location_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, one_dot_max=1, lid_height=1,
      items=create_equally_spaced(Well, dx=0, dy=0, dz=0,
        num_items_x=1, num_items_y=1, item_size_x=1, item_size_y=1))

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate)

    self.assertEqual(plate.get_absolute_location(), Coordinate(0, 0, 0))

  def test_get_absolute_location_lid(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, one_dot_max=1, lid_height=1,
      items=create_equally_spaced(Well, dx=0, dy=0, dz=0,
        num_items_x=1, num_items_y=1, item_size_x=1, item_size_y=1),
      with_lid=True)

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate.lid)

    self.assertEqual(hotel.get_top_item().get_absolute_location(), Coordinate(0, 0, 0))

  def test_get_absolute_location_stack_height(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, one_dot_max=1, lid_height=1,
      items=create_equally_spaced(Well, dx=0, dy=0, dz=0,
        num_items_x=1, num_items_y=1, item_size_x=1, item_size_y=1),
      with_lid=True)
    plate2 = Plate("plate2", size_x=1, size_y=1, size_z=1, one_dot_max=1, lid_height=1,
      items=create_equally_spaced(Well, dx=0, dy=0, dz=0,
        num_items_x=1, num_items_y=1, item_size_x=1, item_size_y=1),
      with_lid=True)

    hotel = Hotel("hotel", size_x=135.0, size_y=135.0, size_z=1, location=Coordinate(0, 0, 0))
    hotel.assign_child_resource(plate.lid)
    self.assertEqual(hotel.get_top_item().get_absolute_location(), Coordinate(0, 0, 0))

    hotel.assign_child_resource(plate2.lid)
    self.assertEqual(hotel.get_top_item().get_absolute_location(), Coordinate(0, 0, 1))


if __name__ == "__main__":
  unittest.main()

"""Tests for ResourceStack resource"""

import unittest

from pylabrobot.resources import Coordinate, Lid, Plate, Resource

from .resource_stack import ResourceStack


class ResourceStackTests(unittest.TestCase):
  def test_create(self):
    stack = ResourceStack(
      "stack",
      "z",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(len(stack.children), 2)

  def test_create_x(self):
    stack = ResourceStack(
      "stack",
      "x",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 0))
    self.assertEqual(stack.get_resource("B").location, Coordinate(10, 0, 0))

    self.assertEqual(stack.get_absolute_size_x(), 20)
    self.assertEqual(stack.get_absolute_size_y(), 10)
    self.assertEqual(stack.get_absolute_size_z(), 10)

  def test_create_y(self):
    stack = ResourceStack(
      "stack",
      "y",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 0))
    self.assertEqual(stack.get_resource("B").location, Coordinate(0, 10, 0))

    self.assertEqual(stack.get_absolute_size_x(), 10)
    self.assertEqual(stack.get_absolute_size_y(), 20)
    self.assertEqual(stack.get_absolute_size_z(), 10)

  def test_create_z(self):
    stack = ResourceStack(
      "stack",
      "z",
      [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
      ],
    )
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 10))
    self.assertEqual(stack.get_resource("B").location, Coordinate(0, 0, 0))

    self.assertEqual(stack.get_absolute_size_x(), 10)
    self.assertEqual(stack.get_absolute_size_y(), 10)
    self.assertEqual(stack.get_absolute_size_z(), 20)

  def test_get_size_empty_stack(self):
    stack = ResourceStack("stack", "z")
    self.assertEqual(stack.get_absolute_size_x(), 0)
    self.assertEqual(stack.get_absolute_size_y(), 0)
    self.assertEqual(stack.get_absolute_size_z(), 0)

  # Tests for using ResourceStack as a stacking area, like the one near the washer on the STARs.

  def test_add_item(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.assign_child_resource(plate)
    self.assertEqual(stacking_area.get_top_item(), plate)

  def test_get_absolute_location_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.location = Coordinate.zero()
    stacking_area.assign_child_resource(plate)
    self.assertEqual(plate.get_absolute_location(), Coordinate(0, 0, 0))

  def test_get_absolute_location_lid(self):
    lid = Lid(name="lid", size_x=1, size_y=1, size_z=1, nesting_z_height=0)
    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.location = Coordinate.zero()
    stacking_area.assign_child_resource(lid)
    self.assertEqual(
      stacking_area.get_top_item().get_absolute_location(),
      Coordinate(0, 0, 0),
    )

  def test_get_absolute_location_stack_height(self):
    lid = Lid(name="lid", size_x=1, size_y=1, size_z=1, nesting_z_height=0)
    lid2 = Lid(name="lid2", size_x=1, size_y=1, size_z=1, nesting_z_height=0)

    stacking_area = ResourceStack("stacking_area", "z")
    stacking_area.location = Coordinate.zero()
    stacking_area.assign_child_resource(lid)
    top_item = stacking_area.get_top_item()
    assert top_item is not None
    self.assertEqual(top_item.get_absolute_location(), Coordinate(0, 0, 0))

    stacking_area.assign_child_resource(lid2)
    top_item = stacking_area.get_top_item()
    assert top_item is not None
    self.assertEqual(top_item.get_absolute_location(), Coordinate(0, 0, 1))

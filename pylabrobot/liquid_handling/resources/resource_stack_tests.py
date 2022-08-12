""" Tests for Carrier resource """
# pylint: disable=missing-class-docstring

import unittest

from .abstract.coordinate import Coordinate
from .abstract.resource import Resource
from .resource_stack import ResourceStack


class ResourceStackTests(unittest.TestCase):
  def test_create(self):
    stack = ResourceStack("stack", "z", [
      Resource("A", size_x=10, size_y=10, size_z=10),
      Resource("B", size_x=10, size_y=10, size_z=10),
    ])
    self.assertEqual(len(stack.children), 2)

  def test_create_x(self):
    stack = ResourceStack("stack", "x", [
      Resource("A", size_x=10, size_y=10, size_z=10),
      Resource("B", size_x=10, size_y=10, size_z=10),
    ])
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 0))
    self.assertEqual(stack.get_resource("B").location, Coordinate(10, 0, 0))

    self.assertEqual(stack.get_size_x(), 20)
    self.assertEqual(stack.get_size_y(), 10)
    self.assertEqual(stack.get_size_z(), 10)

  def test_create_y(self):
    stack = ResourceStack("stack", "y", [
      Resource("A", size_x=10, size_y=10, size_z=10),
      Resource("B", size_x=10, size_y=10, size_z=10),
    ])
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 0))
    self.assertEqual(stack.get_resource("B").location, Coordinate(0, 10, 0))

    self.assertEqual(stack.get_size_x(), 10)
    self.assertEqual(stack.get_size_y(), 20)
    self.assertEqual(stack.get_size_z(), 10)

  def test_create_z(self):
    stack = ResourceStack("stack", "z", [
      Resource("A", size_x=10, size_y=10, size_z=10),
      Resource("B", size_x=10, size_y=10, size_z=10),
    ])
    print(stack.children)
    self.assertEqual(stack.get_resource("A").location, Coordinate(0, 0, 10))
    self.assertEqual(stack.get_resource("B").location, Coordinate(0, 0, 0))

    self.assertEqual(stack.get_size_x(), 10)
    self.assertEqual(stack.get_size_y(), 10)
    self.assertEqual(stack.get_size_z(), 20)

if __name__ == "__main__":
  unittest.main()

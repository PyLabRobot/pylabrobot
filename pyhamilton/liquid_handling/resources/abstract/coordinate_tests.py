""" Tests for Coordinate """
# pylint: disable=missing-class-docstring

import unittest

from .coordinate import Coordinate


class TestCoordinate(unittest.TestCase):
  def setUp(self):
    self.a = Coordinate(1, 2, 3)
    self.b = Coordinate(10, 10, 10)
    self.c = Coordinate(0, 0, 0)
    self.d = Coordinate(None, None, None)

  def test_addition(self):
    self.assertEqual(self.a, self.a)
    self.assertEqual(self.a + self.c, self.a)
    self.assertEqual(self.a + self.b, Coordinate(11, 12, 13))
    self.assertEqual(self.b + self.b, Coordinate(20, 20, 20))

  def test_none_coordinates(self):
    self.assertEqual(self.d, self.d)
    self.assertEqual(self.a + self.d, self.a)
    self.assertEqual(self.d + self.a, self.a)
    self.assertEqual(self.a - self.d, self.a)

  def test_to_string(self):
    self.assertEqual(f"{self.a}", "(001.000, 002.000, 003.000)")
    self.assertEqual(f"{self.d}", "(None, None, None)")

  def test_serialization(self):
    self.assertEqual(self.a.serialize(), dict(x=1, y=2, z=3))
    self.assertEqual(self.a, Coordinate.deserialize(self.a.serialize()))


if __name__ == "__main__":
  unittest.main()

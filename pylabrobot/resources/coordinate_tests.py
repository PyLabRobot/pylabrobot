"""Tests for Coordinate"""

import unittest

from pylabrobot.serializer import deserialize, serialize

from .coordinate import Coordinate


class TestCoordinate(unittest.TestCase):
  def setUp(self):
    self.a = Coordinate(1, 2, 3)
    self.b = Coordinate(10, 10, 10)
    self.c = Coordinate(0, 0, 0)

  def test_addition(self):
    self.assertEqual(self.a, self.a)
    self.assertEqual(self.a + self.c, self.a)
    self.assertEqual(self.a + self.b, Coordinate(11, 12, 13))
    self.assertEqual(self.b + self.b, Coordinate(20, 20, 20))

  def test_to_string(self):
    self.assertEqual(f"{self.a}", "Coordinate(001.000, 002.000, 003.000)")

  def test_serialization(self):
    self.assertEqual(
      serialize(self.a),
      {"x": 1, "y": 2, "z": 3, "type": "Coordinate"},
    )
    self.assertEqual(self.a, deserialize(serialize(self.a)))

  def test_unpacking(self):
    x, y, z = self.a
    self.assertEqual((x, y, z), (1, 2, 3))

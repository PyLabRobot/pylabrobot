""" Tests for positions """

import unittest

from pylabrobot.utils import (
  string_to_position,
  string_to_index,
  string_to_indices,
  string_to_pattern
)


class TestPositions(unittest.TestCase):
  """ Tests for position utilities. """

  def setUp(self) -> None:
    super().setUp()
    self.maxDiff = None

  def test_string_to_pattern(self):
    self.assertEqual(string_to_position("A1"), (0, 0))
    self.assertEqual(string_to_position("A3"), (0, 2))
    self.assertEqual(string_to_position("C1"), (2, 0))

  def test_string_to_index(self):
    self.assertEqual(string_to_index("A1", num_rows=8, num_columns=12), 0)
    self.assertEqual(string_to_index("A3", num_rows=8, num_columns=12), 16)
    self.assertEqual(string_to_index("C1", num_rows=8, num_columns=12), 2)

  def test_string_to_indices(self):
    self.assertEqual(string_to_indices("A1:A3", num_rows=8), [0, 8, 16])
    self.assertEqual(string_to_indices("A1:C1", num_rows=8), [0, 1, 2])
    self.assertEqual(string_to_indices("A1:C3", num_rows=8),
      [0, 8, 16, 1, 9, 17, 2, 10, 18])

  def test_string_to_indices_reverse(self):
    self.assertEqual(string_to_indices("A3:A1", num_rows=8), [16, 8, 0])

  def test_string_range_to_pattern(self):
    self.assertEqual(string_to_pattern("A1:C3", num_rows=8, num_columns=12),
      [[True]*3 + [False]*9]*3 + [[False]*12]*5)
    self.assertEqual(string_to_pattern("A1:A3", num_rows=8, num_columns=12),
      [[True]*3 + [False]*9] + [[False]*12]*7)
    self.assertEqual(string_to_pattern("A1:C1", num_rows=8, num_columns=12),
      [[True] + [False] * 11]*3 + [[False]*12]*5)

  def test_num_rows(self):
    self.assertEqual(string_to_index("A2", num_rows=2, num_columns=12), 2)

    with self.assertRaises(AssertionError):
      string_to_index("C1", num_rows=2, num_columns=12)

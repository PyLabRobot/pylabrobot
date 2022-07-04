""" Tests for positions"""

import unittest

from pyhamilton.utils import string_to_position, string_to_pattern


class TestLiquidHandlerNew(unittest.TestCase):
  def setUp(self) -> None:
    super().setUp()
    self.maxDiff = None

  def test_string_to_pattern(self):
    # pylint: disable=protected-access
    self.assertEqual(string_to_position("A1"), (0, 0))
    self.assertEqual(string_to_position("A3"), (0, 2))
    self.assertEqual(string_to_position("C1"), (2, 0))

  def test_string_range_to_pattern(self):
    # pylint: disable=protected-access
    self.assertEqual(string_to_pattern("A1:C3"),
      [[True]*3 + [False]*9]*3 + [[False]*12]*5)
    self.assertEqual(string_to_pattern("A1:A3"),
      [[True]*3 + [False]*9] + [[False]*12]*7)
    self.assertEqual(string_to_pattern("A1:C1"),
      [[True] + [False] * 11]*3 + [[False]*12]*5)


if __name__ == "__main__":
  unittest.main()

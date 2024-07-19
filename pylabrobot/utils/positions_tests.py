""" Tests for positions """

import unittest

from pylabrobot.utils import expand_string_range


class TestPositions(unittest.TestCase):
  """ Tests for position utilities. """

  def setUp(self) -> None:
    super().setUp()
    self.maxDiff = None

  def test_expand_string_range(self):
    self.assertEqual(expand_string_range("A1:A3"), ["A1", "A2", "A3"])
    self.assertEqual(expand_string_range("A1:C1"), ["A1", "B1", "C1"])
    self.assertEqual(expand_string_range("A1:C3"), ["A1", "A2", "A3",
                                                    "B1", "B2", "B3",
                                                    "C1", "C2", "C3"])

  def test_expand_string_range_reverse(self):
    self.assertEqual(expand_string_range("C3:C1"), ["C3", "C2", "C1"])
    self.assertEqual(expand_string_range("C1:A1"), ["C1", "B1", "A1"])

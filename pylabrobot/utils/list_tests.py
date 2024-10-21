""" Tests for positions """

import unittest

from pylabrobot.utils import assert_shape, reshape_2d


class TestListUtils(unittest.TestCase):
  """ Tests for list utilities. """

  def test_assert_shape(self):
    assert_shape([[1, 2, 3]], (1, 3))
    assert_shape([[1, 2], [3, 4]], (2, 2))

    with self.assertRaises(ValueError):
      assert_shape([[1, 2, 3]], (2, 1))
    with self.assertRaises(ValueError):
      assert_shape([[1, 2], [3, 4]], (2, 3))

  def test_reshape_2d(self):
    self.assertEqual(reshape_2d([1, 2, 3, 4], (2, 2)), [[1, 2], [3, 4]])
    self.assertEqual(reshape_2d([1, 2, 3, 4, 5, 6], (2, 3)), [[1, 2, 3], [4, 5, 6]])

    with self.assertRaises(ValueError):
      reshape_2d([1, 2, 3, 4], (2, 3))
    with self.assertRaises(ValueError):
      reshape_2d([1, 2, 3, 4, 5, 6], (2, 2))

""" Tests for Hamilton backend. """
# pylint: disable=missing-class-docstring

import unittest

from .hamilton import STAR


class TestSTAR(unittest.TestCase):
  def setUp(self):
    super().setUp()
    self.star = STAR()

  def test_parse_response_params(self):
    parsed = self.star.parse_response("C0QMid1111", "")[0]
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1111", "id####")[0]
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1112aaabc", "aa&&&")[0]
    self.assertEqual(parsed, {"id": 1112, "aa": "abc"})

    parsed = self.star.parse_response("C0QMid1112aa-21", "aa##")[0]
    self.assertEqual(parsed, {"id": 1112, "aa": -21})

    parsed = self.star.parse_response("C0QMid1113pqABC", "pq***")[0]
    self.assertEqual(parsed, {"id": 1113, "pq": int("ABC", base=16)})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parsed = self.star.parse_response("C0QMaaabc", "")[0]
      self.assertEqual(parsed, "")

    with self.assertRaises(ValueError):
      self.star.parse_response("C0QM", "id####")[0] # pylint: disable=expression-not-assigned

    with self.assertRaises(ValueError):
      self.star.parse_response("C0RV", "")[0] # pylint: disable=expression-not-assigned

  def test_parse_response_errors(self):
    parsed = self.star.parse_response("C0QMid1111", "")[1]
    self.assertIsNone(parsed)

    parsed = self.star.parse_response("C0QMid1111 er00/00", "")[1]
    self.assertEqual(parsed, {"error": "00/00"})

    parsed = self.star.parse_response("C0QMid1111 er99/00 P103/00 P203/00 P402/50 P603/00", "")[1]
    self.assertEqual(parsed, {"error": "99/00", "P1": "03/00", "P2": "03/00", "P4": "02/50",
                              "P6": "03/00"})


if __name__ == "__main__":
  unittest.main()

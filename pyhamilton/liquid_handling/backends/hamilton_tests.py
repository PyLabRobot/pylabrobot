""" Tests for Hamilton backend. """
# pylint: disable=missing-class-docstring

import unittest

from .hamilton import STAR
from .errors import (
  CommandSyntaxError,
  HamiltonError,
  NoTipError,
  HardwareError,
  UnknownHamiltonError
)


class TestSTAR(unittest.TestCase):
  def setUp(self):
    super().setUp()
    self.star = STAR()

  def test_parse_response_params(self):
    parsed = self.star.parse_response("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1111", "id####")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1112aaabc", "aa&&&")
    self.assertEqual(parsed, {"id": 1112, "aa": "abc"})

    parsed = self.star.parse_response("C0QMid1112aa-21", "aa##")
    self.assertEqual(parsed, {"id": 1112, "aa": -21})

    parsed = self.star.parse_response("C0QMid1113pqABC", "pq***")
    self.assertEqual(parsed, {"id": 1113, "pq": int("ABC", base=16)})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parsed = self.star.parse_response("C0QMaaabc", "")
      self.assertEqual(parsed, "")

    with self.assertRaises(ValueError):
      self.star.parse_response("C0QM", "id####") # pylint: disable=expression-not-assigned

    with self.assertRaises(ValueError):
      self.star.parse_response("C0RV", "") # pylint: disable=expression-not-assigned

  def test_parse_response_no_errors(self):
    parsed = self.star.parse_response("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1111 er00/00", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1111 er00/00 P100/00", "")
    self.assertEqual(parsed, {"id": 1111})

  def test_parse_response_master_error(self):
    with self.assertRaises(HamiltonError) as ctx:
      self.star.parse_response("C0QMid1111 er01/30", "")
    e = ctx.exception
    self.assertEqual(len(e), 1)
    self.assertIn("Master", e)
    self.assertIsInstance(e["Master"], CommandSyntaxError)
    self.assertEqual(e["Master"].message, "Unknown command")

  def test_parse_response_slave_errors(self):
    with self.assertRaises(HamiltonError) as ctx:
      self.star.parse_response("C0QMid1111 er99/00 P100/00 P231/00 P402/98 PG08/76", "")
    e = ctx.exception
    self.assertEqual(len(e), 3)
    self.assertNotIn("Master", e)
    self.assertNotIn("Pipetting channel 1", e)
    self.assertEqual(e["Pipetting channel 2"].raw_response, "31/00")
    self.assertEqual(e["Pipetting channel 4"].raw_response, "02/98")
    self.assertEqual(e["Pipetting channel 16"].raw_response, "08/76")

    self.assertIsInstance(e["Pipetting channel 2"], UnknownHamiltonError)
    self.assertIsInstance(e["Pipetting channel 4"], HardwareError)
    self.assertIsInstance(e["Pipetting channel 16"], NoTipError)

    self.assertEqual(e["Pipetting channel 2"].message, "No error")
    self.assertEqual(e["Pipetting channel 4"].message, "Unknown trace information code 98")
    self.assertEqual(e["Pipetting channel 16"].message, "Tip already picked up")

  def test_parse_slave_response_errors(self):
    with self.assertRaises(HamiltonError) as ctx:
      self.star.parse_response("P1OQid1111er30", "")

    e = ctx.exception
    self.assertEqual(len(e), 1)
    self.assertNotIn("Master", e)
    self.assertIn("Pipetting channel 1", e)
    self.assertIsInstance(e["Pipetting channel 1"], UnknownHamiltonError)
    self.assertEqual(e["Pipetting channel 1"].message, "Unknown command")


if __name__ == "__main__":
  unittest.main()

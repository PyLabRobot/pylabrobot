import unittest

from .hamilton import STAR


class TestSTAR(unittest.TestCase):
  def setUp(self):
    super().setUp()
    self.star = STAR()

  def test_parse_response(self):
    parsed = self.star.parse_response("C0QMid1111", "")
    self.assertEqual(parsed, {'id': 1111})

    parsed = self.star.parse_response("C0QMid1111", "id####")
    self.assertEqual(parsed, {'id': 1111})

    parsed = self.star.parse_response("C0QMid1112aaabc", "aa&&&")
    self.assertEqual(parsed, {'id': 1112, 'aa': 'abc'})

    parsed = self.star.parse_response("C0QMid1112aa-21", "aa##")
    self.assertEqual(parsed, {'id': 1112, 'aa': -21})

    parsed = self.star.parse_response("C0QMid1113pqABC", "pq***")
    self.assertEqual(parsed, {'id': 1113, 'pq': int('ABC', base=16)})

    with self.assertRaises(ValueError) as ctx:
      # should fail with auto-added id.
      parsed = self.star.parse_response("C0QMaaabc", "")
      self.assertEqual(parsed, '')

    with self.assertRaises(ValueError) as ctx:
      self.star.parse_response("C0QM", "id####")

    with self.assertRaises(ValueError) as ctx:
      self.star.parse_response("C0RV", "")


if __name__ == "__main__":
  unittest.main()

import unittest

from .hamilton import STAR


class TestSTAR(unittest.TestCase):
  def setUp(self):
    super().setUp()
    self.star = STAR()

  def test_parse_response(self):
    parsed = self.star.parse_response("C0QMid1111", "QMid####")
    self.assertEqual(parsed, {'id': 1111})

    parsed = self.star.parse_response("C0QMid1112aaabc", "QMid####aa&&&")
    self.assertEqual(parsed, {'id': 1112, 'aa': 'abc'})

    parsed = self.star.parse_response("C0QMid1113pqABC", "QMid####pq***")
    self.assertEqual(parsed, {'id': 1113, 'pq': int('ABC', base=16)})

    with self.assertRaises(AssertionError) as ctx:
      self.star.parse_response("C0RV", "QM")


if __name__ == "__main__":
  unittest.main()

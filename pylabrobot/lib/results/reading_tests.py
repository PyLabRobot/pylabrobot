import os
import tempfile
import unittest

from pylabrobot.lib.results.reading import load_rows


class LoadRowsTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.mkdtemp()
    self.path = os.path.join(self.directory, "rows.jsonl")

  def test_missing_file_gives_no_rows(self):
    self.assertEqual(load_rows(os.path.join(self.directory, "not_started")), [])

  def test_cut_last_line_is_skipped(self):
    with open(self.path, "wb") as file:
      file.write(b'{"read": 0}\n{"read": 1}\n{"read": 2, "mo')
    self.assertEqual(load_rows(self.directory), [{"read": 0}, {"read": 1}])

  def test_malformed_complete_line_raises_with_its_line_number(self):
    with open(self.path, "wb") as file:
      file.write(b'{"read": 0}\n{"read": 0,\n{"read": 1}\n')
    with self.assertRaisesRegex(ValueError, "line 2"):
      load_rows(self.directory)


if __name__ == "__main__":
  unittest.main()

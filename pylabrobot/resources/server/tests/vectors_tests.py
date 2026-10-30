"""The store's geometry against the shared vectors in `vectors/geometry.json`."""

import unittest
from typing import Any, Dict

from pylabrobot.resources.server.tests import vectors


class VectorTests(unittest.TestCase):
  vectors: Dict[str, Any]

  @classmethod
  def setUpClass(cls):
    cls.vectors = vectors.load()

  def test_format(self):
    self.assertEqual(self.vectors["format"], vectors.FORMAT)
    self.assertEqual(self.vectors["tolerance"], vectors.TOLERANCE)

  def test_every_kind_of_question_is_asked(self):
    sections = [
      "locations",
      "resources",
      "heightVolume",
      "seats",
      "turns",
      "jaws",
      "placements",
      "grips",
      "occupancy",
      "tracks",
    ]
    for section in sections:
      asked = sum(len(case.get(section, [])) for case in self.vectors["cases"])
      self.assertGreater(asked, 0, section)
    refused = sum(
      bool(entry.get("refused", False))
      for case in self.vectors["cases"]
      for section in sections
      for entry in case.get(section, [])
    )
    self.assertGreater(refused, 0)

  def test_cases(self):
    for case in self.vectors["cases"]:
      with self.subTest(case=case["name"]):
        wrong = vectors.check_case(case)
        self.assertEqual(wrong, [], "\n".join(wrong[:20]))


if __name__ == "__main__":
  unittest.main()

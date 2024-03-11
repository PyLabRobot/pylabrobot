# pylint: disable=missing-class-docstring

import unittest

from .well import Well, WellBottomType


class TestWell(unittest.TestCase):
  def test_serialize(self):
    well = Well(name="well...", size_x=1, size_y=2, size_z=3, bottom_type=WellBottomType.FLAT,
                max_volume=10, model="model")
    self.assertEqual(well.serialize(), {
      "name": "well...",
      "size_x": 1,
      "size_y": 2,
      "size_z": 3,
      "bottom_type": "flat",
      "cross_section_type": "circle",
      "max_volume": 10,
      "model": "model",

      "category": "well",
      "children": [],
      "type": "Well",
      "parent_name": None,
      "location": None,
    })

    self.assertEqual(Well.deserialize(well.serialize()), well)

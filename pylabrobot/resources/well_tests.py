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
      "material_z_thickness": None,
      "bottom_type": "flat",
      "cross_section_type": "circle",
      "max_volume": 10,
      "model": "model",

      "category": "well",
      "children": [],
      "type": "Well",
      "parent_name": None,
      "location": None,
      "rotation": {
        "type": "Rotation",
        "x": 0, "y": 0, "z": 0
      },
      "compute_volume_from_height": None,
      "compute_height_from_volume": None,
    })

    self.assertEqual(Well.deserialize(well.serialize()), well)

  def test_set_liquids(self):
    well = Well(name="well...", size_x=1, size_y=2, size_z=3, bottom_type=WellBottomType.FLAT,
                max_volume=10, model="model")
    well.set_liquids([(None, 10)])
    self.assertEqual(well.tracker.liquids, [(None, 10)])
    self.assertEqual(well.tracker.get_used_volume(), 10)

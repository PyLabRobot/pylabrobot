import unittest

from .plate import Plate
from .utils import create_ordered_items_2d
from .well import Well, WellBottomType


class TestWell(unittest.TestCase):
  def test_serialize(self):
    well = Well(
      name="well...",
      size_x=1,
      size_y=2,
      size_z=3,
      bottom_type=WellBottomType.FLAT,
      max_volume=10,
      model="model",
    )
    self.assertEqual(
      well.serialize(),
      {
        "name": "well...",
        "size_x": 1,
        "size_y": 2,
        "size_z": 3,
        "material_z_thickness": None,
        "bottom_type": "flat",
        "cross_section_type": "circle",
        "max_volume": 10,
        "model": "model",
        "barcode": None,
        "preferred_pickup_location": None,
        "category": "well",
        "children": [],
        "type": "Well",
        "parent_name": None,
        "location": None,
        "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
        "compute_volume_from_height": None,
        "compute_height_from_volume": None,
      },
    )

    self.assertEqual(Well.deserialize(well.serialize()), well)

  def test_get_index_in_plate(self):
    plate = Plate(
      "plate",
      size_x=1,
      size_y=1,
      size_z=1,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=24,
        num_items_y=16,
        dx=1,
        dy=1,
        dz=1,
        item_dx=1,
        item_dy=1,
        size_x=1,
        size_y=1,
        size_z=1,
      ),
    )
    well = plate.get_well("A1")
    self.assertEqual(well.get_identifier(), "A1")

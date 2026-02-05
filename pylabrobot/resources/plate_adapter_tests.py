import unittest

from pylabrobot.resources import PlateAdapter

_adapter = PlateAdapter(
  name="adapter",
  size_x=128.0,
  size_y=86.0,
  size_z=15.0,
  dx=0.0,
  dy=1.0,
  dz=2.0,
  adapter_hole_size_x=100.0,
  adapter_hole_size_y=80.0,
  adapter_hole_dx=9.0,
  adapter_hole_dy=9.0,
  plate_z_offset=3.0,
)


class TestPlateAdapter(unittest.TestCase):
  def test_plate_adapter_serialization(self):
    serialized = _adapter.serialize()
    self.assertEqual(
      serialized,
      {
        "name": "adapter",
        "type": "PlateAdapter",
        "size_x": 128.0,
        "size_y": 86.0,
        "size_z": 15.0,
        "location": None,
        "rotation": {"x": 0, "y": 0, "z": 0, "type": "Rotation"},
        "category": "plate_adapter",
        "model": None,
        "barcode": None,
        "preferred_pickup_location": None,
        "children": [],
        "parent_name": None,
        "dx": 0.0,
        "dy": 1.0,
        "dz": 2.0,
        "adapter_hole_size_x": 100.0,
        "adapter_hole_size_y": 80.0,
        "adapter_hole_dx": 9.0,
        "adapter_hole_dy": 9.0,
        "plate_z_offset": 3.0,
      },
    )

  def test_plate_adapter_deserialization(self):
    assert _adapter == PlateAdapter.deserialize(_adapter.serialize())

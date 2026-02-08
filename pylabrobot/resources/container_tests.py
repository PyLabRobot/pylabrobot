import unittest

from pylabrobot.serializer import serialize

from .container import Container


class TestContainer(unittest.TestCase):
  def test_serialize(self):
    def compute_volume_from_height(height):
      return height

    def compute_height_from_volume(volume):
      return volume

    c = Container(
      name="container",
      size_x=10,
      size_y=10,
      size_z=10,
      material_z_thickness=1,
      max_volume=1000,
      compute_height_from_volume=compute_height_from_volume,
      compute_volume_from_height=compute_volume_from_height,
    )

    self.assertEqual(
      c.serialize(),
      {
        "name": "container",
        "size_x": 10,
        "size_y": 10,
        "size_z": 10,
        "material_z_thickness": 1,
        "category": None,
        "model": None,
        "barcode": None,
        "preferred_pickup_location": None,
        "max_volume": 1000,
        "compute_volume_from_height": serialize(compute_volume_from_height),
        "compute_height_from_volume": serialize(compute_height_from_volume),
        "parent_name": None,
        "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
        "type": "Container",
        "children": [],
        "location": None,
      },
    )

    d = Container.deserialize(c.serialize(), allow_marshal=True)
    self.assertEqual(c, d)
    self.assertEqual(d.compute_height_from_volume(10), 10)
    self.assertEqual(d.compute_volume_from_height(10), 10)

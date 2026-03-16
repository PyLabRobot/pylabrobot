import json
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
        "height_volume_data": None,
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

  def test_height_volume_data_auto_generates_functions(self):
    c = Container(
      name="c",
      size_x=10,
      size_y=10,
      size_z=10,
      height_volume_data={0: 0, 5: 50, 10: 200},
    )
    self.assertEqual(c.compute_volume_from_height(0), 0)
    self.assertEqual(c.compute_volume_from_height(5), 50)
    self.assertEqual(c.compute_volume_from_height(2.5), 25)
    self.assertEqual(c.compute_height_from_volume(50), 5)
    with self.assertRaises(ValueError):
      c.compute_volume_from_height(11)

  def test_height_volume_data_does_not_override_explicit_functions(self):
    c = Container(
      name="c",
      size_x=10,
      size_y=10,
      size_z=10,
      height_volume_data={0: 0, 10: 100},
      compute_volume_from_height=lambda h: h * 999,
    )
    self.assertEqual(c.compute_volume_from_height(5), 4995)
    self.assertEqual(c.compute_height_from_volume(50), 5)

  def test_height_volume_data_serialize_deserialize_roundtrip(self):
    c = Container(
      name="c",
      size_x=10,
      size_y=10,
      size_z=10,
      height_volume_data={0: 0, 5: 50, 10: 200},
    )
    serialized = c.serialize()
    self.assertIsNone(serialized["compute_volume_from_height"])
    self.assertIsNone(serialized["compute_height_from_volume"])
    self.assertEqual(serialized["height_volume_data"], {0: 0, 5: 50, 10: 200})

    d = Container.deserialize(serialized)
    self.assertEqual(d.compute_volume_from_height(2.5), 25)
    self.assertEqual(d.compute_height_from_volume(50), 5)

    # True JSON roundtrip (keys become strings)
    from_json = json.loads(json.dumps(serialized))
    d2 = Container.deserialize(from_json)
    self.assertEqual(d2.compute_volume_from_height(2.5), 25)
    self.assertEqual(d2.compute_height_from_volume(50), 5)

  def test_height_volume_data_none_preserves_existing_behaviour(self):
    def compute_volume_from_height(height):
      return height * 2

    def compute_height_from_volume(volume):
      return volume / 2

    c = Container(
      name="c",
      size_x=10,
      size_y=10,
      size_z=10,
      compute_volume_from_height=compute_volume_from_height,
      compute_height_from_volume=compute_height_from_volume,
    )
    serialized = c.serialize()
    self.assertIsNotNone(serialized["compute_volume_from_height"])
    self.assertIsNone(serialized["height_volume_data"])

    d = Container.deserialize(serialized, allow_marshal=True)
    self.assertEqual(d.compute_volume_from_height(10), 20)
    self.assertEqual(d.compute_height_from_volume(20), 10)

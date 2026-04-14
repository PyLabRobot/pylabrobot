import json
import unittest

from pylabrobot.legacy.liquid_handling.errors import ChannelsDoNotFitError
from pylabrobot.serializer import serialize

from .container import Container
from .coordinate import Coordinate


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
        "type": "Container",
        "material_z_thickness": 1,
        "max_volume": 1000,
        "compute_volume_from_height": serialize(compute_volume_from_height),
        "compute_height_from_volume": serialize(compute_height_from_volume),
        "height_volume_data": None,
        "no_go_zones": [],
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

  def test_height_volume_data_with_explicit_functions_serialize_deserialize_roundtrip(self):
    """Explicit functions win at construction time, but height_volume_data is the serialization
    source of truth. After roundtrip, auto-generated interpolators replace explicit functions."""

    c = Container(
      name="c",
      size_x=10,
      size_y=10,
      size_z=10,
      height_volume_data={0: 0, 10: 100},
      compute_volume_from_height=lambda h: h * 999,
      compute_height_from_volume=lambda v: v / 999,
    )
    # Explicit functions win at construction time.
    self.assertEqual(c.compute_volume_from_height(5), 4995)

    serialized = c.serialize()
    # Closures are not serialized when height_volume_data is present.
    self.assertIsNone(serialized["compute_volume_from_height"])
    self.assertIsNone(serialized["compute_height_from_volume"])
    self.assertEqual(serialized["height_volume_data"], {0: 0, 10: 100})

    d = Container.deserialize(serialized)
    # After roundtrip, auto-generated interpolators from height_volume_data take over.
    self.assertEqual(d.compute_volume_from_height(5), 50)
    self.assertEqual(d.compute_height_from_volume(50), 5)

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

  def test_height_volume_data_validates_monotonicity(self):
    with self.assertRaises(ValueError):
      Container(
        name="c", size_x=10, size_y=10, size_z=10, height_volume_data={0: 0, 5: 100, 10: 50}
      )  # non-monotonic volumes

  def test_height_volume_data_validates_minimum_points(self):
    with self.assertRaises(ValueError):
      Container(
        name="c", size_x=10, size_y=10, size_z=10, height_volume_data={0: 0}
      )  # only 1 point

  def test_no_go_zones_default_empty(self):
    c = Container(name="c", size_x=10, size_y=10, size_z=10)
    self.assertEqual(c.no_go_zones, [])

  def test_no_go_zones_stored(self):
    zones = [(Coordinate(0, 44, 0), Coordinate(10, 46, 10))]
    c = Container(name="c", size_x=10, size_y=90, size_z=10, no_go_zones=zones)
    self.assertEqual(len(c.no_go_zones), 1)
    self.assertEqual(c.no_go_zones[0][0], Coordinate(0, 44, 0))
    self.assertEqual(c.no_go_zones[0][1], Coordinate(10, 46, 10))

  def test_no_go_zones_serialize(self):
    zones = [(Coordinate(0, 44, 0), Coordinate(10, 46, 10))]
    c = Container(name="c", size_x=10, size_y=90, size_z=10, no_go_zones=zones)
    serialized = c.serialize()
    self.assertEqual(
      serialized["no_go_zones"],
      [
        (
          {"type": "Coordinate", "x": 0, "y": 44, "z": 0},
          {"type": "Coordinate", "x": 10, "y": 46, "z": 10},
        )
      ],
    )

  def test_no_go_zones_multiple(self):
    zones = [
      (Coordinate(0, 34.6, 0), Coordinate(10, 36.6, 10)),
      (Coordinate(0, 70.2, 0), Coordinate(10, 72.2, 10)),
      (Coordinate(0, 105.9, 0), Coordinate(10, 107.9, 10)),
    ]
    c = Container(name="c", size_x=10, size_y=142, size_z=10, no_go_zones=zones)
    self.assertEqual(len(c.no_go_zones), 3)


class TestNoGoZoneCollision(unittest.TestCase):
  def _make_container(self, size_y, no_go_zones=None):
    return Container(name="c", size_x=10, size_y=size_y, size_z=10, no_go_zones=no_go_zones)

  def test_no_zones_uses_standard_spread(self):
    from pylabrobot.legacy.liquid_handling.channel_positioning import compute_channel_offsets

    c = self._make_container(90)
    result = compute_channel_offsets(c, num_channels=1)
    self.assertEqual(len(result), 1)
    # No no-go zones: single channel goes to center (offset 0)
    self.assertAlmostEqual(result[0].y, 0.0)

  def test_1_channel_in_2_compartments(self):
    from pylabrobot.legacy.liquid_handling.channel_positioning import compute_channel_offsets

    # 90mm container, divider at Y=44-46 -> 2 compartments [0,44] and [46,90]
    # edge_clearance = 2.0
    # Usable: [2.0, 42.0] and [48.0, 88.0]
    # 1 channel -> center-out back-first -> goes to back compartment (index 1)
    # Center of back usable = (48.0 + 88.0) / 2 = 68.0
    # Container center = 45.0, offset = 68.0 - 45.0 = 23.0
    c = self._make_container(
      90,
      no_go_zones=[(Coordinate(0, 44, 0), Coordinate(10, 46, 10))],
    )
    result = compute_channel_offsets(c, num_channels=1)
    self.assertEqual(len(result), 1)
    self.assertAlmostEqual(result[0].y, 23.0)

  def test_2_channels_across_2_compartments(self):
    from pylabrobot.legacy.liquid_handling.channel_positioning import compute_channel_offsets

    c = self._make_container(
      90,
      no_go_zones=[(Coordinate(0, 44, 0), Coordinate(10, 46, 10))],
    )
    result = compute_channel_offsets(c, num_channels=2)
    self.assertEqual(len(result), 2)
    # Sorted descending by Y (back-to-front)
    self.assertGreater(result[0].y, result[1].y)

  def test_4_channels_across_2_compartments(self):
    from pylabrobot.legacy.liquid_handling.channel_positioning import compute_channel_offsets

    c = self._make_container(
      90,
      no_go_zones=[(Coordinate(0, 44, 0), Coordinate(10, 46, 10))],
    )
    result = compute_channel_offsets(c, num_channels=4)
    self.assertEqual(len(result), 4)

  def test_raises_when_impossible(self):
    from pylabrobot.legacy.liquid_handling.channel_positioning import compute_channel_offsets

    # Entire container is no-go
    c = self._make_container(
      12,
      no_go_zones=[(Coordinate(0, 0, 0), Coordinate(10, 12, 10))],
    )
    with self.assertRaises(ChannelsDoNotFitError):
      compute_channel_offsets(c, num_channels=1)

  def test_3_compartments_6_channels(self):
    from pylabrobot.legacy.liquid_handling.channel_positioning import compute_channel_offsets

    # 150mm container, 2 dividers -> 3 compartments, 6 channels -> 2 per compartment
    c = self._make_container(
      150,
      no_go_zones=[
        (Coordinate(0, 49, 0), Coordinate(10, 51, 10)),
        (Coordinate(0, 99, 0), Coordinate(10, 101, 10)),
      ],
    )
    result = compute_channel_offsets(c, num_channels=6)
    self.assertEqual(len(result), 6)

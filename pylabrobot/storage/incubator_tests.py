import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.storage import Incubator, IncubatorChatterboxBackend


class IncubatorTests(unittest.TestCase):
  def test_serialization(self):
    i = Incubator(
      name="test_tc",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=IncubatorChatterboxBackend(),
      loading_tray_location=Coordinate(0, 0, 0),
      racks=[],
    )

    serialized = i.serialize()
    deserialized = Incubator.deserialize(serialized)
    self.assertEqual(i, deserialized)

import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.shaking import Shaker, ShakerChatterboxBackend


class ShakerTests(unittest.TestCase):
  def test_serialization(self):
    s = Shaker(
      name="test_shaker",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=ShakerChatterboxBackend(),
      child_location=Coordinate(0, 0, 0),
    )

    serialized = s.serialize()
    deserialized = Shaker.deserialize(serialized)
    self.assertEqual(s, deserialized)

import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.temperature_controlling import (
  TemperatureController,
  TemperatureControllerChatterboxBackend,
)


class TemperatureControllerTests(unittest.TestCase):
  def test_serialization(self):
    tc = TemperatureController(
      name="test_tc",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=TemperatureControllerChatterboxBackend(),
      child_location=Coordinate(0, 0, 0),
    )

    serialized = tc.serialize()
    deserialized = TemperatureController.deserialize(serialized)
    self.assertEqual(tc, deserialized)

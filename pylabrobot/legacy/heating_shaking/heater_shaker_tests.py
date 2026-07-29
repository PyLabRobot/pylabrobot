import unittest

from pylabrobot.legacy.heating_shaking import HeaterShaker, HeaterShakerChatterboxBackend
from pylabrobot.resources.coordinate import Coordinate


class HeaterShakerTests(unittest.TestCase):
  def test_serialization(self):
    hs = HeaterShaker(
      name="test_hs",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=HeaterShakerChatterboxBackend(),
      child_location=Coordinate(0, 0, 0),
    )

    serialized = hs.serialize()
    deserialized = HeaterShaker.deserialize(serialized)
    self.assertEqual(hs, deserialized)


class HeaterShakerSetupTests(unittest.IsolatedAsyncioTestCase):
  async def test_setup_and_stop(self):
    hs = HeaterShaker(
      name="test_hs",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=HeaterShakerChatterboxBackend(),
      child_location=Coordinate(0, 0, 0),
    )

    await hs.setup()
    try:
      self.assertTrue(hs.setup_finished)
      await hs.set_temperature(37)
      await hs.shake(speed=100, duration=0)
    finally:
      await hs.stop()
    self.assertFalse(hs.setup_finished)

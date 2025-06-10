import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.temperature_controlling import (
  TemperatureController,
  TemperatureControllerChatterboxBackend,
)
from pylabrobot.temperature_controlling.backend import TemperatureControllerBackend


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


class PassiveCoolingTests(unittest.IsolatedAsyncioTestCase):
  async def test_cannot_cool_without_support(self):
    backend = TemperatureControllerChatterboxBackend(dummy_temperature=20.0)
    tc = TemperatureController(
      name="tc",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=backend,
      child_location=Coordinate.zero(),
    )

    with self.assertRaises(ValueError):
      await tc.set_temperature(10)

  async def test_passive_cooling_without_support(self):
    backend = TemperatureControllerChatterboxBackend(dummy_temperature=20.0)
    tc = TemperatureController(
      name="tc",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=backend,
      child_location=Coordinate.zero(),
    )

    await tc.set_temperature(10, passive=True)
    # Temperature should remain unchanged on the backend.
    self.assertEqual(await backend.get_current_temperature(), 20.0)


class _FakeBackend(TemperatureControllerBackend):
  def __init__(self, temperature: float = 25.0):
    super().__init__()
    self.temperature = temperature
    self.set_called = False

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def set_temperature(self, temperature: float):
    self.set_called = True
    self.temperature = temperature

  async def get_current_temperature(self) -> float:
    return self.temperature

  async def deactivate(self):
    pass


class PassiveCoolingWithSupportTests(unittest.IsolatedAsyncioTestCase):
  async def test_passive_cooling_with_support(self):
    backend = _FakeBackend(temperature=30.0)
    tc = TemperatureController(
      name="tc",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=backend,
      child_location=Coordinate.zero(),
    )

    await tc.set_temperature(20, passive=True)
    self.assertFalse(backend.set_called)

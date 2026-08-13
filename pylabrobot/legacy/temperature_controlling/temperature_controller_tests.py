import unittest

from pylabrobot.events import EventBus, use_event_bus
from pylabrobot.legacy.temperature_controlling import (
  TemperatureController,
  TemperatureControllerChatterboxBackend,
)
from pylabrobot.legacy.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


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


class TemperatureControllerEventTests(unittest.IsolatedAsyncioTestCase):
  async def test_set_and_wait_for_temperature_emit_device_scoped_events(self):
    temperature_controller = TemperatureController(
      name="test_temperature_module",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=TemperatureControllerChatterboxBackend(dummy_temperature=20.0),
      child_location=Coordinate.zero(),
    )
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await temperature_controller.set_temperature(37.0)
      await temperature_controller.wait_for_temperature(timeout=1.0, tolerance=0.5)

    self.assertEqual(
      [event.name for event in events],
      [
        "temperature_controller.set_temperature.started",
        "temperature_controller.set_temperature.completed",
        "temperature_controller.wait_for_temperature.started",
        "temperature_controller.wait_for_temperature.completed",
      ],
    )
    self.assertEqual(events[0].context["device"]["name"], "test_temperature_module")
    self.assertEqual(events[0].context["target_temperature_c"], 37.0)
    self.assertEqual(events[2].context["timeout_seconds"], 1.0)
    self.assertEqual(events[2].context["tolerance_c"], 0.5)

  async def test_temperature_events_include_currently_loaded_resource(self):
    temperature_controller = TemperatureController(
      name="test_temperature_module",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=TemperatureControllerChatterboxBackend(dummy_temperature=20.0),
      child_location=Coordinate.zero(),
    )
    plate = Resource("plate", size_x=1, size_y=1, size_z=1)
    temperature_controller.assign_child_resource(plate)
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await temperature_controller.set_temperature(37.0)

    self.assertEqual(events[0].context["resources"][0]["name"], "plate")


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

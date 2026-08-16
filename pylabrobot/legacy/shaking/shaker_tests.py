import unittest

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.shaking import Shaker, ShakerChatterboxBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


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


class ShakerEventTests(unittest.IsolatedAsyncioTestCase):
  async def test_shake_and_stop_emit_device_scoped_events(self):
    shaker = Shaker(
      name="test_shaker",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=ShakerChatterboxBackend(),
      child_location=Coordinate.zero(),
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await shaker.shake(speed=900, duration=0)
      await shaker.stop_shaking()

    self.assertEqual(
      [event.name for event in events],
      [
        "shaker.shake.started",
        "shaker.shake.completed",
        "shaker.stop_shaking.started",
        "shaker.stop_shaking.completed",
      ],
    )
    self.assertEqual(events[0].context["device"]["name"], "test_shaker")
    self.assertEqual(events[0].context["speed_rpm"], 900.0)
    self.assertEqual(events[0].context["duration"], 0.0)
    self.assertNotIn("duration_seconds", events[0].context)

  async def test_shake_includes_currently_loaded_resource(self):
    shaker = Shaker(
      name="test_shaker",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=ShakerChatterboxBackend(),
      child_location=Coordinate.zero(),
    )
    plate = Resource("plate", size_x=1, size_y=1, size_z=1)
    shaker.assign_child_resource(plate)
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await shaker.shake(speed=900, duration=0)

    self.assertEqual(events[0].context["resources"][0]["name"], "plate")

import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.agilent.vspin.access2 import Access2
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.events import EventBus, use_event_bus
from pylabrobot.resources import Coordinate, Resource


class TestVSpinEvents(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.vspin_ftdi = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.vspin_ftdi.start()
    self.addCleanup(self.vspin_ftdi.stop)

  async def test_spin_emits_loaded_bucket_resources_and_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    vspin.request_door_open = AsyncMock(return_value=False)
    vspin.request_door_locked = AsyncMock(return_value=True)
    vspin.request_bucket_locked = AsyncMock(return_value=False)
    vspin.request_tachometer = AsyncMock(return_value=100000)
    vspin.request_position = AsyncMock(side_effect=[0, 10000000])
    vspin.request_home_position = AsyncMock(side_effect=[0, 1])
    vspin.send_command = AsyncMock(return_value=b"")
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(g=500, duration=1, acceleration=0.5, deceleration=0.6)

    self.assertEqual([event.name for event in events], [
      "centrifuge.spin.started",
      "centrifuge.spin.completed",
    ])
    started, completed = events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["device"]["name"], "centrifuge")
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["bucket_resources"][0]["holder"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["relative_centrifugal_force"], 500)
    self.assertEqual(started.data["duration"], 1)
    self.assertEqual(started.data["acceleration_fraction"], 0.5)
    self.assertEqual(started.data["deceleration_fraction"], 0.6)
    self.assertNotIn("relative_centrifugal_force_g", started.data)
    self.assertNotIn("duration_seconds", started.data)

  async def test_spin_failure_emits_requested_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(ValueError, "G-force"):
        await vspin.spin(g=0)

    self.assertEqual([event.name for event in events], [
      "centrifuge.spin.started",
      "centrifuge.spin.failed",
    ])
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])
    self.assertEqual(events[1].data["error_type"], "ValueError")


class TestAccess2Events(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.vspin_ftdi = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.access2_ftdi = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    self.vspin_ftdi.start()
    self.access2_ftdi.start()
    self.addCleanup(self.access2_ftdi.stop)
    self.addCleanup(self.vspin_ftdi.stop)

  async def asyncSetUp(self):
    self.vspin = VSpin(name="centrifuge", device_id="test")
    self.vspin._door_open = True
    self.vspin._at_bucket = self.vspin.bucket1
    self.loader = Access2(name="loader", device_id="test", vspin=self.vspin)
    self.loader.driver.load = AsyncMock()
    self.loader.driver.unload = AsyncMock()

  async def test_load_emits_loader_to_bucket_transfer(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.loader.load()

    lifecycle_events = [event for event in events if event.name.startswith("centrifuge_loader.load.")]
    self.assertEqual([event.name for event in lifecycle_events], [
      "centrifuge_loader.load.started",
      "centrifuge_loader.load.completed",
    ])
    started, completed = lifecycle_events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["source"]["name"], "loader")
    self.assertEqual(started.data["destination"]["name"], "centrifuge_bucket1")
    self.assertIs(self.vspin.bucket1.resource, plate)

  async def test_unload_failure_emits_bucket_to_loader_transfer(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    self.loader.driver.unload = AsyncMock(side_effect=RuntimeError("loader fault"))
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(RuntimeError, "loader fault"):
        await self.loader.unload()

    lifecycle_events = [event for event in events if event.name.startswith("centrifuge_loader.unload.")]
    self.assertEqual([event.name for event in lifecycle_events], [
      "centrifuge_loader.unload.started",
      "centrifuge_loader.unload.failed",
    ])
    started, failed = lifecycle_events
    self.assertEqual(started.context["operation_id"], failed.context["operation_id"])
    self.assertEqual(started.data["source"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["destination"]["name"], "loader")
    self.assertEqual(failed.data["error_type"], "RuntimeError")

import unittest
import unittest.mock
from unittest.mock import AsyncMock, patch

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.centrifuge import (
  BucketHasPlateError,
  BucketNoPlateError,
  Centrifuge,
  CentrifugeDoorError,
  Loader,
  LoaderNoPlateError,
  NotAtBucketError,
)
from pylabrobot.legacy.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.legacy.centrifuge.chatterbox import (
  CentrifugeChatterboxBackend,
  LoaderChatterboxBackend,
)
from pylabrobot.legacy.centrifuge.vspin_backend import Access2Backend, VSpinBackend
from pylabrobot.resources import Coordinate, Resource, cor_96_wellplate_360uL_Fb


class CentrifugeTests(unittest.IsolatedAsyncioTestCase):
  def test_serialization(self):
    centrifuge = Centrifuge(
      backend=CentrifugeChatterboxBackend(),
      name="centrifuge",
      size_x=1,
      size_y=1,
      size_z=1,
    )
    serialized = centrifuge.serialize()
    deserialized = Centrifuge.deserialize(serialized)
    self.assertEqual(deserialized, centrifuge)


class CentrifugeLoaderResourceModelTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mock_centrifuge_backend = unittest.mock.MagicMock(spec=CentrifugeBackend)
    self.mock_loader_backend = unittest.mock.MagicMock(spec=LoaderBackend)
    self.centrifuge = Centrifuge(
      backend=self.mock_centrifuge_backend, name="centrifuge", size_x=1, size_y=1, size_z=1
    )
    self.loader = Loader(
      backend=self.mock_loader_backend,
      centrifuge=self.centrifuge,
      name="loader",
      size_x=1,
      size_y=1,
      size_z=1,
      child_location=Coordinate.zero(),
    )
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    return await super().asyncSetUp()

  async def test_go_to_bucket(self):
    self.assertIsNone(self.centrifuge.at_bucket)
    await self.centrifuge.go_to_bucket1()
    self.assertEqual(self.centrifuge.at_bucket, self.centrifuge.bucket1)
    await self.centrifuge.go_to_bucket2()
    self.assertEqual(self.centrifuge.at_bucket, self.centrifuge.bucket2)

  async def test_load(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    assert self.centrifuge._door_open
    assert self.centrifuge.door_open
    self.loader.assign_child_resource(self.plate)
    await self.loader.load()
    self.mock_loader_backend.load.assert_awaited_once()
    assert self.centrifuge.at_bucket is not None
    self.assertEqual(self.centrifuge.at_bucket.children[0], self.plate)
    self.assertEqual(self.loader.children, [])

  async def test_load_emits_loader_to_bucket_transfer(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    self.loader.assign_child_resource(self.plate)
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.loader.load()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.load.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      ["centrifuge_loader.load.started", "centrifuge_loader.load.completed"],
    )
    started, completed = lifecycle_events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["resources"][0]["name"], "plate")
    self.assertEqual(started.data["source"]["name"], "loader")
    self.assertEqual(started.data["destination"]["name"], "centrifuge_bucket1")

  async def test_load_locked_door(self):
    self.loader.assign_child_resource(self.plate)
    with self.assertRaises(CentrifugeDoorError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_load_no_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    with self.assertRaises(LoaderNoPlateError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_load_bucket_has_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    assert self.centrifuge.at_bucket is not None
    self.centrifuge.at_bucket.assign_child_resource(self.plate)
    another_plate = cor_96_wellplate_360uL_Fb(name="another_plate")
    self.loader.assign_child_resource(another_plate)
    with self.assertRaises(BucketHasPlateError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_load_not_at_bucket(self):
    self.loader.assign_child_resource(self.plate)
    await self.centrifuge.open_door()
    with self.assertRaises(NotAtBucketError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_unload(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    assert self.centrifuge.at_bucket is not None
    self.centrifuge.at_bucket.assign_child_resource(self.plate)
    await self.loader.unload()
    self.mock_loader_backend.unload.assert_awaited_once()
    self.assertEqual(self.centrifuge.at_bucket.children, [])
    self.assertEqual(self.loader.children, [self.plate])

  async def test_unload_failure_emits_bucket_to_loader_transfer(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    assert self.centrifuge.at_bucket is not None
    self.centrifuge.at_bucket.assign_child_resource(self.plate)
    self.mock_loader_backend.unload = AsyncMock(side_effect=RuntimeError("loader fault"))
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(RuntimeError, "loader fault"):
        await self.loader.unload()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.unload.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      ["centrifuge_loader.unload.started", "centrifuge_loader.unload.failed"],
    )
    started, failed = lifecycle_events
    self.assertEqual(started.context["operation_id"], failed.context["operation_id"])
    self.assertEqual(started.data["source"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["destination"]["name"], "loader")
    self.assertEqual(failed.data["error_type"], "RuntimeError")

  async def test_unload_locked_door(self):
    self.loader.assign_child_resource(self.plate)
    with self.assertRaises(CentrifugeDoorError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  async def test_unload_bucket_has_no_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    with self.assertRaises(BucketNoPlateError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  async def test_unload_loader_has_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    self.loader.assign_child_resource(self.plate)
    with self.assertRaises(BucketNoPlateError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  async def test_unload_not_at_bucket(self):
    self.loader.assign_child_resource(self.plate)
    await self.centrifuge.open_door()
    with self.assertRaises(NotAtBucketError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  def test_serialize(self):
    self.loader.backend = LoaderChatterboxBackend()
    self.centrifuge.backend = CentrifugeChatterboxBackend()
    serialized = self.loader.serialize()
    self.assertEqual(Loader.deserialize(serialized), self.loader)


class Access2BackendTests(unittest.IsolatedAsyncioTestCase):
  async def test_load_accepts_default_grip_steps(self):
    """The default one-step grip is valid and must reach the loader sequence."""
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = Access2Backend(device_id="test")
    backend.send_command = AsyncMock(return_value=b"")  # type: ignore[method-assign]

    await backend.load()

    self.assertGreater(backend.send_command.await_count, 0)

  async def test_load_rejects_invalid_grip_steps_before_hardware_command(self):
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = Access2Backend(device_id="test")
    backend.send_command = AsyncMock()  # type: ignore[method-assign]

    with self.assertRaisesRegex(ValueError, "grip_steps must be between 1 and 4"):
      await backend.load(grip_steps=0)  # type: ignore[arg-type]

    backend.send_command.assert_not_awaited()


class CentrifugeEventTests(unittest.IsolatedAsyncioTestCase):
  async def test_spin_emits_loaded_resources_and_backend_parameters(self):
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = VSpinBackend(device_id=None)
    backend.spin = AsyncMock()  # type: ignore[method-assign]
    centrifuge = Centrifuge(
      backend=backend,
      name="centrifuge",
      size_x=1,
      size_y=1,
      size_z=1,
    )
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    centrifuge.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await centrifuge.spin(500, 1, acceleration=0.5, deceleration=0.6)

    lifecycle_events = [event for event in events if event.name.startswith("centrifuge.spin.")]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      ["centrifuge.spin.started", "centrifuge.spin.completed"],
    )
    started, completed = lifecycle_events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["device"]["name"], "centrifuge")
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["bucket_resources"][0]["holder"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["relative_centrifugal_force"], 500)
    self.assertEqual(started.data["duration"], 1)
    self.assertEqual(started.data["acceleration_fraction"], 0.5)
    self.assertEqual(started.data["deceleration_fraction"], 0.6)

  async def test_spin_reports_vspin_backend_defaults(self):
    with patch("pylabrobot.legacy.centrifuge.vspin_backend.FTDI"):
      backend = VSpinBackend(device_id=None)
    backend.spin = AsyncMock()  # type: ignore[method-assign]
    centrifuge = Centrifuge(
      backend=backend,
      name="centrifuge",
      size_x=1,
      size_y=1,
      size_z=1,
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await centrifuge.spin(g=500, duration=1)

    started = next(event for event in events if event.name == "centrifuge.spin.started")
    self.assertEqual(started.data["acceleration_fraction"], 0.8)
    self.assertEqual(started.data["deceleration_fraction"], 0.8)

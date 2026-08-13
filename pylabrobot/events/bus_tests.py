import json
import unittest

from pylabrobot.events import (
  EventBus,
  coordinate_reference,
  emit_event,
  event_context,
  event_operation,
  evented_operation,
  use_event_bus,
)
from pylabrobot.resources import Coordinate, Resource


class TestEventBus(unittest.TestCase):
  def test_coordinate_reference_preserves_coordinate_serialization(self):
    self.assertEqual(
      coordinate_reference(Coordinate(1.25, 2.5, 3.75)),
      {"x": 1.25, "y": 2.5, "z": 3.75, "type": "Coordinate"},
    )

  def test_context_is_attached_to_events(self):
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus), event_context(run_id="run-1", batch_id="batch-1"):
      emit_event("example.completed", value=42)

    self.assertEqual(events[0].name, "example.completed")
    self.assertEqual(events[0].context, {"run_id": "run-1", "batch_id": "batch-1"})
    self.assertEqual(events[0].data, {"value": 42})
    self.assertEqual(json.loads(json.dumps(events[0].as_dict()))["name"], "example.completed")

  def test_resource_assignment_and_unassignment_emit_contextual_events(self):
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    parent = Resource("parent", size_x=10, size_y=10, size_z=1)
    child = Resource("child", size_x=5, size_y=5, size_z=1)

    with use_event_bus(event_bus), event_context(run_id="run-1"):
      parent.assign_child_resource(child, location=Coordinate(1, 2, 3))
      child.unassign()

    assigned, unassigned = events
    self.assertEqual(assigned.name, "resource.assigned")
    self.assertEqual(assigned.context["run_id"], "run-1")
    self.assertEqual(assigned.data["resource"]["name"], "child")
    self.assertEqual(assigned.data["parent"]["name"], "parent")
    self.assertEqual(assigned.data["location"], {"x": 1, "y": 2, "z": 3, "type": "Coordinate"})
    self.assertEqual(unassigned.name, "resource.unassigned")
    self.assertEqual(unassigned.data["previous_parent"]["name"], "parent")
    self.assertEqual(assigned.data["resource"]["rotation"], {"x": 0, "y": 0, "z": 0})


class TestEventedOperation(unittest.IsolatedAsyncioTestCase):
  def test_block_operation_emits_a_correlated_lifecycle(self):
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus), event_operation("device.action", device="device-1"):
      emit_event("firmware.command.started", command="AB")

    self.assertEqual(
      [event.name for event in events],
      ["device.action.started", "firmware.command.started", "device.action.completed"],
    )
    operation_id = events[0].context["operation_id"]
    self.assertEqual(events[1].context["operation_id"], operation_id)
    self.assertEqual(events[2].context["operation_id"], operation_id)

  def test_block_operation_can_capture_final_completion_data(self):
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    location = {"name": "source"}

    with use_event_bus(event_bus), event_operation(
      "resource.transfer",
      resources=[{"name": "plate", "location": location["name"]}],
      completed_data_factory=lambda: {
        "resources": [{"name": "plate", "location": location["name"]}],
      },
    ):
      location["name"] = "destination"

    self.assertEqual(events[0].data["resources"][0]["location"], "source")
    self.assertEqual(events[1].data["resources"][0]["location"], "destination")

  async def test_operation_context_is_inherited_by_nested_events(self):
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    @evented_operation("device.action", lambda value: {"device": "device-1", "value": value})
    async def action(value):
      emit_event("firmware.command.started", command="AB")
      return value * 2

    with use_event_bus(event_bus):
      self.assertEqual(await action(3), 6)

    self.assertEqual([event.name for event in events], [
      "device.action.started",
      "firmware.command.started",
      "device.action.completed",
    ])
    operation_id = events[0].context["operation_id"]
    self.assertEqual(events[1].context["operation_id"], operation_id)
    self.assertEqual(events[2].context["operation_id"], operation_id)

  async def test_failed_operation_emits_a_correlated_failure(self):
    events = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    @evented_operation("device.action", lambda: {"device": "device-1"})
    async def action():
      raise RuntimeError("expected failure")

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(RuntimeError, "expected failure"):
        await action()

    self.assertEqual([event.name for event in events], [
      "device.action.started",
      "device.action.failed",
    ])
    self.assertEqual(events[1].data["error_type"], "RuntimeError")
    self.assertEqual(events[1].context["operation_id"], events[0].context["operation_id"])

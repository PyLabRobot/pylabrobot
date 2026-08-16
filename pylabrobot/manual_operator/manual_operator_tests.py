import asyncio
import unittest
from typing import Callable

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.manual_operator import (
  ConsoleOperatorActionProvider,
  ManualOperator,
  OperatorActionCancelledError,
  OperatorActionFailedError,
  OperatorActionRequest,
  OperatorActionResult,
)
from pylabrobot.resources import Coordinate, Resource, ResourceHolder


class RecordingProvider:
  def __init__(self, result: OperatorActionResult):
    self.result = result
    self.requests: list[OperatorActionRequest] = []

  async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
    self.requests.append(action)
    return self.result


class CallbackProvider:
  def __init__(self, callback: Callable[[OperatorActionRequest], OperatorActionResult]):
    self.callback = callback
    self.requests: list[OperatorActionRequest] = []

  async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
    self.requests.append(action)
    return self.callback(action)


class TestManualOperator(unittest.IsolatedAsyncioTestCase):
  async def test_perform_sends_structured_request_and_returns_completion(self):
    provider = RecordingProvider(OperatorActionResult.completed(confirmed_by="operator-1"))
    operator = ManualOperator(provider, name="cell_operator")

    result = await operator.perform(
      action="centrifuge.spin",
      title="Spin sample plate",
      instructions="Spin plate_1 at 300 x g for 180 seconds.",
      confirmation_text="Confirm spin completed",
      details={"relative_centrifugal_force": 300, "duration": 180},
    )

    self.assertEqual(result.confirmed_by, "operator-1")
    self.assertEqual(len(provider.requests), 1)
    request = provider.requests[0]
    self.assertEqual(request.operator_name, "cell_operator")
    self.assertEqual(request.action, "centrifuge.spin")
    self.assertEqual(request.details["duration"], 180)

  async def test_cancelled_result_raises_specific_error(self):
    provider = RecordingProvider(OperatorActionResult.cancelled(message="Protocol stopped"))
    operator = ManualOperator(provider)

    with self.assertRaisesRegex(OperatorActionCancelledError, "Protocol stopped"):
      await operator.perform(action="inspect", title="Inspect plate", instructions="Inspect it.")

  async def test_failed_result_raises_specific_error(self):
    provider = RecordingProvider(OperatorActionResult.failed(message="Plate was damaged"))
    operator = ManualOperator(provider)

    with self.assertRaisesRegex(OperatorActionFailedError, "Plate was damaged"):
      await operator.perform(action="inspect", title="Inspect plate", instructions="Inspect it.")

  async def test_provider_exception_propagates(self):
    class FailingProvider:
      async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
        del action
        raise ConnectionError("provider disconnected")

    with self.assertRaisesRegex(ConnectionError, "provider disconnected"):
      await ManualOperator(FailingProvider()).perform(
        action="inspect", title="Inspect plate", instructions="Inspect it."
      )

  async def test_request_copies_details(self):
    details = {"duration": 60}
    request = OperatorActionRequest(
      operator_name="operator",
      action="centrifuge.spin",
      title="Spin",
      instructions="Spin the plate.",
      details=details,
    )

    details["duration"] = 120

    self.assertEqual(request.details["duration"], 60)

  async def test_move_resource_reassigns_only_after_completion(self):
    source = ResourceHolder("source", size_x=100, size_y=100, size_z=10)
    destination = ResourceHolder(
      "destination",
      size_x=100,
      size_y=100,
      size_z=10,
      child_location=Coordinate(4, 5, 6),
    )
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    source.assign_child_resource(plate)

    def complete_while_model_is_unchanged(
      request: OperatorActionRequest,
    ) -> OperatorActionResult:
      self.assertIs(plate.parent, source)
      self.assertIs(source.resource, plate)
      self.assertIsNone(destination.resource)
      self.assertEqual(request.action, "resource.move")
      return OperatorActionResult.completed(confirmed_by="operator-1")

    provider = CallbackProvider(complete_while_model_is_unchanged)
    result = await ManualOperator(provider).move_resource(
      resource=plate,
      source=source,
      destination=destination,
    )

    self.assertEqual(result.confirmed_by, "operator-1")
    self.assertIsNone(source.resource)
    self.assertIs(destination.resource, plate)
    self.assertEqual(plate.location, Coordinate(4, 5, 6))
    request = provider.requests[0]
    self.assertEqual(
      request.details,
      {"resource": "plate", "source": "source", "destination": "destination"},
    )

  async def test_move_resource_uses_explicit_destination_location(self):
    source = ResourceHolder("source", size_x=100, size_y=100, size_z=10)
    destination = Resource("destination", size_x=100, size_y=100, size_z=10)
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    source.assign_child_resource(plate)
    location = Coordinate(1, 2, 3)
    provider = RecordingProvider(OperatorActionResult.completed())

    await ManualOperator(provider).move_resource(
      resource=plate,
      source=source,
      destination=destination,
      destination_location=location,
      details={"reason": "manual handoff", "source": "ignored override"},
    )

    self.assertIs(plate.parent, destination)
    self.assertEqual(plate.location, location)
    self.assertEqual(provider.requests[0].details["reason"], "manual handoff")
    self.assertEqual(provider.requests[0].details["source"], "source")
    self.assertEqual(
      provider.requests[0].details["destination_location"],
      {"x": 1, "y": 2, "z": 3, "type": "Coordinate"},
    )

  async def test_move_resource_cancellation_leaves_model_unchanged(self):
    source = ResourceHolder("source", size_x=100, size_y=100, size_z=10)
    destination = ResourceHolder("destination", size_x=100, size_y=100, size_z=10)
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    source.assign_child_resource(plate)
    provider = RecordingProvider(OperatorActionResult.cancelled())

    with self.assertRaises(OperatorActionCancelledError):
      await ManualOperator(provider).move_resource(
        resource=plate,
        source=source,
        destination=destination,
      )

    self.assertIs(source.resource, plate)
    self.assertIsNone(destination.resource)

  async def test_move_resource_rejects_incorrect_source_before_prompt(self):
    actual_source = ResourceHolder("actual_source", size_x=100, size_y=100, size_z=10)
    stated_source = ResourceHolder("stated_source", size_x=100, size_y=100, size_z=10)
    destination = ResourceHolder("destination", size_x=100, size_y=100, size_z=10)
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    actual_source.assign_child_resource(plate)
    provider = RecordingProvider(OperatorActionResult.completed())

    with self.assertRaisesRegex(ValueError, "not source 'stated_source'"):
      await ManualOperator(provider).move_resource(
        resource=plate,
        source=stated_source,
        destination=destination,
      )

    self.assertEqual(provider.requests, [])
    self.assertIs(plate.parent, actual_source)

  async def test_move_resource_rejects_occupied_destination_before_prompt(self):
    source = ResourceHolder("source", size_x=100, size_y=100, size_z=10)
    destination = ResourceHolder("destination", size_x=100, size_y=100, size_z=10)
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    other_plate = Resource("other_plate", size_x=80, size_y=60, size_z=15)
    source.assign_child_resource(plate)
    destination.assign_child_resource(other_plate)
    provider = RecordingProvider(OperatorActionResult.completed())

    with self.assertRaisesRegex(RuntimeError, "already has a resource"):
      await ManualOperator(provider).move_resource(
        resource=plate,
        source=source,
        destination=destination,
      )

    self.assertEqual(provider.requests, [])
    self.assertIs(source.resource, plate)
    self.assertIs(destination.resource, other_plate)

  async def test_move_resource_detects_model_change_while_pending(self):
    source = ResourceHolder("source", size_x=100, size_y=100, size_z=10)
    destination = ResourceHolder("destination", size_x=100, size_y=100, size_z=10)
    unexpected = ResourceHolder("unexpected", size_x=100, size_y=100, size_z=10)
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    source.assign_child_resource(plate)

    def change_model_then_complete(_request: OperatorActionRequest) -> OperatorActionResult:
      unexpected.assign_child_resource(plate)
      return OperatorActionResult.completed()

    provider = CallbackProvider(change_model_then_complete)
    with self.assertRaisesRegex(RuntimeError, "model changed while the manual move was pending"):
      await ManualOperator(provider).move_resource(
        resource=plate,
        source=source,
        destination=destination,
      )

    self.assertIs(plate.parent, unexpected)
    self.assertIsNone(destination.resource)

  async def test_perform_emits_action_specific_lifecycle_events(self):
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    provider = RecordingProvider(
      OperatorActionResult.completed(message="Spin verified", confirmed_by="operator-1")
    )
    event_bus = EventBus()
    events: list[PLREvent] = []
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await ManualOperator(provider, name="cell_operator").perform(
        action="centrifuge.spin",
        title="Spin sample plate",
        instructions="Spin plate at 300 x g for 180 seconds.",
        details={"relative_centrifugal_force": 300, "duration": 180},
        resources=[plate],
      )

    self.assertEqual(
      [event.name for event in events],
      [
        "manual_operator.centrifuge.spin.started",
        "manual_operator.centrifuge.spin.completed",
      ],
    )
    self.assertEqual(events[0].context["operation"], "manual_operator.centrifuge.spin")
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])
    self.assertEqual(
      events[0].data["device"],
      {"name": "cell_operator", "type": "ManualOperator"},
    )
    self.assertEqual(events[0].data["resources"][0]["name"], "plate")
    self.assertEqual(events[0].data["manual_action"], "centrifuge.spin")
    self.assertEqual(events[0].data["details"]["relative_centrifugal_force"], 300)
    self.assertNotIn("confirmed_by", events[0].data)
    self.assertEqual(events[1].data["confirmed_by"], "operator-1")
    self.assertEqual(events[1].data["result_message"], "Spin verified")

  async def test_perform_emits_failed_event_for_reported_failure(self):
    provider = RecordingProvider(OperatorActionResult.failed(message="Inspection failed"))
    event_bus = EventBus()
    events: list[PLREvent] = []
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(OperatorActionFailedError):
        await ManualOperator(provider).perform(
          action="quality_control.inspect",
          title="Inspect sample",
          instructions="Inspect the sample.",
        )

    self.assertEqual(
      [event.name for event in events],
      [
        "manual_operator.quality_control.inspect.started",
        "manual_operator.quality_control.inspect.failed",
      ],
    )
    self.assertEqual(events[1].data["error_type"], "OperatorActionFailedError")
    self.assertEqual(events[1].data["error_message"], "Inspection failed")

  async def test_move_resource_emits_resource_and_endpoint_context(self):
    source = ResourceHolder("source", size_x=100, size_y=100, size_z=10)
    destination = ResourceHolder("destination", size_x=100, size_y=100, size_z=10)
    plate = Resource("plate", size_x=80, size_y=60, size_z=15)
    source.assign_child_resource(plate)
    provider = RecordingProvider(OperatorActionResult.completed())
    event_bus = EventBus()
    events: list[PLREvent] = []
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await ManualOperator(provider).move_resource(
        resource=plate,
        source=source,
        destination=destination,
      )

    manual_events = [event for event in events if event.name.startswith("manual_operator.")]
    self.assertEqual(
      [event.name for event in manual_events],
      ["manual_operator.resource.move.started", "manual_operator.resource.move.completed"],
    )
    self.assertEqual(manual_events[0].data["resources"][0]["name"], "plate")
    self.assertEqual(manual_events[0].data["source"]["name"], "source")
    self.assertEqual(manual_events[0].data["destination"]["name"], "destination")
    self.assertEqual(
      [event.name for event in events if event.name.startswith("resource.")],
      ["resource.unassigned", "resource.assigned"],
    )
    self.assertIs(plate.parent, destination)


class TestConsoleOperatorActionProvider(unittest.IsolatedAsyncioTestCase):
  async def test_enter_completes_action_without_blocking_event_loop(self):
    output: list[str] = []

    def input_fn(prompt: str) -> str:
      output.append(prompt)
      return ""

    provider = ConsoleOperatorActionProvider(
      input_fn=input_fn,
      output_fn=output.append,
    )
    request = OperatorActionRequest(
      operator_name="operator",
      action="inspect",
      title="Inspect plate",
      instructions="Check that the plate is seated.",
    )
    event_loop_progressed = asyncio.Event()

    async def mark_progress() -> None:
      await asyncio.sleep(0)
      event_loop_progressed.set()

    progress_task = asyncio.create_task(mark_progress())
    result = await provider.request(request)
    await progress_task

    self.assertTrue(event_loop_progressed.is_set())
    self.assertEqual(result, OperatorActionResult.completed())
    self.assertIn("Inspect plate", output[0])
    self.assertEqual(output[1], "Confirm action completed: ")

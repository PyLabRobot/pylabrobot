"""Semantic ODTC events through fake SOAP exchanges; no hardware connections."""

import asyncio
import json
import unittest
import xml.etree.ElementTree as ET
from contextvars import Context
from unittest.mock import AsyncMock, patch

from pylabrobot.events import (
  EventBus,
  PLREvent,
  event_context,
  set_default_event_bus,
  use_event_bus,
)
from pylabrobot.inheco.odtc import (
  ODTC,
  ODTCProtocol,
  ODTCProtocolStopped,
  ODTCStage,
  ODTCStep,
  compile_protocol,
)
from pylabrobot.inheco.odtc.tests.odtc_tests import FIXTURES, MockODTC, profile
from pylabrobot.inheco.soap import soap_encode
from pylabrobot.inheco.transport.sila import SiLAError
from pylabrobot.resources import Plate


class ODTCEventTests(unittest.IsolatedAsyncioTestCase):
  """Observe public operation boundaries without replacing the driver's workflows."""

  async def asyncSetUp(self) -> None:
    """Initialize a fake instrument before activating the test's event subscription."""
    server_patch = patch("pylabrobot.inheco.transport.sila.HTTPServer")
    server = server_patch.start().return_value
    self.addCleanup(server_patch.stop)
    server.setup, server.stop, server.bound_port = AsyncMock(), AsyncMock(), 8000
    self.odtc = ODTC("odtc", "127.0.0.1", client_ip="127.0.0.1")
    self.fake = MockODTC()
    self.fake.callback = self.odtc._sila._handle_event
    post_patch = patch.object(self.odtc._sila.io, "request_raw", side_effect=self.fake.post)
    post_patch.start()
    self.addCleanup(post_patch.stop)
    await self.odtc.setup()
    self.bus = EventBus()
    self.events: list[PLREvent] = []
    self.bus.subscribe(self.events.append)

  async def asyncTearDown(self) -> None:
    """Close the fake session without adding events to the observed operation."""
    self.fake.reject.clear()
    await self.odtc.stop()

  def lifecycle(self, operation: str, outcome: str = "completed") -> list[PLREvent]:
    """Check one correlated lifecycle, stable invocation data, and JSON-ready references."""
    name = f"thermocycler.{operation}"
    events = [event for event in self.events if event.context.get("operation") == name]
    self.assertEqual([event.name for event in events], [f"{name}.started", f"{name}.{outcome}"])
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])
    for event in events:
      self.assertEqual(event.data["device"], {"name": "odtc", "type": "ODTC"})
      self.assertIn("resources", event.data)
      self.assertNotIn("source", event.data)
      self.assertNotIn("destination", event.data)
      json.dumps(event.as_dict())
    for key, value in events[0].data.items():
      self.assertEqual(events[1].data[key], value)
    return events

  async def test_setup_stop_and_cached_setup(self) -> None:
    """Lifecycle events describe connection work and its nested temperature stop."""
    with use_event_bus(self.bus):
      await self.odtc.stop()
    self.lifecycle("stop")
    self.lifecycle("stop_protocol")
    self.assertEqual(
      [event.name for event in self.events],
      [
        "thermocycler.stop.started",
        "thermocycler.stop_protocol.started",
        "thermocycler.stop_protocol.completed",
        "thermocycler.stop.completed",
      ],
    )
    for cached in (False, True):
      self.events.clear()
      with use_event_bus(self.bus):
        await self.odtc.setup(simulation_mode=False)
      start, _ = self.lifecycle("setup")
      self.assertEqual(start.data["already_satisfied"], cached)
      self.assertIs(start.data["simulation_mode"], False)

  async def test_door_resource_snapshot_cache_and_firmware_failure(self) -> None:
    """Door events retain loaded-plate identity and never claim a plate transfer."""
    plate = Plate("pcr_plate", 127.76, 85.48, 15, ordered_items={})
    self.odtc.plate_holder.assign_child_resource(plate)
    for cached in (False, True):
      self.events.clear()
      with use_event_bus(self.bus), event_context(frame_run_id="pcr-run"):
        await self.odtc.open_door()
      start, end = self.lifecycle("open_door")
      self.assertEqual(start.data["already_satisfied"], cached)
      self.assertEqual(start.data["resources"][0]["name"], "pcr_plate")
      self.assertEqual(start.data["resources"][0]["ancestors"][0]["name"], "odtc_plate_holder")
      self.assertEqual(end.context["frame_run_id"], "pcr-run")
    self.assertEqual(self.fake.commands.count("OpenDoor"), 1)
    self.events.clear()
    self.fake.reject["CloseDoor"] = 2002
    with use_event_bus(self.bus), self.assertRaises(SiLAError):
      await self.odtc.close_door()
    _, failed = self.lifecycle("close_door", "failed")
    self.assertEqual(failed.data["error_type"], "SiLAError")
    self.assertIsNone(self.odtc.door_open)
    self.fake.reject.clear()
    self.events.clear()
    with use_event_bus(self.bus):
      await self.odtc.close_door()
    self.lifecycle("close_door")

  async def test_prepare_targets_cache_and_validation(self) -> None:
    """Preparation reports Celsius targets and cached calls without extra executions."""
    for cached in (False, True):
      self.events.clear()
      with use_event_bus(self.bus):
        await self.odtc.prepare(98, lid_temperature=110, timeout=60)
      start, end = self.lifecycle("prepare")
      self.assertEqual(start.data["target_temperatures"], [98])
      self.assertEqual(start.data["target_lid_temperatures"], [110])
      self.assertEqual(start.data["timeout"], 60)
      self.assertEqual(end.data["already_satisfied"], cached)
      self.assertEqual(start.data["resources"], [])
      self.assertEqual(len(self.events), 2)
    self.assertEqual(self.fake.executed, ["plr_currentPreMethod"])
    self.events.clear()
    before = list(self.fake.commands)
    with use_event_bus(self.bus), self.assertRaises(ValueError):
      await self.odtc.prepare(100, 110)
    self.lifecycle("prepare", "failed")
    self.assertEqual(self.fake.commands, before)

  async def test_profile_execution_waits_and_summarizes_nested_repeats(self) -> None:
    """Completion follows the callback and reports authored counts, not compiler expansion."""
    program = ODTCProtocol(
      [ODTCStage([ODTCStep(95, 30), ODTCStage([ODTCStep(55, 20), ODTCStep(72, 60)], 2)], 3)]
    )
    self.fake.hold_name = "plr_currentProtocol"
    with use_event_bus(self.bus), event_context(frame_run_id="pcr-run"):
      task = asyncio.create_task(
        self.odtc.run_protocol(program, volume=60, lid_temperature=110, post_heating=False)
      )
      await asyncio.wait_for(self.fake.started.wait(), 1)
      self.assertEqual([e.name for e in self.events], ["thermocycler.execute_protocol.started"])
      await self.fake.complete()
      await task
    start, end = self.lifecycle("execute_protocol")
    self.assertEqual(start.data["block_max_volume"], 60)
    self.assertEqual(start.data["lid_temperature"], 110)
    self.assertIs(start.data["post_heating"], False)
    self.assertEqual(end.context["frame_run_id"], "pcr-run")
    self.assertEqual(
      [end.data[key] for key in ("stage_count", "step_definition_count", "step_execution_count")],
      [1, 3, 15],
    )
    self.assertEqual(end.data["temperature_zone_count"], 1)
    self.assertNotIn("step_execution_count", start.data)
    self.assertEqual(len(self.events), 2)

  async def test_upload_and_stored_execution_do_not_invent_volume_or_stages(self) -> None:
    """Stored-method events omit information absent from native method storage."""
    method = compile_protocol(profile("stored"), variant=96, volume=60, lid_temperature=110)
    with use_event_bus(self.bus):
      await self.odtc.upload_protocol(method)
    start, _ = self.lifecycle("upload_protocol")
    self.assertEqual(start.data["method_name"], "stored")
    self.assertIs(start.data["overwrite"], False)
    self.events.clear()
    with use_event_bus(self.bus), self.assertRaises(ValueError):
      await self.odtc.upload_protocol(method)
    self.lifecycle("upload_protocol", "failed")
    self.events.clear()
    with use_event_bus(self.bus):
      await self.odtc.run_stored_protocol("stored")
    start, end = self.lifecycle("execute_protocol")
    self.assertEqual(start.data["protocol_source"], "stored")
    self.assertNotIn("post_heating", start.data)
    self.assertIs(end.data["post_heating"], True)
    self.assertNotIn("block_max_volume", end.data)
    self.assertNotIn("stage_count", end.data)
    self.assertEqual(len(self.events), 2)

  async def test_execution_validation_failure_has_lifecycle_without_hardware(self) -> None:
    """Invalid invocation data produces a failed lifecycle without executing a method."""
    before = list(self.fake.commands)
    with use_event_bus(self.bus), self.assertRaises(ValueError):
      await self.odtc.run_protocol(profile(), volume=0, lid_temperature=110)
    _, end = self.lifecycle("execute_protocol", "failed")
    self.assertEqual(end.data["error_type"], "ValueError")
    self.assertEqual(self.fake.commands, before)

  async def test_cancelled_execution_does_not_stop_or_later_complete_event(self) -> None:
    """A cancelled waiter has one terminal event even if hardware subsequently finishes."""
    self.fake.hold_name = "plr_currentProtocol"
    with use_event_bus(self.bus):
      task = asyncio.create_task(self.odtc.run_protocol(profile(), volume=60, lid_temperature=110))
      await asyncio.wait_for(self.fake.started.wait(), 1)
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task
      _, failed = self.lifecycle("execute_protocol", "failed")
      self.assertEqual(failed.data["error_type"], "CancelledError")
      self.assertNotIn("StopMethod", self.fake.commands)
      await self.fake.complete()
      await self.odtc.wait_for_completion()
    self.assertEqual(len(self.events), 2)

  async def test_prepare_can_be_stopped_with_independent_correlated_lifecycles(self) -> None:
    """Acknowledged stop succeeds while the interrupted preparation fails."""
    self.fake.hold_name = "plr_currentPreMethod"
    with use_event_bus(self.bus):
      task = asyncio.create_task(self.odtc.prepare(98, 110))
      await asyncio.wait_for(self.fake.started.wait(), 1)
      await self.odtc.stop_protocol()
      with self.assertRaises(ODTCProtocolStopped):
        await task
    prep = self.lifecycle("prepare", "failed")
    stop = self.lifecycle("stop_protocol")
    self.assertNotEqual(prep[0].context["operation_id"], stop[0].context["operation_id"])
    self.assertEqual(self.fake.executed, ["plr_currentPreMethod"])

  async def test_timeout_failure_does_not_stop_temperature_control(self) -> None:
    """Timeout is a failed wait, not an implicit device stop."""
    self.fake.hold_name = "plr_currentProtocol"
    with use_event_bus(self.bus), self.assertRaises(asyncio.TimeoutError):
      await self.odtc.run_protocol(profile(), volume=60, lid_temperature=110, timeout=0.001)
    _, failed = self.lifecycle("execute_protocol", "failed")
    self.assertEqual(failed.data["error_type"], "TimeoutError")
    self.assertNotIn("StopMethod", self.fake.commands)

  async def test_reads_are_quiet_and_subscriber_errors_do_not_change_control(self) -> None:
    """Read helpers have no lifecycles and failing listeners cannot prevent door motion."""
    with use_event_bus(self.bus):
      await self.odtc.request_status()
      await self.odtc.request_temperatures()
      await self.odtc.request_progress()
      await self.odtc.request_method_set()
      await self.odtc.wait_for_completion()
    self.assertEqual(self.events, [])

    def broken_listener(event: PLREvent) -> None:
      """Simulate a consumer failure independent of hardware control."""
      raise RuntimeError("subscriber failed")

    self.bus.subscribe(broken_listener)
    with use_event_bus(self.bus), self.assertLogs("pylabrobot.events.bus", level="ERROR"):
      await self.odtc.open_door()
    self.lifecycle("open_door")
    self.assertTrue(self.odtc.door_open)
    self.events.clear()
    await self.odtc.close_door()
    with use_event_bus(EventBus()):
      await self.odtc.open_door()
    self.assertEqual(self.events, [])

  async def test_failed_stop_still_disconnects_and_failed_setup_does_not_reset_busy_device(
    self,
  ) -> None:
    """Observation preserves transport cleanup and the refusal to reset an active device."""
    self.fake.reject["StopMethod"] = 2002
    with use_event_bus(self.bus), self.assertRaises(SiLAError):
      await self.odtc.stop()
    self.lifecycle("stop_protocol", "failed")
    self.lifecycle("stop", "failed")
    self.fake.reject.clear()
    self.events.clear()
    with use_event_bus(self.bus), self.assertRaisesRegex(RuntimeError, "not set up"):
      await self.odtc.open_door()
    self.lifecycle("open_door", "failed")
    self.events.clear()
    self.fake.state = "Busy"
    before = list(self.fake.commands)
    with use_event_bus(self.bus), self.assertRaisesRegex(RuntimeError, "will not reset"):
      await self.odtc.setup()
    self.lifecycle("setup", "failed")
    self.assertEqual(self.fake.commands, [*before, "GetStatus"])

  async def test_execution_firmware_error_is_preserved(self) -> None:
    """An asynchronous device failure is the same exception observed by the caller."""
    self.fake.hold_name = "plr_currentProtocol"
    with use_event_bus(self.bus):
      task = asyncio.create_task(self.odtc.run_protocol(profile(), volume=60, lid_temperature=110))
      await asyncio.wait_for(self.fake.started.wait(), 1)
      await self.fake.complete(code=9)
      with self.assertRaises(SiLAError) as raised:
        await task
    _, failed = self.lifecycle("execute_protocol", "failed")
    self.assertEqual(failed.data["error_type"], "SiLAError")
    self.assertEqual(failed.data["error_message"], str(raised.exception))

  async def send_samples(self, request_id: int) -> None:
    """Deliver a real-format callback through the SiLA dispatcher."""
    await self.odtc._sila._handle_event(
      soap_encode(
        "DataEvent",
        {
          "requestId": request_id,
          "dataValue": (FIXTURES / "firmware_manual_data_event.xml").read_text(),
        },
        method_ns="http://sila.coop",
      ).encode()
    )

  async def test_invalid_telemetry_does_not_finish_preparation(self) -> None:
    """Bad observations preserve progress and leave completion to the device response."""
    xml = (FIXTURES / "firmware_manual_data_event.xml").read_text()
    invalid_batches = ["<invalid"]
    for empty in (False, True):
      outer = ET.fromstring(xml)
      assert outer[0].text is not None
      capture = ET.fromstring(outer[0].text)
      values = capture.find(".//seriesValueSet")
      assert values is not None
      if empty:
        values.clear()
      else:
        values.remove(values[0])
      outer[0].text = ET.tostring(capture, encoding="unicode")
      invalid_batches.append(ET.tostring(outer, encoding="unicode"))

    self.fake.hold_name = "plr_currentPreMethod"
    for completion_code, target in ((3, 98), (9, 97)):
      self.events.clear()
      self.fake.started.clear()
      with use_event_bus(self.bus):
        task = asyncio.create_task(self.odtc.prepare(target, 110))
        await asyncio.wait_for(self.fake.started.wait(), 1)
        request_id = self.fake.running_id
        assert request_id is not None
        await self.send_samples(request_id)
        latest = await self.odtc.request_progress()
        event_count = len(self.events)
        for data in invalid_batches:
          with self.assertLogs("pylabrobot.inheco.odtc.odtc", level="WARNING"):
            ack = await self.odtc._sila._handle_event(
              soap_encode(
                "DataEvent",
                {"requestId": request_id, "dataValue": data},
                method_ns="http://sila.coop",
              ).encode()
            )
          self.assertIn(b"DataEventResponse", ack)
          self.assertEqual(self.odtc._sila.pending_command_name(request_id), "ExecuteMethod")
          self.assertFalse(task.done())
          self.assertEqual(await self.odtc.request_progress(), latest)
          self.assertEqual(len(self.events), event_count)
        await self.send_samples(request_id)
        self.assertEqual(len(self.events), event_count + 1)
        await self.fake.complete(code=completion_code)
        if completion_code == 3:
          await task
          self.assertEqual(self.events[-1].name, "thermocycler.prepare.completed")
        else:
          with self.assertRaises(SiLAError):
            await task
          self.assertEqual(self.events[-1].name, "thermocycler.prepare.failed")
    self.assertNotIn("StopMethod", self.fake.commands)

  async def test_early_telemetry_separates_preparation_and_protocol_executions(self) -> None:
    """Callbacks before HTTP acceptance retain every sample and the parent operation ID."""
    plate = Plate("pcr_plate", 127.76, 85.48, 15, ordered_items={})
    self.odtc.plate_holder.assign_child_resource(plate)
    self.fake.emit_progress = True
    with use_event_bus(self.bus), event_context(frame_run_id="pcr-run"):
      await self.odtc.run_protocol(profile(), volume=60, lid_temperature=110)
    start = self.events[0]
    batches = [e for e in self.events if e.name == "thermocycler.temperature_samples"]
    self.assertEqual(len(batches), 2)
    self.assertNotEqual(batches[0].data["execution_id"], batches[1].data["execution_id"])
    self.assertEqual([e.data["method_kind"] for e in batches], ["preparation", "protocol"])
    self.assertEqual(
      [e.data["method_name"] for e in batches], ["plr_currentPreMethod", "plr_currentProtocol"]
    )
    for batch in batches:
      self.assertEqual(batch.context["frame_run_id"], "pcr-run")
      self.assertEqual(batch.context["operation_id"], start.context["operation_id"])
      self.assertEqual(batch.data["device"], {"name": "odtc", "type": "ODTC"})
      self.assertEqual(batch.data["resources"][0]["name"], plate.name)
      self.assertEqual(len(batch.data["samples"]), 5)
      self.assertEqual(
        batch.data["samples"][0],
        {
          "elapsed": 0.19,
          "target_temperature": 21.42,
          "current_temperature": 21.39,
          "lid_temperature": 23.68,
        },
      )
      json.dumps(batch.as_dict())
    latest = await self.odtc.request_progress()
    assert latest is not None
    self.assertEqual(latest.elapsed, 4.355)
    self.assertEqual(latest.current_temperature, 16.56)
    self.assertEqual(self.events[-1].name, "thermocycler.execute_protocol.completed")

  async def test_telemetry_restores_origin_after_cancellation_and_ignores_late_callbacks(
    self,
  ) -> None:
    """A callback task's unrelated bus/run cannot steal samples from a still-running method."""
    self.fake.hold_name = "plr_currentPreMethod"
    other_bus = EventBus()
    other_events: list[PLREvent] = []
    other_bus.subscribe(other_events.append)
    # Exercise capture of the process default bus, not only a task-local use_event_bus.
    previous = set_default_event_bus(self.bus)
    self.addCleanup(set_default_event_bus, previous)
    with event_context(frame_run_id="original-run"):
      task = asyncio.create_task(self.odtc.prepare(98, 110))
    await asyncio.wait_for(self.fake.started.wait(), 1)
    request_id = self.fake.running_id
    assert request_id is not None
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    parent_id = self.events[0].context["operation_id"]
    set_default_event_bus(other_bus)

    async def callback() -> None:
      """Model an HTTP server callback with an unrelated caller context."""
      with use_event_bus(other_bus), event_context(frame_run_id="other-run"):
        await self.send_samples(request_id)

    # create_task(context=...) requires Python 3.11; Context.run also supports PLR's 3.10 floor.
    await Context().run(asyncio.create_task, callback())
    self.assertEqual(other_events, [])
    batch = self.events[-1]
    self.assertEqual(batch.name, "thermocycler.temperature_samples")
    self.assertEqual(batch.context["frame_run_id"], "original-run")
    self.assertEqual(batch.context["operation_id"], parent_id)
    self.assertNotIn("StopMethod", self.fake.commands)
    count = len(self.events)
    await self.fake.complete()
    await self.send_samples(request_id)
    await self.send_samples(request_id + 999)
    self.assertEqual(len(self.events), count)
    self.assertEqual(other_events, [])

  async def test_acknowledged_stop_ends_telemetry_before_future_cleanup_runs(self) -> None:
    """Late callbacks cannot emit observations after an acknowledged stop."""
    self.fake.hold_name = "plr_currentPreMethod"
    with use_event_bus(self.bus):
      task = asyncio.create_task(self.odtc.prepare(98, 110))
      await asyncio.wait_for(self.fake.started.wait(), 1)
      request_id = self.fake.running_id
      assert request_id is not None
      await self.send_samples(request_id)
      await self.odtc.stop_protocol()
      await self.send_samples(request_id)
      with self.assertRaises(ODTCProtocolStopped):
        await task
    batches = [event for event in self.events if event.name == "thermocycler.temperature_samples"]
    self.assertEqual(len(batches), 1)

  async def test_telemetry_listener_failure_and_no_bus_leave_progress_and_commands_unchanged(
    self,
  ) -> None:
    """Telemetry is optional observation and creates no additional hardware queries."""
    self.fake.emit_progress = True

    def broken_listener(event: PLREvent) -> None:
      """Fail only telemetry consumption, leaving lifecycle observation intact."""
      if event.name == "thermocycler.temperature_samples":
        raise RuntimeError("telemetry subscriber failed")

    self.bus.subscribe(broken_listener)
    with use_event_bus(self.bus), self.assertLogs("pylabrobot.events.bus", level="ERROR"):
      await self.odtc.run_protocol(profile(), volume=60, lid_temperature=110)
    self.assertEqual(self.events[-1].name, "thermocycler.execute_protocol.completed")
    commands = list(self.fake.commands[5:])  # Exclude initial setup.
    self.fake.commands.clear()
    self.events.clear()
    previous = set_default_event_bus(None)
    try:
      await self.odtc.run_protocol(profile(), volume=60, lid_temperature=110)
    finally:
      set_default_event_bus(previous)
    self.assertEqual(self.events, [])
    self.assertEqual(self.fake.commands, commands)
    latest = await self.odtc.request_progress()
    assert latest is not None
    self.assertEqual(latest.current_temperature, 16.56)

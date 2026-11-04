import importlib
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.heating_shaking import HeaterShaker as LegacyHeaterShaker
from pylabrobot.legacy.liquid_handling import LiquidHandler as LegacyLiquidHandler
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.legacy.only_fans import Fan as LegacyFan
from pylabrobot.legacy.storage import Incubator as LegacyIncubator
from pylabrobot.legacy.temperature_controlling import (
  TemperatureController as LegacyTemperatureController,
)
from pylabrobot.testing import AnyioTestBase


class RecordingBackend(MachineBackend):
  def __init__(
    self,
    events: list[str],
    fail_setup: bool = False,
    fail_stop: bool = False,
  ) -> None:
    super().__init__()
    self.events = events
    self.fail_setup = fail_setup
    self.fail_stop = fail_stop
    self.options: dict[str, Any] = {}

  async def _record_stop(self) -> None:
    self.events.append("backend.stop")
    if self.fail_stop:
      raise RuntimeError("backend stop failed")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    self.options = backend_kwargs
    self.events.append("backend.setup")
    if self.fail_setup:
      raise RuntimeError("backend setup failed")
    stack.push_shielded_async_callback(self._record_stop)


class NativeRecordingBackend(MachineBackend):
  def __init__(self, events: list[str]) -> None:
    super().__init__()
    self.events = events
    self.options: dict[str, Any] = {}
    self.exit_exception: Optional[BaseException] = None

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    raise AssertionError("custom lifespan must own entry")

  @asynccontextmanager
  async def lifespan(self, **backend_kwargs: Any) -> AsyncIterator[HasLifespan]:
    self.options = backend_kwargs
    self.events.append("backend.setup")
    try:
      yield self
    except BaseException as error:
      self.exit_exception = error
      raise
    finally:
      self.events.append("backend.stop")


class CancellationRecordingBackend(MachineBackend):
  def __init__(self, events: list[str]) -> None:
    super().__init__()
    self.events = events
    self.options: dict[str, Any] = {}

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    raise AssertionError("custom lifespan must own entry")

  @asynccontextmanager
  async def lifespan(self, **backend_kwargs: Any) -> AsyncIterator[HasLifespan]:
    self.options = backend_kwargs
    self.events.append("backend.setup")
    try:
      yield self
    finally:
      self.events.append("backend.stop.started")
      with anyio.CancelScope(shield=True):
        await anyio.sleep(0)
      self.events.append("backend.stop.completed")


class NativeFailingEntryBackend(MachineBackend):
  def __init__(self, events: list[str]) -> None:
    super().__init__()
    self.events = events
    self.options: dict[str, Any] = {}

  async def _rollback(self) -> None:
    self.events.append("backend.rollback")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    self.options = backend_kwargs
    self.events.append("backend.acquire")
    stack.push_shielded_async_callback(self._rollback)
    raise RuntimeError("native backend entry failed")


class RecordingMachine(Machine):
  def __init__(self, backend: MachineBackend, events: list[str]) -> None:
    super().__init__(backend)
    self.events = events
    self.fail_frontend_stop = False

  async def _pre_backend_stop(self) -> None:
    self.events.append("frontend.stop")
    if self.fail_frontend_stop:
      raise RuntimeError("frontend stop failed")


class CancellationRecordingMachine(RecordingMachine):
  async def _pre_backend_stop(self) -> None:
    self.events.append("frontend.stop.started")
    await anyio.sleep(0)
    self.events.append("frontend.stop.completed")


def _event_bus(events: list[PLREvent]) -> EventBus:
  event_bus = EventBus()
  event_bus.subscribe(events.append)
  return event_bus


def _reachable_failures(error: BaseException) -> list[str]:
  messages: list[str] = []
  seen: list[int] = []
  current: Optional[BaseException] = error
  while current is not None:
    assert id(current) not in seen, "exception context chain contains a cycle"
    seen.append(id(current))
    if isinstance(current, RuntimeError):
      messages.append(str(current))
    current = current.__context__
  return messages


def test_top_level_shims_reexport_legacy_classes() -> None:
  machines_module = importlib.import_module("pylabrobot.machines")
  with pytest.warns(DeprecationWarning):
    machines_module = importlib.reload(machines_module)
  assert machines_module.Machine is Machine

  liquid_handling_module = importlib.import_module("pylabrobot.liquid_handling")
  with pytest.warns(DeprecationWarning):
    liquid_handling_module = importlib.reload(liquid_handling_module)
  assert liquid_handling_module.LiquidHandler is LegacyLiquidHandler

  fan_module = importlib.import_module("pylabrobot.only_fans")
  with pytest.warns(DeprecationWarning):
    fan_module = importlib.reload(fan_module)
  assert fan_module.Fan is LegacyFan

  temperature_module = importlib.import_module("pylabrobot.temperature_controlling")
  with pytest.warns(DeprecationWarning):
    temperature_module = importlib.reload(temperature_module)
  assert temperature_module.TemperatureController is LegacyTemperatureController

  heater_shaker_module = importlib.import_module("pylabrobot.heating_shaking")
  with pytest.warns(DeprecationWarning):
    heater_shaker_module = importlib.reload(heater_shaker_module)
  assert heater_shaker_module.HeaterShaker is LegacyHeaterShaker

  storage_module = importlib.import_module("pylabrobot.storage")
  with pytest.warns(DeprecationWarning):
    storage_module = importlib.reload(storage_module)
  assert storage_module.Incubator is LegacyIncubator


class TestMachineSerialization:
  class SerializableBackend(MachineBackend):
    def __init__(self, mock_param: str) -> None:
      super().__init__()
      self.mock_param = mock_param

    async def _enter_lifespan(
      self,
      stack: AsyncExitStackWithShielding,
      **backend_kwargs: Any,
    ) -> None:
      pass

    def serialize(self) -> dict:
      return {**super().serialize(), "mock_param": self.mock_param}

  class SerializableMachine(Machine):
    pass

  def test_serialize(self) -> None:
    machine = self.SerializableMachine(backend=self.SerializableBackend("mock_param"))
    assert machine.serialize() == {
      "backend": {"mock_param": "mock_param", "type": "SerializableBackend"}
    }

  def test_deserialize(self) -> None:
    machine = self.SerializableMachine(backend=self.SerializableBackend("mock_param"))
    Machine.deserialize(machine.serialize())


class TestMachineSetupBridge(AnyioTestBase):
  _anyio_backends = ["asyncio"]

  async def test_setup_bridge_uses_machine_lifespan(self) -> None:
    events: list[PLREvent] = []
    lifecycle: list[str] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    backend = RecordingBackend(lifecycle)
    machine = RecordingMachine(backend, lifecycle)

    with use_event_bus(event_bus):
      with pytest.warns(DeprecationWarning, match="async with device"):
        await machine.setup(port="mock")
      assert machine.setup_finished
      assert backend.options == {"port": "mock"}
      await machine.stop()

    assert not machine.setup_finished
    assert [event.name for event in events] == [
      "machine.setup.started",
      "machine.setup.completed",
      "machine.stop.started",
      "machine.stop.completed",
    ]


class TestMachineLifespan(AnyioTestBase):
  async def test_entry_exit_order_options_and_state(self) -> None:
    events: list[str] = []
    backend = RecordingBackend(events)
    machine = RecordingMachine(backend, events)
    assert not machine.setup_finished
    async with machine.lifespan(port="mock"):
      assert machine.setup_finished
      assert backend.options == {"port": "mock"}
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]
    assert not machine.setup_finished

  async def test_success_emits_exact_events_and_context(self) -> None:
    emitted: list[PLREvent] = []
    machine = RecordingMachine(RecordingBackend([]), [])
    with use_event_bus(_event_bus(emitted)):
      async with machine:
        pass
    assert [event.name for event in emitted] == [
      "machine.setup.started",
      "machine.setup.completed",
      "machine.stop.started",
      "machine.stop.completed",
    ]
    assert all(event.data["backend"] == "RecordingBackend" for event in emitted)
    assert all(
      event.data["device"] == {"name": "RecordingMachine", "type": "RecordingMachine"}
      for event in emitted
    )

  async def test_lifecycle_events_observe_corresponding_resource_state(self) -> None:
    machine = RecordingMachine(RecordingBackend([]), [])
    observed: list[tuple[str, bool]] = []
    bus = EventBus()
    bus.subscribe(lambda event: observed.append((event.name, machine.is_active)))
    with use_event_bus(bus):
      async with machine:
        pass
    assert observed == [
      ("machine.setup.started", False),
      ("machine.setup.completed", True),
      ("machine.stop.started", False),
      ("machine.stop.completed", False),
    ]

  async def test_setup_failure_emits_failure_without_cleanup(self) -> None:
    events: list[str] = []
    emitted: list[PLREvent] = []
    machine = RecordingMachine(RecordingBackend(events, fail_setup=True), events)
    with use_event_bus(_event_bus(emitted)):
      with pytest.raises(RuntimeError, match="backend setup failed"):
        async with machine:
          pytest.fail("failed setup must not reach the body")
    assert events == ["backend.setup"]
    assert [event.name for event in emitted] == [
      "machine.setup.started",
      "machine.setup.failed",
    ]
    assert not machine.setup_finished

  async def test_stop_failure_emits_failure_and_retains_setup_state(self) -> None:
    events: list[str] = []
    emitted: list[PLREvent] = []
    machine = RecordingMachine(RecordingBackend(events, fail_stop=True), events)
    with use_event_bus(_event_bus(emitted)):
      with pytest.raises(RuntimeError, match="backend stop failed"):
        async with machine:
          pass
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]
    assert [event.name for event in emitted] == [
      "machine.setup.started",
      "machine.setup.completed",
      "machine.stop.started",
      "machine.stop.failed",
    ]
    assert not machine.setup_finished

  async def test_body_failure_still_cleans_up_and_propagates(self) -> None:
    events: list[str] = []
    machine = RecordingMachine(RecordingBackend(events), events)
    with pytest.raises(ValueError, match="body failed"):
      async with machine:
        raise ValueError("body failed")
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]
    assert not machine.setup_finished

  async def test_cancellation_after_backend_entry_shields_cleanup_and_propagates(self) -> None:
    lifecycle: list[str] = []
    emitted: list[PLREvent] = []
    body_started = anyio.Event()
    backend = CancellationRecordingBackend(lifecycle)
    machine = CancellationRecordingMachine(backend, lifecycle)

    async def run_machine() -> None:
      try:
        with use_event_bus(_event_bus(emitted)):
          async with machine:
            body_started.set()
            await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        lifecycle.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_machine)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert lifecycle == [
      "backend.setup",
      "frontend.stop.started",
      "frontend.stop.completed",
      "backend.stop.started",
      "backend.stop.completed",
      "cancelled",
    ]
    assert [event.name for event in emitted] == [
      "machine.setup.started",
      "machine.setup.completed",
      "machine.stop.started",
      "machine.stop.completed",
    ]
    assert not machine.setup_finished
    assert not machine.is_active
    assert not backend.is_active

  async def test_native_backend_entry_failure_rolls_back_through_machine(self) -> None:
    lifecycle: list[str] = []
    emitted: list[PLREvent] = []
    backend = NativeFailingEntryBackend(lifecycle)
    machine = RecordingMachine(backend, lifecycle)

    with use_event_bus(_event_bus(emitted)):
      with pytest.raises(RuntimeError, match="native backend entry failed"):
        async with machine:
          pytest.fail("failed native backend entry must not reach the body")

    assert lifecycle == ["backend.acquire", "backend.rollback"]
    assert [event.name for event in emitted] == [
      "machine.setup.started",
      "machine.setup.failed",
    ]
    assert not machine.setup_finished
    assert not machine.is_active
    assert not backend.is_active

  async def test_listener_failure_does_not_change_lifecycle(self) -> None:
    events: list[str] = []
    emitted: list[PLREvent] = []
    event_bus = _event_bus(emitted)

    def fail_listener(event: PLREvent) -> None:
      raise RuntimeError(f"listener failed for {event.name}")

    event_bus.subscribe(fail_listener)
    machine = RecordingMachine(RecordingBackend(events), events)
    with use_event_bus(event_bus):
      async with machine:
        pass
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]
    assert len(emitted) == 4
    assert not machine.setup_finished

  async def test_sequential_reuse_enters_and_exits_each_time(self) -> None:
    events: list[str] = []
    machine = RecordingMachine(RecordingBackend(events), events)
    async with machine:
      pass
    async with machine:
      pass
    assert events == [
      "backend.setup",
      "frontend.stop",
      "backend.stop",
      "backend.setup",
      "frontend.stop",
      "backend.stop",
    ]

  async def test_active_reentry_is_rejected_without_exiting_outer_lifespan(self) -> None:
    events: list[str] = []
    machine = RecordingMachine(RecordingBackend(events), events)
    async with machine:
      with pytest.raises(RuntimeError, match="already has an active lifespan"):
        async with machine:
          pytest.fail("nested entry must not reach the body")
      assert machine.setup_finished
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]

  async def test_frontend_cleanup_failure_still_exits_backend(self) -> None:
    events: list[str] = []
    machine = RecordingMachine(RecordingBackend(events), events)
    machine.fail_frontend_stop = True
    with pytest.raises(RuntimeError, match="frontend stop failed"):
      async with machine:
        pass
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]
    assert not machine.setup_finished

  async def test_both_cleanup_failures_remain_reachable(self) -> None:
    events: list[str] = []
    machine = RecordingMachine(RecordingBackend(events, fail_stop=True), events)
    machine.fail_frontend_stop = True
    with pytest.raises(RuntimeError) as exc_info:
      async with machine:
        pass
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]
    assert _reachable_failures(exc_info.value) == [
      "backend stop failed",
      "frontend stop failed",
    ]

  async def test_body_exception_is_forwarded_to_native_backend_exit(self) -> None:
    events: list[str] = []
    backend = NativeRecordingBackend(events)
    machine = RecordingMachine(backend, events)
    with pytest.raises(ValueError, match="body failed"):
      async with machine:
        raise ValueError("body failed")
    assert isinstance(backend.exit_exception, ValueError)
    assert events == ["backend.setup", "frontend.stop", "backend.stop"]

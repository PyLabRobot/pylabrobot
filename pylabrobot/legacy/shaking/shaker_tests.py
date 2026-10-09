import unittest
from typing import Any, Optional

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.shaking import Shaker, ShakerChatterboxBackend
from pylabrobot.legacy.shaking import shaker as shaker_module
from pylabrobot.legacy.shaking.backend import ShakerBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.testing import AnyioTestBase


class RecordingShakerBackend(ShakerBackend):
  def __init__(self, *, supports_locking: bool = True) -> None:
    super().__init__()
    self.events: list[str] = []
    self._supports_locking = supports_locking
    self.start_error: Optional[BaseException] = None
    self.stop_error: Optional[BaseException] = None
    self.lock_error: Optional[BaseException] = None
    self.unlock_error: Optional[BaseException] = None
    self.start_kwargs: list[dict[str, Any]] = []
    self.start_entered: Optional[anyio.Event] = None
    self.allow_start: Optional[anyio.Event] = None
    self.backend_stopped: Optional[anyio.Event] = None

  async def _record_stop(self) -> None:
    self.events.append("backend_stop")
    if self.backend_stopped is not None:
      self.backend_stopped.set()

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    if backend_kwargs:
      raise TypeError("RecordingShakerBackend does not accept setup options")
    self.events.append("setup")
    stack.push_shielded_async_callback(self._record_stop)

  async def start_shaking(self, speed: float, **backend_kwargs: Any) -> None:  # type: ignore[override]
    self.events.append(f"start:{speed}")
    self.start_kwargs.append(backend_kwargs)
    if self.start_entered is not None:
      self.start_entered.set()
    if self.allow_start is not None:
      await self.allow_start.wait()
    if self.start_error is not None:
      raise self.start_error

  async def stop_shaking(self, **backend_kwargs: Any) -> None:  # type: ignore[override]
    self.events.append("stop")
    if self.stop_error is not None:
      raise self.stop_error

  @property
  def supports_locking(self) -> bool:
    return self._supports_locking

  async def lock_plate(self, **backend_kwargs: Any) -> None:  # type: ignore[override]
    self.events.append("lock")
    if self.lock_error is not None:
      raise self.lock_error

  async def unlock_plate(self, **backend_kwargs: Any) -> None:  # type: ignore[override]
    self.events.append("unlock")
    if self.unlock_error is not None:
      raise self.unlock_error


@pytest.mark.parametrize("backend_type", [RecordingShakerBackend])
def test_backend_double_inherits_managed_methods(
  backend_type: type[HasLifespan],
) -> None:
  assert backend_type.setup is HasLifespan.setup
  assert backend_type.stop is HasLifespan.stop


def make_shaker(backend: ShakerBackend) -> Shaker:
  return Shaker(
    name="test_shaker",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


def exception_is_reachable(start: BaseException, target_type: type[BaseException]) -> bool:
  seen: set[int] = set()
  current: Optional[BaseException] = start
  while current is not None and id(current) not in seen:
    if isinstance(current, target_type):
      return True
    seen.add(id(current))
    current = current.__context__
  return False


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


class TestShakerEvents(AnyioTestBase):
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
      async with shaker:
        await shaker.shake(speed=900, duration=0)
        await shaker.stop_shaking()

    shaker_events = [event for event in events if event.name.startswith("shaker.")]
    assert [event.name for event in shaker_events] == [
      "shaker.shake.started",
      "shaker.shake.completed",
      "shaker.stop_shaking.started",
      "shaker.stop_shaking.completed",
      "shaker.stop_shaking.started",
      "shaker.stop_shaking.completed",
    ]
    assert shaker_events[0].context["device"]["name"] == "test_shaker"
    assert shaker_events[0].context["speed_rpm"] == 900.0
    assert shaker_events[0].context["duration"] == 0.0
    assert "duration_seconds" not in shaker_events[0].context

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
      async with shaker:
        await shaker.shake(speed=900, duration=0)

    shake_started = next(event for event in events if event.name == "shaker.shake.started")
    assert shake_started.context["resources"][0]["name"] == "plate"


class TestShakingScope(AnyioTestBase):
  async def test_shaking_scope_orders_lock_start_stop_unlock(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)

    async with shaker:
      async with shaker.shaking(1200, acceleration=3):
        backend.events.append("body")

    assert backend.events == [
      "setup",
      "lock",
      "start:1200",
      "body",
      "stop",
      "unlock",
      "backend_stop",
    ]
    assert backend.start_kwargs == [{"acceleration": 3}]

  async def test_shaking_scope_body_failure_stops_and_unlocks(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)

    async with shaker:
      with pytest.raises(ValueError, match="body failed"):
        async with shaker.shaking(900):
          raise ValueError("body failed")

    assert backend.events == ["setup", "lock", "start:900", "stop", "unlock", "backend_stop"]

  async def test_shaking_scope_cancellation_completes_stop_and_unlock(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    entered = anyio.Event()
    exited = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def run_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with shaker.shaking(900):
            entered.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          backend.events.append("cancelled")
          raise
      exited.set()

    async with shaker:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_scope)
        await entered.wait()
        scopes[0].cancel()
        await exited.wait()

    assert backend.events.index("stop") < backend.events.index("unlock")
    assert backend.events.index("unlock") < backend.events.index("cancelled")

  async def test_shaking_scope_start_failure_unlocks(self) -> None:
    backend = RecordingShakerBackend()
    backend.start_error = RuntimeError("start failed")
    shaker = make_shaker(backend)

    async with shaker:
      with pytest.raises(RuntimeError, match="start failed"):
        async with shaker.shaking(900):
          pytest.fail("failed entry must not reach the body")

    assert backend.events == ["setup", "lock", "start:900", "stop", "unlock", "backend_stop"]

    backend = RecordingShakerBackend()
    backend.lock_error = RuntimeError("lock failed")
    shaker = make_shaker(backend)
    async with shaker:
      with pytest.raises(RuntimeError, match="lock failed"):
        async with shaker.shaking(900):
          pytest.fail("failed entry must not reach the body")
    assert backend.events == ["setup", "lock", "unlock", "backend_stop"]

  async def test_parent_teardown_stops_and_unlocks_live_scope_before_backend_close(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    lifespan = shaker.lifespan()
    await lifespan.__aenter__()
    scope = shaker.shaking(900)
    await scope.__aenter__()

    await lifespan.__aexit__(None, None, None)
    assert backend.events == ["setup", "lock", "start:900", "stop", "unlock", "backend_stop"]
    await scope.__aexit__(None, None, None)
    assert backend.events.count("stop") == 1
    assert backend.events.count("unlock") == 1

  async def test_parent_failed_stop_retains_unknown_state_for_next_lifespan(self) -> None:
    backend = RecordingShakerBackend()
    backend.stop_error = RuntimeError("stop failed")
    shaker = make_shaker(backend)
    with pytest.raises(RuntimeError, match="stop failed"):
      async with shaker:
        await shaker.shake(900)

    assert shaker._shaking_state_unknown
    backend.stop_error = None
    async with shaker:
      with pytest.raises(RuntimeError, match="already active"):
        async with shaker.shaking(800):
          pytest.fail("unknown state adopted")
    assert backend.events.count("stop") == 2

  async def test_parent_failed_stop_still_unlocks_live_scope_and_retains_unknown(self) -> None:
    backend = RecordingShakerBackend()
    backend.stop_error = RuntimeError("stop failed")
    shaker = make_shaker(backend)
    lifespan = shaker.lifespan()
    await lifespan.__aenter__()
    scope = shaker.shaking(900)
    await scope.__aenter__()
    with pytest.raises(RuntimeError, match="stop failed"):
      await lifespan.__aexit__(None, None, None)
    assert backend.events == ["setup", "lock", "start:900", "stop", "unlock", "backend_stop"]
    assert shaker._shaking_state_unknown
    await scope.__aexit__(None, None, None)
    assert backend.events.count("stop") == 1

  async def test_failed_scope_unlock_is_retried_at_parent_teardown(self) -> None:
    backend = RecordingShakerBackend()
    backend.unlock_error = RuntimeError("unlock failed")
    shaker = make_shaker(backend)

    async with shaker:
      with pytest.raises(RuntimeError, match="unlock failed"):
        async with shaker.shaking(900):
          pass
      backend.unlock_error = None

    assert backend.events == [
      "setup",
      "lock",
      "start:900",
      "stop",
      "unlock",
      "unlock",
      "backend_stop",
    ]

  async def test_stalled_scope_stop_times_out_then_unlocks_and_preserves_body_error(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(shaker_module, "_SHAKER_CLEANUP_TIMEOUT", 0.02, raising=False)
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    body_error = ValueError("body failed")

    async def slow_stop() -> None:
      backend.events.append("stop")
      await anyio.sleep(0.1)

    backend.stop_shaking = slow_stop  # type: ignore[method-assign]
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError) as error_info:
        async with shaker:
          async with shaker.shaking(900):
            raise body_error

    assert exception_is_reachable(error_info.value, ValueError)
    assert shaker._shaking_state_unknown
    assert backend.events[:5] == ["setup", "lock", "start:900", "stop", "unlock"]
    assert backend.events[-1] == "backend_stop"

  async def test_stalled_parent_stop_times_out_but_attempts_plate_unlock(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(shaker_module, "_SHAKER_CLEANUP_TIMEOUT", 0.02, raising=False)
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)

    async def slow_stop() -> None:
      backend.events.append("stop")
      await anyio.sleep(0.1)

    backend.stop_shaking = slow_stop  # type: ignore[method-assign]
    lifespan = shaker.lifespan()
    await lifespan.__aenter__()
    scope = shaker.shaking(900)
    await scope.__aenter__()

    with anyio.fail_after(1):
      with pytest.raises(TimeoutError):
        await lifespan.__aexit__(None, None, None)
    await scope.__aexit__(None, None, None)
    assert backend.events == ["setup", "lock", "start:900", "stop", "unlock", "backend_stop"]
    assert shaker._shaking_state_unknown

  async def test_stalled_scope_unlock_times_out_and_parent_retries(
    self,
    monkeypatch: pytest.MonkeyPatch,
  ) -> None:
    monkeypatch.setattr(shaker_module, "_SHAKER_CLEANUP_TIMEOUT", 0.02)
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    attempts = 0

    async def unlock() -> None:
      nonlocal attempts
      attempts += 1
      backend.events.append("unlock")
      if attempts == 1:
        await anyio.sleep(0.1)

    backend.unlock_plate = unlock  # type: ignore[method-assign]
    async with shaker:
      with anyio.fail_after(1):
        with pytest.raises(TimeoutError):
          async with shaker.shaking(900):
            pass
      assert shaker._shaking_plate_unlock_owed

    assert backend.events == [
      "setup",
      "lock",
      "start:900",
      "stop",
      "unlock",
      "unlock",
      "backend_stop",
    ]

  async def test_shaking_scope_failed_start_rollback_retains_unknown_state(self) -> None:
    start_error = RuntimeError("start failed after mutation")
    rollback_error = RuntimeError("rollback failed")
    backend = RecordingShakerBackend()
    backend.start_error = start_error
    backend.stop_error = rollback_error
    shaker = make_shaker(backend)

    async with shaker:
      with pytest.raises(RuntimeError, match="rollback failed") as error_info:
        async with shaker.shaking(900):
          pytest.fail("failed entry must not reach the body")

      assert error_info.value is rollback_error
      assert error_info.value.__context__ is start_error
      with pytest.raises(RuntimeError, match="already active"):
        async with shaker.shaking(800):
          pytest.fail("unknown shaking state must not be adopted")
      backend.start_error = None
      backend.stop_error = None

    assert backend.events == [
      "setup",
      "lock",
      "start:900",
      "stop",
      "unlock",
      "stop",
      "backend_stop",
    ]

  async def test_shaking_scope_stop_failure_still_unlocks(self) -> None:
    backend = RecordingShakerBackend()
    backend.stop_error = RuntimeError("stop failed")
    shaker = make_shaker(backend)
    entered = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    failures: list[BaseException] = []

    async def run_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with shaker.shaking(900):
            entered.set()
            await anyio.sleep_forever()
      except BaseException as error:
        failures.append(error)

    async with shaker:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_scope)
        await entered.wait()
        scopes[0].cancel()

      assert len(failures) == 1
      assert isinstance(failures[0], RuntimeError)
      assert str(failures[0]) == "stop failed"
      assert exception_is_reachable(failures[0], anyio.get_cancelled_exc_class())
      backend.stop_error = None

    assert backend.events[:5] == ["setup", "lock", "start:900", "stop", "unlock"]
    assert backend.events[-2:] == ["stop", "backend_stop"]

  async def test_shaking_scope_rejects_nested_and_concurrent_use(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    concurrent_failures: list[RuntimeError] = []

    async def enter_concurrently() -> None:
      try:
        async with shaker.shaking(800):
          pytest.fail("concurrent scope must not reach the body")
      except RuntimeError as error:
        concurrent_failures.append(error)

    async with shaker:
      async with shaker.shaking(900):
        with pytest.raises(RuntimeError, match="already scoped"):
          async with shaker.shaking(800):
            pytest.fail("nested scope must not reach the body")
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(enter_concurrently)

    assert len(concurrent_failures) == 1
    assert "already scoped" in str(concurrent_failures[0])
    assert backend.events.count("start:900") == 1
    assert "start:800" not in backend.events

  async def test_shaking_scope_blocks_durable_transitions(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)

    async with shaker:
      async with shaker.shaking(900):
        with pytest.raises(RuntimeError, match="scope"):
          await shaker.shake(800)
        with pytest.raises(RuntimeError, match="scope"):
          await shaker.stop_shaking()

    assert backend.events.count("start:900") == 1
    assert "start:800" not in backend.events
    assert backend.events.count("stop") == 1

  async def test_shaking_scope_rejects_durable_and_unknown_state(self) -> None:
    durable_backend = RecordingShakerBackend()
    durable_shaker = make_shaker(durable_backend)
    async with durable_shaker:
      await durable_shaker.shake(900)
      with pytest.raises(RuntimeError, match="already active"):
        async with durable_shaker.shaking(800):
          pytest.fail("durable shaking must not be adopted")
      await durable_shaker.stop_shaking()
      await durable_shaker.unlock_plate()

    unknown_backend = RecordingShakerBackend()
    unknown_backend.start_error = RuntimeError("durable start failed")
    unknown_shaker = make_shaker(unknown_backend)
    async with unknown_shaker:
      with pytest.raises(RuntimeError, match="durable start failed"):
        await unknown_shaker.shake(900)
      with pytest.raises(RuntimeError, match="already active"):
        async with unknown_shaker.shaking(800):
          pytest.fail("unknown shaking state must not be adopted")
      unknown_backend.start_error = None

    assert "start:800" not in durable_backend.events
    assert "start:800" not in unknown_backend.events

  async def test_shaking_scope_blocks_manual_unlock(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)

    async with shaker:
      async with shaker.shaking(900):
        with pytest.raises(RuntimeError, match="scope"):
          await shaker.unlock_plate()

    assert backend.events.count("unlock") == 1

  async def test_cancelled_scope_waits_for_transition_lock_before_cleanup(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    entered = anyio.Event()
    exited = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def run_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with shaker.shaking(900):
          entered.set()
          await anyio.sleep_forever()
      exited.set()

    async with shaker:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_scope)
        await entered.wait()
        await shaker._shaking_transition_lock.acquire()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert "stop" not in backend.events
        assert not exited.is_set()
        shaker._shaking_transition_lock.release()
        await exited.wait()

    assert backend.events.index("stop") < backend.events.index("unlock")

  async def test_shaking_scope_cancellation_while_waiting_for_entry_does_no_io(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    started = anyio.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def enter_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        started.set()
        async with shaker.shaking(900):
          pytest.fail("cancelled entry must not reach the body")
      finished.set()

    async with shaker:
      await shaker._shaking_transition_lock.acquire()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_scope)
        await started.wait()
        await anyio.sleep(0)
        scopes[0].cancel()
        await finished.wait()
      shaker._shaking_transition_lock.release()

    assert backend.events == ["setup", "backend_stop"]

  async def test_shaking_scope_cancellation_at_final_checkpoint_does_no_io(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    checkpoint_reached = anyio.Event()
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    entry_task_id: Optional[int] = None
    original_checkpoint = anyio.lowlevel.checkpoint

    async def pause_entry_checkpoint() -> None:
      if anyio.get_current_task().id != entry_task_id:
        await original_checkpoint()
        return
      owner = shaker._shaking_transition_lock.statistics().owner
      assert owner is not None and owner.id == entry_task_id
      assert backend.events == ["setup"]
      checkpoint_reached.set()
      await anyio.sleep_forever()

    monkeypatch.setattr(shaker_module.anyio.lowlevel, "checkpoint", pause_entry_checkpoint)

    async def enter_scope() -> None:
      nonlocal entry_task_id
      entry_task_id = anyio.get_current_task().id
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with shaker.shaking(900):
          pytest.fail("cancelled entry must not reach the body")
      finished.set()

    async with shaker:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_scope)
        with anyio.fail_after(1):
          await checkpoint_reached.wait()
        scopes[0].cancel()
        await finished.wait()

    assert backend.events == ["setup", "backend_stop"]

  async def test_lifespan_stop_waits_for_start_transition_before_state_decision(self) -> None:
    backend = RecordingShakerBackend()
    backend.start_entered = anyio.Event()
    backend.allow_start = anyio.Event()
    backend.backend_stopped = anyio.Event()
    shaker = make_shaker(backend)
    lifespan = shaker.lifespan()
    await lifespan.__aenter__()

    async def exit_lifespan() -> None:
      await lifespan.__aexit__(None, None, None)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(shaker.shake, 900)
      await backend.start_entered.wait()
      task_group.start_soon(exit_lifespan)
      closed_early = False
      with anyio.move_on_after(0.05):
        await backend.backend_stopped.wait()
        closed_early = True
      backend.allow_start.set()

    assert not closed_early, "backend closed before the in-flight start transition was reconciled"
    assert backend.events == ["setup", "lock", "start:900", "stop", "backend_stop"]

  async def test_indefinite_shake_remains_durable(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)

    async with shaker:
      await shaker.shake(900)
      assert backend.events == ["setup", "lock", "start:900"]
      await shaker.stop_shaking()
      await shaker.unlock_plate()

    assert backend.events == ["setup", "lock", "start:900", "stop", "unlock", "backend_stop"]

  async def test_timed_shake_uses_scoped_cleanup(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)

    async with shaker:
      await shaker.shake(900, duration=0)

    assert backend.events == [
      "setup",
      "lock",
      "start:900",
      "stop",
      "unlock",
      "backend_stop",
    ]

  async def test_shaking_scope_preserves_transition_events(self) -> None:
    backend = RecordingShakerBackend()
    shaker = make_shaker(backend)
    plate = Resource("plate", size_x=1, size_y=1, size_z=1)
    shaker.assign_child_resource(plate)
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      async with shaker:
        async with shaker.shaking(900):
          pass

    transition_events = [event for event in events if event.name.startswith("shaker.")]
    assert [event.name for event in transition_events] == [
      "shaker.shake.started",
      "shaker.shake.completed",
      "shaker.stop_shaking.started",
      "shaker.stop_shaking.completed",
    ]
    assert transition_events[0].context["speed_rpm"] == 900.0
    assert "duration" not in transition_events[0].context
    assert transition_events[0].context["resources"][0]["name"] == "plate"
    assert transition_events[2].context["resources"][0]["name"] == "plate"

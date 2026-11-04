import sys
import threading
from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.scales.mettler_toledo_backend import (
  MettlerToledoWXS205SDUBackend,
)
from pylabrobot.testing.concurrency import AnyioTestBase

LineEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]
WriteEffect = Union[None, BaseException, Callable[[], Awaitable[None]]]


class FakeSerial(HasLifespan):
  """Replay serial responses and record lifecycle and wire events without hardware."""

  def __init__(
    self,
    lines: Optional[List[LineEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    write_effects: Optional[List[WriteEffect]] = None,
  ) -> None:
    super().__init__()
    self.lines = list(lines or [])
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.write_effects = list(write_effects or [])
    self.writes: List[bytes] = []
    self.port = "FAKE"

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerial does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes) -> None:
    self.writes.append(data)
    self.events.append(f"write:{data!r}")
    if self.write_effects:
      effect = self.write_effects.pop(0)
      if isinstance(effect, BaseException):
        raise effect
      if effect is not None:
        await effect()

  async def readline(self) -> bytes:
    if not self.lines:
      await anyio.lowlevel.checkpoint()
      return b""
    effect = self.lines.pop(0)
    if isinstance(effect, BaseException):
      raise effect
    line = await effect() if callable(effect) else effect
    self.events.append(f"read:{line!r}")
    return line


def make_backend(
  lines: Optional[List[LineEffect]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  write_effects: Optional[List[WriteEffect]] = None,
) -> tuple[MettlerToledoWXS205SDUBackend, FakeSerial]:
  """Build a legacy backend using a deterministic software serial transport."""
  backend = MettlerToledoWXS205SDUBackend(port="FAKE")
  fake = FakeSerial(lines, events, entry_error, exit_error, write_effects)
  backend.io = cast(Serial, fake)
  return backend, fake


class CancelAfterAcquireLock:
  """Cancel synchronously after immediate acquisition, without an implicit checkpoint."""

  def __init__(self, scope: anyio.CancelScope, events: List[str]) -> None:
    self.scope = scope
    self.events = events

  async def acquire(self) -> None:
    self.events.append("lock.acquired")
    self.scope.cancel()

  def release(self) -> None:
    self.events.append("lock.released")


class ReleaseTrackingLock:
  """Expose synchronous release and waiter eligibility around cancellation delivery."""

  def __init__(self, events: List[str]) -> None:
    self.lock = anyio.Lock()
    self.events = events
    self.waiter_waiting = anyio.Event()
    self.acquire_count = 0
    self.release_count = 0

  async def acquire(self) -> None:
    self.acquire_count += 1
    acquisition = self.acquire_count
    if acquisition == 2:
      self.events.append("waiter.waiting")
      self.waiter_waiting.set()
    await self.lock.acquire()
    if acquisition == 2:
      self.events.append("waiter.acquired")

  def release(self) -> None:
    self.release_count += 1
    self.events.append("lock.released")
    self.lock.release()
    if self.release_count == 1:
      self.events.append("waiter.unblocked")
    if sys.exc_info()[1] is not None:
      raise AssertionError("command lock released while cancellation was propagating")


class RecordingLock:
  """Record command-lock release without imposing scheduler ordering."""

  def __init__(self, events: List[str]) -> None:
    self.lock = anyio.Lock()
    self.events = events

  async def acquire(self) -> None:
    await self.lock.acquire()

  def release(self) -> None:
    self.events.append("command_lock.released")
    self.lock.release()


class ThreadedSerial:
  """Model Serial's operation lock and non-abandoning worker dispatch."""

  def __init__(self, events: List[str], gate_first_write: bool = False) -> None:
    self.events = events
    self.gate_first_write = gate_first_write
    self.operation_lock = anyio.Lock()
    self.worker_limiter = anyio.CapacityLimiter(1)
    self.write_waiting = anyio.Event()
    self.worker_started = threading.Event()
    self.release_worker = threading.Event()
    self.holder_started = threading.Event()
    self.release_holder = threading.Event()
    self.state_lock = threading.Lock()
    self.writes: List[bytes] = []
    self.responses: List[bytes] = []
    self.read_calls = 0
    self.port = "FAKE"

  async def write(self, data: bytes) -> None:
    async with self.operation_lock:
      self.write_waiting.set()
      await anyio.to_thread.run_sync(self._write_sync, data, limiter=self.worker_limiter)

  def _write_sync(self, data: bytes) -> None:
    self.worker_started.set()
    self.events.append(f"worker.started:{data!r}")
    if self.gate_first_write:
      self.gate_first_write = False
      if not self.release_worker.wait(2):
        raise RuntimeError("test write worker was not released")
    with self.state_lock:
      self.writes.append(data)
      response = b"S S 1.000 g\r\n" if data == b"S\r\n" else b"SI S 2.000 g\r\n"
      self.responses.append(response)
    self.events.append(f"worker.completed:{data!r}")

  async def readline(self) -> bytes:
    async with self.operation_lock:
      self.read_calls += 1
      with self.state_lock:
        response = self.responses.pop(0) if self.responses else b""
      self.events.append(f"read:{response!r}")
      return response

  async def hold_worker_limiter(self) -> None:
    await anyio.to_thread.run_sync(self._hold_worker, limiter=self.worker_limiter)

  def _hold_worker(self) -> None:
    self.holder_started.set()
    if not self.release_holder.wait(2):
      raise RuntimeError("test worker-limiter holder was not released")


class TestMettlerToledoLifecycle(AnyioTestBase):
  def test_inherits_managed_methods(self) -> None:
    assert MettlerToledoWXS205SDUBackend.__aenter__ is HasLifespan.__aenter__
    assert MettlerToledoWXS205SDUBackend.__aexit__ is HasLifespan.__aexit__
    assert MettlerToledoWXS205SDUBackend.setup is HasLifespan.setup
    assert MettlerToledoWXS205SDUBackend.stop is HasLifespan.stop

  async def test_initialization_order_and_serial_number(self) -> None:
    events: List[str] = []
    backend, fake = make_backend(events=events)
    backend.serial_number = "old"

    async def unit_reply() -> bytes:
      assert fake.writes == [b"M21 0 0\r\n"]
      return b"M21 A\r\n"

    async def serial_reply() -> bytes:
      assert fake.writes == [b"M21 0 0\r\n", b"I4\r\n"]
      assert backend.serial_number == "old"
      return b'I4 A "SN-205"\r\n'

    fake.lines.extend([unit_reply, serial_reply])
    async with backend:
      assert backend.serial_number == "SN-205"

    assert fake.writes == [b"M21 0 0\r\n", b"I4\r\n"]
    assert events[0] == "serial.enter"
    assert events[-1] == "serial.exit"

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    backend, _ = make_backend(events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed entry reached body")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not backend.is_active

  async def test_unit_configuration_failure_closes_serial(self) -> None:
    events: List[str] = []
    failure = RuntimeError("unit configuration failed")
    backend, fake = make_backend([failure], events)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed configuration reached body")

    assert error_info.value is failure
    assert fake.writes == [b"M21 0 0\r\n"]
    assert events[-1] == "serial.exit"
    assert not backend.is_active

  async def test_serial_number_failure_closes_serial(self) -> None:
    events: List[str] = []
    request_failure = RuntimeError("serial number failed")
    close_failure = RuntimeError("serial close failed")
    backend, fake = make_backend([b"M21 A\r\n", request_failure], events, exit_error=close_failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed serial request reached body")

    assert error_info.value is close_failure
    assert error_info.value.__context__ is request_failure
    assert fake.writes == [b"M21 0 0\r\n", b"I4\r\n"]
    assert events[-1] == "serial.exit"
    assert not backend.is_active

  @pytest.mark.parametrize("cancelled_command", [b"M21 0 0\r\n", b"I4\r\n"])
  async def test_cancelled_initialization_reconciles_before_close(
    self, cancelled_command: bytes
  ) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    release_reply = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def blocked_reply() -> bytes:
      read_started.set()
      await release_reply.wait()
      events.append("reply.released")
      return b"M21 A\r\n" if cancelled_command == b"M21 0 0\r\n" else b'I4 A "SN"\r\n'

    lines: List[LineEffect] = [blocked_reply]
    if cancelled_command == b"I4\r\n":
      lines.insert(0, b"M21 A\r\n")
    backend, fake = make_backend(lines, events)

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with backend:
            pytest.fail("cancelled initialization reached body")
        except anyio.get_cancelled_exc_class():
          events.append("entry.cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await read_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "entry.cancelled" not in events
      release_reply.set()

    assert fake.writes[-1] == cancelled_command
    assert events.index("reply.released") < events.index("serial.exit")
    assert events.index("serial.exit") < events.index("entry.cancelled")
    assert not backend.is_active

  async def test_normal_and_cancelled_body_close_serial(self) -> None:
    normal_events: List[str] = []
    normal, _ = make_backend([b"M21 A\r\n", b'I4 A "SN"\r\n'], normal_events)
    async with normal:
      normal_events.append("body")
    assert normal_events[-2:] == ["body", "serial.exit"]

    cancelled_events: List[str] = []
    body_started = anyio.Event()
    cancelled, _ = make_backend([b"M21 A\r\n", b'I4 A "SN"\r\n'], cancelled_events)

    async def use_backend() -> None:
      try:
        async with cancelled:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        cancelled_events.append("body.cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert cancelled_events[-2:] == ["serial.exit", "body.cancelled"]
    assert not normal.is_active
    assert not cancelled.is_active

  async def test_sequential_reuse_reinitializes(self) -> None:
    events: List[str] = []
    backend, fake = make_backend(
      [b"M21 A\r\n", b'I4 A "SN-1"\r\n', b"M21 A\r\n", b'I4 A "SN-2"\r\n'],
      events,
    )

    async with backend:
      assert backend.serial_number == "SN-1"
    async with backend:
      assert backend.serial_number == "SN-2"

    assert fake.writes == [b"M21 0 0\r\n", b"I4\r\n", b"M21 0 0\r\n", b"I4\r\n"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_rejects_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    backend, _ = make_backend(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached body")

    assert events == []


class TestMettlerToledoTransactions(AnyioTestBase):
  async def test_concurrent_commands_do_not_cross_responses(self) -> None:
    events: List[str] = []
    first_read_started = anyio.Event()
    release_first_reply = anyio.Event()

    async def first_reply() -> bytes:
      first_read_started.set()
      await release_first_reply.wait()
      return b"S S 1.000 g\r\n"

    backend, fake = make_backend([first_reply, b"SI S 2.000 g\r\n"], events)
    responses: List[List[str]] = []

    async def command(command: str) -> None:
      responses.append(await backend.send_command(command))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(command, "S")
      await first_read_started.wait()
      task_group.start_soon(command, "SI")
      await anyio.sleep(0)
      assert fake.writes == [b"S\r\n"]
      release_first_reply.set()

    assert responses == [["S", "S", "1.000", "g"], ["SI", "S", "2.000", "g"]]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]
    assert events.index("read:b'S S 1.000 g\\r\\n'") < events.index("write:b'SI\\r\\n'")

  async def test_cancel_before_write_writes_nothing(self) -> None:
    read_started = anyio.Event()
    release_reply = anyio.Event()
    waiter_started = anyio.Event()
    waiter_scope: List[anyio.CancelScope] = []

    async def first_reply() -> bytes:
      read_started.set()
      await release_reply.wait()
      return b"S S 1.000 g\r\n"

    backend, fake = make_backend([first_reply])

    async def waiting_command() -> None:
      with anyio.CancelScope() as scope:
        waiter_scope.append(scope)
        waiter_started.set()
        await backend.send_command("SI")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(backend.send_command, "S")
      await read_started.wait()
      task_group.start_soon(waiting_command)
      await waiter_started.wait()
      await anyio.sleep(0)
      waiter_scope[0].cancel()
      await anyio.sleep(0)
      assert fake.writes == [b"S\r\n"]
      release_reply.set()

    assert fake.writes == [b"S\r\n"]

  async def test_immediate_lock_cancellation_stops_at_explicit_prewrite_checkpoint(self) -> None:
    events: List[str] = []
    backend, fake = make_backend([b"S S 1.000 g\r\n"], events)

    with anyio.CancelScope() as scope:
      lock = CancelAfterAcquireLock(scope, events)
      backend._command_lock = cast(anyio.Lock, lock)
      try:
        await backend.send_command("S")
      except anyio.get_cancelled_exc_class():
        events.append("command.cancelled")

    assert fake.writes == []
    assert events == ["lock.acquired", "lock.released", "command.cancelled"]

  async def test_cancel_after_write_consumes_response(self) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    release_reply = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def blocked_reply() -> bytes:
      read_started.set()
      await release_reply.wait()
      events.append("reply.released")
      return b"S S 1.000 g\r\n"

    backend, fake = make_backend([blocked_reply, b"SI S 2.000 g\r\n"], events)

    async def cancelled_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend.send_command("S")
        except anyio.get_cancelled_exc_class():
          events.append("command.cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(cancelled_command)
      await read_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "command.cancelled" not in events
      release_reply.set()

    assert events.index("reply.released") < events.index("command.cancelled")
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]

  async def test_cancelled_transaction_releases_lock_before_propagating(self) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    release_reply = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def first_reply() -> bytes:
      read_started.set()
      await release_reply.wait()
      events.append("response.complete")
      return b"S S 1.000 g\r\n"

    backend, fake = make_backend([first_reply, b"SI S 2.000 g\r\n"], events)
    lock = ReleaseTrackingLock(events)
    backend._command_lock = cast(anyio.Lock, lock)
    competitor_responses: List[List[str]] = []

    async def cancelled_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend.send_command("S")
        except anyio.get_cancelled_exc_class():
          events.append("command.cancelled")

    async def competitor() -> None:
      competitor_responses.append(await backend.send_command("SI"))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(cancelled_command)
      await read_started.wait()
      task_group.start_soon(competitor)
      await lock.waiter_waiting.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "command.cancelled" not in events
      release_reply.set()

    assert events.index("response.complete") < events.index("lock.released")
    assert events.index("lock.released") < events.index("waiter.unblocked")
    assert events.index("waiter.unblocked") < events.index("command.cancelled")
    assert competitor_responses == [["SI", "S", "2.000", "g"]]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]

  async def test_transport_timeout_identity_and_reuse(self) -> None:
    failure = TimeoutError("serial transport timeout")
    backend, fake = make_backend([failure, b"SI S 2.000 g\r\n"])

    with pytest.raises(TimeoutError) as error_info:
      await backend.send_command("S")

    assert error_info.value is failure
    assert str(error_info.value) == "serial transport timeout"
    assert error_info.value.__context__ is None
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]

  async def test_write_deadline_covers_committed_write_and_allows_reuse(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    command_finished = anyio.Event()
    errors: List[TimeoutError] = []

    async def stalled_write() -> None:
      events.append("write.started")
      write_started.set()
      await release_write.wait()
      events.append("write.completed")

    backend, fake = make_backend([b"SI S 2.000 g\r\n"], events, write_effects=[stalled_write, None])

    async def timed_command() -> None:
      try:
        await backend.send_command("S", timeout=0)
      except TimeoutError as error:
        errors.append(error)
      finally:
        command_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(timed_command)
      await write_started.wait()
      with anyio.move_on_after(0.1):
        await command_finished.wait()
      finished_before_release = command_finished.is_set()
      release_write.set()

    assert finished_before_release
    assert "write.completed" not in events
    assert [str(error) for error in errors] == ["Timeout while waiting for response from scale."]
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]

  async def test_deadline_during_nonabandoning_write_reconciles_before_reuse(self) -> None:
    events: List[str] = []
    fake = ThreadedSerial(events, gate_first_write=True)
    backend = MettlerToledoWXS205SDUBackend(port="FAKE")
    backend.io = cast(Serial, fake)
    backend._command_lock = cast(anyio.Lock, RecordingLock(events))
    errors: List[TimeoutError] = []

    async def timed_command() -> None:
      try:
        await backend.send_command("S", timeout=cast(int, 0.05))
      except TimeoutError as error:
        events.append("operation.timeout")
        errors.append(error)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(timed_command)
      await anyio.to_thread.run_sync(fake.worker_started.wait)
      await anyio.sleep(0.1)
      fake.release_worker.set()

    assert [str(error) for error in errors] == ["Timeout while waiting for response from scale."]
    assert events.index("worker.completed:b'S\\r\\n'") < events.index("read:b'S S 1.000 g\\r\\n'")
    assert events.index("read:b'S S 1.000 g\\r\\n'") < events.index("command_lock.released")
    assert events.index("command_lock.released") < events.index("operation.timeout")
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]
    assert fake.responses == []

  async def test_deadline_before_worker_dispatch_does_not_read_response(self) -> None:
    events: List[str] = []
    fake = ThreadedSerial(events)
    backend = MettlerToledoWXS205SDUBackend(port="FAKE")
    backend.io = cast(Serial, fake)
    errors: List[TimeoutError] = []
    command_finished = anyio.Event()

    async def timed_command() -> None:
      try:
        await backend.send_command("S", timeout=cast(int, 0.05))
      except TimeoutError as error:
        errors.append(error)
      finally:
        command_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(fake.hold_worker_limiter)
      await anyio.to_thread.run_sync(fake.holder_started.wait)
      task_group.start_soon(timed_command)
      await fake.write_waiting.wait()
      with anyio.move_on_after(0.2):
        await command_finished.wait()
      finished_before_release = command_finished.is_set()
      fake.release_holder.set()

    assert finished_before_release
    assert [str(error) for error in errors] == ["Timeout while waiting for response from scale."]
    assert fake.writes == []
    assert fake.read_calls == 0
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"SI\r\n"]
    assert fake.responses == []

  async def test_read_deadline_releases_lock_and_allows_reuse(self) -> None:
    read_started = anyio.Event()

    async def stalled_read() -> bytes:
      read_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend, fake = make_backend([stalled_read, b"S S 1.000 g\r\n", b"SI S 2.000 g\r\n"])

    with pytest.raises(TimeoutError, match="Timeout while waiting for response from scale"):
      await backend.send_command("S", timeout=0)

    assert read_started.is_set()
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]

  async def test_write_exception_identity_and_reuse(self) -> None:
    failure = RuntimeError("write failed")
    backend, fake = make_backend([b"SI S 2.000 g\r\n"], write_effects=[failure, None])

    with pytest.raises(RuntimeError) as error_info:
      await backend.send_command("S")

    assert error_info.value is failure
    assert error_info.value.__context__ is None
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]

  async def test_read_exception_identity_and_reuse(self) -> None:
    failure = RuntimeError("read failed")
    backend, fake = make_backend([failure, b"SI S 2.000 g\r\n"])

    with pytest.raises(RuntimeError) as error_info:
      await backend.send_command("S")

    assert error_info.value is failure
    assert error_info.value.__context__ is None
    assert await backend.send_command("SI") == ["SI", "S", "2.000", "g"]
    assert fake.writes == [b"S\r\n", b"SI\r\n"]

  async def test_timeout_uses_public_error(self) -> None:
    backend, fake = make_backend()

    with pytest.raises(TimeoutError, match="Timeout while waiting for response from scale") as info:
      await backend.send_command("S", timeout=0)

    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert fake.writes == [b"S\r\n"]

  def test_serialization_shape_is_unchanged(self) -> None:
    backend, _ = make_backend()

    assert backend.serialize() == {"type": "MettlerToledoWXS205SDUBackend", "port": "FAKE"}

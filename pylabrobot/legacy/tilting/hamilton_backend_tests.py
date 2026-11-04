from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple, cast

import anyio
import pytest

pytest.importorskip("serial")

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.tilting import hamilton_backend as hamilton_module
from pylabrobot.legacy.tilting.hamilton_backend import (
  HamiltonTiltModuleBackend,
  HamiltonTiltModuleChatterboxBackend,
)
from pylabrobot.legacy.tilting.tilter import Tilter
from pylabrobot.legacy.tilting.tilter_backend import TilterBackend, TiltModuleError
from pylabrobot.resources import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

_GUARD_SECONDS = 2.0
_STALL_SECONDS = 0.5


def _command(data: bytes) -> str:
  return data.decode("ascii")[2:4]


class RecordingSerial(HasLifespan):
  """Typed software Serial double with scripted replies and explicit blocking events."""

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    replies: Optional[Dict[str, List[bytes]]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    entry_error: Optional[BaseException] = None,
    write_timeout: float = 0.2,
    timeout: float = 0.3,
    write_stall: float = 0.0,
    read_stall: float = 0.0,
  ) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.replies: Dict[str, Deque[bytes]] = {
      command: deque(values) for command, values in (replies or {}).items()
    }
    self.failures = failures or {}
    self.entry_error = entry_error
    self.write_timeout = write_timeout
    self.timeout = timeout
    self.write_stall = write_stall
    self.read_stall = read_stall
    self.serial_kwargs: Dict[str, Any] = {}
    self.writes: List[bytes] = []
    self.entries = 0
    self.exits = 0
    self.blocked_event: Optional[str] = None
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingSerial does not accept lifespan options")
    self.entries += 1
    self.events.append("serial.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    self.exits += 1
    self.events.append("serial.exit")
    await anyio.lowlevel.checkpoint()

  async def _block(self, event: str) -> None:
    if self.blocked_event == event:
      self.blocked_started.set()
      await self.release_blocked.wait()

  async def write(self, data: bytes) -> None:
    self.writes.append(data)
    event = f"write:{data!r}"
    self.events.append(event)
    await anyio.lowlevel.checkpoint()
    await self._block("write")
    if self.write_stall:
      await anyio.sleep(self.write_stall)
    failure = self.failures.get(event)
    if failure is not None:
      raise failure

  async def read(self, num_bytes: int = 1) -> bytes:
    self.events.append("read")
    await anyio.lowlevel.checkpoint()
    await self._block("read")
    if self.read_stall:
      await anyio.sleep(self.read_stall)
    command = _command(self.writes[-1])
    failure = self.failures.get(f"read:{command}")
    if failure is not None:
      raise failure
    scripted = self.replies.get(command)
    if scripted:
      return scripted.popleft()
    return f"T1{command}\r\n".encode("ascii")


class RecordingLock:
  """Test wrapper exposing when a second command attempts to acquire the real lock."""

  def __init__(self, lock: anyio.Lock) -> None:
    self._lock = lock
    self.acquire_count = 0
    self.first_acquired = anyio.Event()
    self.waiter_started = anyio.Event()
    self.waiter_acquired = anyio.Event()

  async def acquire(self) -> None:
    self.acquire_count += 1
    acquisition = self.acquire_count
    if acquisition == 2:
      self.waiter_started.set()
    await self._lock.acquire()
    if acquisition == 1:
      self.first_acquired.set()
    elif acquisition == 2:
      self.waiter_acquired.set()

  def release(self) -> None:
    self._lock.release()


def make_backend(
  monkeypatch: pytest.MonkeyPatch,
  *,
  write_timeout: float = 0.2,
  timeout: float = 0.3,
  **double_kwargs: Any,
) -> Tuple[HamiltonTiltModuleBackend, RecordingSerial]:
  io = RecordingSerial(write_timeout=write_timeout, timeout=timeout, **double_kwargs)

  def build_serial(**kwargs: Any) -> Serial:
    io.serial_kwargs = dict(kwargs)
    return cast(Serial, io)

  monkeypatch.setattr(hamilton_module, "Serial", build_serial)
  return (
    HamiltonTiltModuleBackend(com_port="COM_TILT", write_timeout=write_timeout, timeout=timeout),
    io,
  )


def record_command_lock(backend: HamiltonTiltModuleBackend) -> RecordingLock:
  """Wrap the lifespan-local command lock after entry without changing its behavior."""
  lock = RecordingLock(backend._command_lock)
  backend._command_lock = cast(anyio.Lock, lock)
  return lock


class TestHamiltonTiltLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    for backend_type in (HamiltonTiltModuleBackend, HamiltonTiltModuleChatterboxBackend):
      assert backend_type.setup is HasLifespan.setup
      assert backend_type.stop is HasLifespan.stop
      assert backend_type.__aenter__ is HasLifespan.__aenter__
      assert backend_type.__aexit__ is HasLifespan.__aexit__

  async def test_initial_offset_and_initialize_transcript(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend.lifespan(initial_offset=-7):
      assert backend.is_active

    assert io.writes == [
      b"99SO-7\r\n",
      b"99SI\r\n",
      b"99TO\r\n",
      b"99WO\r\n",
      b"99PO\r\n",
    ]
    assert io.events[0] == "serial.enter"
    assert io.events[-1] == "serial.exit"
    assert io.entries == io.exits == 1

  def test_serial_configuration_and_operation_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch, write_timeout=2.5, timeout=3.5)

    assert io.serial_kwargs == {
      "port": "COM_TILT",
      "baudrate": 1200,
      "bytesize": hamilton_module.serial.EIGHTBITS,
      "parity": hamilton_module.serial.PARITY_EVEN,
      "stopbits": hamilton_module.serial.STOPBITS_ONE,
      "write_timeout": 2.5,
      "timeout": 3.5,
      "human_readable_device_name": "Hamilton Tilt Module",
    }
    assert backend._command_timeout == 6.0

  @pytest.mark.parametrize("value", [True, False, 1.5, -101, 101])
  async def test_invalid_initial_offset_fails_before_serial_entry(
    self, monkeypatch: pytest.MonkeyPatch, value: object
  ) -> None:
    backend, io = make_backend(monkeypatch)

    with pytest.raises((TypeError, ValueError)):
      async with backend.lifespan(initial_offset=value):
        pytest.fail("invalid offset entered the lifespan")

    assert io.events == []

  async def test_unknown_option_fails_before_serial_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    with pytest.raises(TypeError, match="unexpected"):
      async with backend.lifespan(initial_offset=0, unexpected=True):
        pytest.fail("unknown option entered the lifespan")

    assert io.events == []

  async def test_offset_failure_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(
      monkeypatch,
      replies={"SO": [b"T1SOer06\r\n"]},
    )

    with pytest.raises(TiltModuleError, match="Parameter out"):
      async with backend.lifespan(initial_offset=4):
        pytest.fail("failed setup yielded")

    assert io.writes == [b"99SO4\r\n", b"99TO\r\n", b"99WO\r\n", b"99PO\r\n"]
    assert io.events[-1] == "serial.exit"

  async def test_initialize_failure_runs_safety_cleanup(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, failures={"read:SI": OSError("read failed")})

    with pytest.raises(OSError, match="read failed"):
      async with backend.lifespan():
        pytest.fail("failed setup yielded")

    assert io.writes == [b"99SO0\r\n", b"99SI\r\n", b"99TO\r\n", b"99WO\r\n", b"99PO\r\n"]
    assert io.events[-1] == "serial.exit"

  async def test_body_cancellation_runs_to_wo_po_before_close(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    entered = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend.lifespan():
          entered.set()
          await anyio.sleep_forever()

    with anyio.move_on_after(_GUARD_SECONDS) as guard:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_backend)
        await entered.wait()
        scopes[0].cancel()
    assert not guard.cancelled_caught
    assert io.writes[-3:] == [b"99TO\r\n", b"99WO\r\n", b"99PO\r\n"]
    assert io.events[-1] == "serial.exit"

  async def test_cleanup_attempts_every_action(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(
      monkeypatch,
      replies={"TO": [b"T1TOer07\r\n"]},
      failures={"read:WO": OSError("waste read failed")},
    )

    with pytest.raises(BaseException):
      async with backend.lifespan():
        pass

    assert io.writes[-3:] == [b"99TO\r\n", b"99WO\r\n", b"99PO\r\n"]
    assert io.events[-1] == "serial.exit"

  async def test_cleanup_callbacks_have_a_finite_overrun_bound(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, write_timeout=0.01, timeout=0.01)
    backend._cleanup_timeout = 0.05
    started = anyio.Event()

    async def never_returns() -> str:
      started.set()
      await anyio.sleep(_STALL_SECONDS)
      return "late"

    monkeypatch.setattr(backend, "tilt_switch_off_temperature_controller", never_returns)

    with anyio.move_on_after(_GUARD_SECONDS) as guard:
      with pytest.raises(TimeoutError):
        async with backend.lifespan():
          pass
    assert not guard.cancelled_caught
    assert started.is_set()
    assert b"99WO\r\n" in io.writes
    assert b"99PO\r\n" in io.writes
    assert io.events[-1] == "serial.exit"

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend.lifespan():
      await backend.tilt_go_to_position(1)
    async with backend.lifespan(initial_offset=2):
      await backend.tilt_go_to_position(2)

    assert io.entries == io.exits == 2
    assert io.writes.count(b"99GP1\r\n") == 1
    assert io.writes.count(b"99GP2\r\n") == 1


class TestHamiltonTiltTransactions(AnyioTestBase):
  async def test_split_firmware_reply_is_assembled_before_matching(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A reply split across serial chunks keeps its command prefix."""
    backend, io = make_backend(monkeypatch, replies={"SI": [b"T", b"1SI\r\n"]})
    assert await backend.send_command("SI") == "T1SI\r\n"
    assert io.events.count("read") == 2

  async def test_command_transactions_do_not_interleave(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend.lifespan():
      lock = record_command_lock(backend)
      baseline = len(io.writes)
      io.blocked_event = "read"
      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(backend.tilt_go_to_position, 3)
          await io.blocked_started.wait()
          assert lock.first_acquired.is_set()
          task_group.start_soon(backend.tilt_set_speed, 4)
          await lock.waiter_started.wait()
          assert io.writes[baseline:] == [b"99GP3\r\n"]
          io.release_blocked.set()
      assert not guard.cancelled_caught
      assert io.writes[baseline:] == [b"99GP3\r\n", b"99SV4\r\n"]

  async def test_competing_command_waits_through_unmatched_replies(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, replies={"GP": [b"noise\r\n", b"T1GP ok\r\n"]})

    async with backend.lifespan():
      lock = record_command_lock(backend)
      baseline = len(io.writes)
      first_read = anyio.Event()
      release = anyio.Event()
      original_read = io.read

      async def controlled_read(num_bytes: int = 1) -> bytes:
        reply = await original_read(num_bytes)
        if reply == b"noise\r\n":
          first_read.set()
          await release.wait()
        return reply

      monkeypatch.setattr(io, "read", controlled_read)
      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(backend.tilt_go_to_position, 5)
          await first_read.wait()
          assert lock.first_acquired.is_set()
          task_group.start_soon(backend.tilt_set_speed, 6)
          await lock.waiter_started.wait()
          assert io.writes[baseline:] == [b"99GP5\r\n"]
          release.set()
      assert not guard.cancelled_caught
      assert io.writes[baseline:] == [b"99GP5\r\n", b"99SV6\r\n"]

  async def test_cancelled_command_consumes_matching_reply(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    scopes: List[anyio.CancelScope] = []
    cancelled = anyio.Event()

    async with backend.lifespan():
      lock = record_command_lock(backend)
      baseline = len(io.writes)
      io.blocked_event = "read"

      async def send() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.tilt_go_to_position(7)
          except anyio.get_cancelled_exc_class():
            io.events.append("cancelled")
            cancelled.set()
            raise

      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send)
          await io.blocked_started.wait()
          task_group.start_soon(backend.tilt_set_speed, 8)
          await lock.waiter_started.wait()
          scopes[0].cancel()
          assert io.writes[baseline:] == [b"99GP7\r\n"]
          io.release_blocked.set()
      assert not guard.cancelled_caught
      assert cancelled.is_set()
      assert io.writes[baseline:] == [b"99GP7\r\n", b"99SV8\r\n"]
      assert io.events.index("write:b'99SV8\\r\\n'") < io.events.index("cancelled")

  async def test_ambiguous_write_failure_reconciles_matching_reply(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    write_event = "write:b'99GP6\\r\\n'"
    backend, io = make_backend(monkeypatch, failures={write_event: OSError("write failed")})

    async with backend.lifespan():
      baseline = len(io.events)
      with pytest.raises(OSError, match="write failed"):
        await backend.tilt_go_to_position(6)
      assert io.events[baseline:] == [write_event, "read"]

  async def test_cancelled_lock_waiter_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    scopes: List[anyio.CancelScope] = []
    waiter_cancelled = anyio.Event()

    async with backend.lifespan():
      lock = record_command_lock(backend)
      baseline = len(io.writes)
      io.blocked_event = "read"

      async def queued_send() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.tilt_set_speed(9)
          except anyio.get_cancelled_exc_class():
            waiter_cancelled.set()
            raise

      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(backend.tilt_go_to_position, 9)
          await io.blocked_started.wait()
          task_group.start_soon(queued_send)
          await lock.waiter_started.wait()
          scopes[0].cancel()
          await waiter_cancelled.wait()
          io.release_blocked.set()
      assert not guard.cancelled_caught
      assert io.writes[baseline:] == [b"99GP9\r\n"]

  async def test_unmatched_reply_deadline_is_finite(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(
      monkeypatch,
      write_timeout=0.02,
      timeout=0.03,
      replies={"GP": [b"unmatched\r\n"] * 100000},
    )

    async with backend.lifespan():
      started = anyio.current_time()
      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        with pytest.raises(TimeoutError):
          await backend.tilt_go_to_position(1)
      elapsed = anyio.current_time() - started
      assert not guard.cancelled_caught
      assert 0.04 <= elapsed < 0.25
      assert len(io.writes) >= 3

  async def test_write_and_read_share_one_operation_budget(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(
      monkeypatch,
      write_timeout=0.02,
      timeout=0.03,
    )

    async with backend.lifespan():
      io.write_stall = 0.03
      io.read_stall = 0.03
      try:
        with anyio.move_on_after(_GUARD_SECONDS) as guard:
          with pytest.raises(TimeoutError):
            await backend.tilt_go_to_position(2)
        assert not guard.cancelled_caught
      finally:
        io.write_stall = 0
        io.read_stall = 0

  async def test_lock_wait_and_exchange_share_one_operation_budget(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, write_timeout=0.1, timeout=0.1)
    outcome: List[str] = []
    finished = anyio.Event()

    async with backend.lifespan():
      lock = record_command_lock(backend)
      io.blocked_event = "read"

      async def queued_send() -> None:
        try:
          await backend.tilt_set_speed(5)
        except TimeoutError:
          outcome.append("timeout")
        else:
          outcome.append("completed")
        finally:
          finished.set()

      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(backend.tilt_go_to_position, 5)
          await io.blocked_started.wait()
          assert lock.first_acquired.is_set()

          backend._command_timeout = 0.05
          task_group.start_soon(queued_send)
          await lock.waiter_started.wait()
          try:
            with anyio.fail_after(0.2):
              await finished.wait()
            assert finished.is_set()
            assert not lock.waiter_acquired.is_set()
          finally:
            io.release_blocked.set()
      assert not guard.cancelled_caught
      assert outcome == ["timeout"]

  async def test_pending_cancellation_beats_stored_read_failure(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, failures={"read:GP": OSError("stored")})
    scopes: List[anyio.CancelScope] = []
    outcome: List[str] = []

    async with backend.lifespan():
      io.blocked_event = "read"

      async def send() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.tilt_go_to_position(3)
          except anyio.get_cancelled_exc_class():
            outcome.append("cancelled")
            raise
          except OSError:
            outcome.append("error")

      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send)
          await io.blocked_started.wait()
          scopes[0].cancel()
          io.release_blocked.set()
      assert not guard.cancelled_caught
      assert outcome == ["cancelled"]

  async def test_pending_cancellation_beats_stored_deadline(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, write_timeout=0.02, timeout=0.03)
    scopes: List[anyio.CancelScope] = []
    outcome: List[str] = []
    finished = anyio.Event()

    async with backend.lifespan():
      io.blocked_event = "read"

      async def send() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.tilt_go_to_position(3)
          except anyio.get_cancelled_exc_class():
            outcome.append("cancelled")
            raise
          except TimeoutError:
            outcome.append("timeout")
          finally:
            finished.set()

      with anyio.move_on_after(_GUARD_SECONDS) as guard:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send)
          await io.blocked_started.wait()
          scopes[0].cancel()
          await finished.wait()
          io.release_blocked.set()
      assert not guard.cancelled_caught
      assert outcome == ["cancelled"]

  async def test_error_mapping_and_exact_bytes_are_preserved(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(
      monkeypatch,
      replies={"SV": [b"T1SVer02\r\n"], "GP": [b"T1GPer09\r\n"]},
    )

    async with backend.lifespan():
      with pytest.raises(TiltModuleError, match=r"\*\*Step\*\* loss"):
        await backend.tilt_set_speed(4)
      with pytest.raises(RuntimeError, match="Unexpected error code: 9"):
        await backend.tilt_go_to_position(4)

    assert b"99SV4\r\n" in io.writes
    assert b"99GP4\r\n" in io.writes


class TestHamiltonTiltChatterbox(AnyioTestBase):
  async def test_chatterbox_preserves_option_output(
    self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend, io = make_backend(monkeypatch)
    chatterbox = HamiltonTiltModuleChatterboxBackend(com_port="SOFTWARE")

    async with chatterbox.lifespan(initial_offset=-9):
      await chatterbox.send_command("GP", "3")

    assert capsys.readouterr().out == (
      "[tilter] setup initial offset -9\n"
      "[tilter] Sending command: GP with parameter: 3\n"
      "[tilter] stopping\n"
    )
    assert io.entries == io.exits == 0
    assert io.writes == []

  @pytest.mark.parametrize(
    "options",
    [
      {"initial_offset": True},
      {"initial_offset": 1.5},
      {"initial_offset": -101},
      {"initial_offset": 101},
      {"other": 1},
    ],
  )
  async def test_chatterbox_rejects_options_before_output(
    self,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: Dict[str, object],
  ) -> None:
    make_backend(monkeypatch)
    chatterbox = HamiltonTiltModuleChatterboxBackend(com_port="SOFTWARE")

    with pytest.raises((TypeError, ValueError)):
      async with chatterbox.lifespan(**options):
        pytest.fail("invalid chatterbox options entered")

    assert capsys.readouterr().out == ""


class FrontendBackend(TilterBackend):
  """Resource-free backend controlling frontend success and failure."""

  def __init__(self) -> None:
    super().__init__()
    self.failure: Optional[BaseException] = None

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FrontendBackend does not accept lifespan options")

  async def set_angle(self, angle: float) -> None:
    if self.failure is not None:
      raise self.failure


class TestTilterFrontend(AnyioTestBase):
  async def test_frontend_angle_changes_only_after_success(self) -> None:
    backend = FrontendBackend()
    tilter = Tilter(
      name="tilter",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=backend,
      hinge_coordinate=Coordinate.zero(),
      child_location=Coordinate.zero(),
    )

    await tilter.set_angle(3)
    assert tilter.absolute_angle == 3
    backend.failure = RuntimeError("move failed")
    with pytest.raises(RuntimeError, match="move failed"):
      await tilter.set_angle(8)
    assert tilter.absolute_angle == 3

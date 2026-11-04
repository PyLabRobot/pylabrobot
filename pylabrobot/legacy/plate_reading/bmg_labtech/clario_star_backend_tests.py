import importlib
import warnings
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.legacy.plate_reading.bmg_labtech import clario_star_backend
from pylabrobot.legacy.plate_reading.bmg_labtech.clario_star_backend import CLARIOstarBackend
from pylabrobot.testing.concurrency import AnyioTestBase

ReadEffect = Union[bytes, Callable[[], Awaitable[bytes]]]


class RecordingFTDI(HasLifespan):
  """Replay FTDI reads and record lifecycle, configuration, and wire events."""

  def __init__(
    self,
    reads: Optional[Sequence[ReadEffect]] = None,
    *,
    events: Optional[List[str]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    repeat_read: Optional[ReadEffect] = None,
  ) -> None:
    super().__init__()
    self.reads = list(reads or [])
    self.events = events if events is not None else []
    self.failures = failures or {}
    self.repeat_read = repeat_read
    self.writes: List[bytes] = []
    self.entries = 0
    self.exits = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingFTDI does not accept lifespan options")
    self.entries += 1
    self.events.append("ftdi.enter")
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("ftdi.exit")

  async def _record(self, event: str) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(event)
    failure = self.failures.get(event)
    if failure is not None:
      raise failure

  async def set_baudrate(self, baudrate: int) -> None:
    await self._record(f"baud:{baudrate}")

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    await self._record(f"line:{bits},{stopbits},{parity}")

  async def set_latency_timer(self, latency: int) -> None:
    await self._record(f"latency:{latency}")

  async def write(self, data: bytes) -> int:
    await anyio.lowlevel.checkpoint()
    self.writes.append(data)
    self.events.append(f"write:{data.hex()}")
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    await anyio.lowlevel.checkpoint()
    effect = self.reads.pop(0) if self.reads else self.repeat_read
    if effect is None:
      return b""
    return await effect() if callable(effect) else effect


def _make_backend(
  monkeypatch: pytest.MonkeyPatch,
  reads: Optional[Sequence[ReadEffect]] = None,
  **ftdi_kwargs: Any,
) -> tuple[CLARIOstarBackend, RecordingFTDI]:
  ftdi = RecordingFTDI(reads, **ftdi_kwargs)
  monkeypatch.setattr(clario_star_backend, "FTDI", lambda **kwargs: cast(FTDI, ftdi))
  return CLARIOstarBackend(), ftdi


def _set_initializers(
  backend: CLARIOstarBackend,
  events: List[str],
  *,
  initialize_error: Optional[BaseException] = None,
  eeprom_error: Optional[BaseException] = None,
) -> None:
  async def initialize() -> None:
    events.append("initialize")
    await anyio.lowlevel.checkpoint()
    if initialize_error is not None:
      raise initialize_error

  async def request_eeprom_data() -> None:
    events.append("eeprom")
    await anyio.lowlevel.checkpoint()
    if eeprom_error is not None:
      raise eeprom_error

  backend.initialize = initialize  # type: ignore[method-assign]
  backend.request_eeprom_data = request_eeprom_data  # type: ignore[method-assign]


async def _cancelled_entry(backend: CLARIOstarBackend) -> None:
  async with backend:
    pytest.fail("cancelled entry reached the body")


class TestCLARIOstarLifecycle(AnyioTestBase):
  def test_inherits_managed_methods(self) -> None:
    assert CLARIOstarBackend.__aenter__ is HasLifespan.__aenter__
    assert CLARIOstarBackend.__aexit__ is HasLifespan.__aexit__
    assert CLARIOstarBackend.setup is HasLifespan.setup
    assert CLARIOstarBackend.stop is HasLifespan.stop

  async def test_rejects_options_before_ftdi_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, ftdi = _make_backend(monkeypatch)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options reached the body")

    assert ftdi.events == []

  async def test_configures_ftdi_before_initialization(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    backend, ftdi = _make_backend(monkeypatch, events=events)
    _set_initializers(backend, events)

    async with backend:
      pass

    assert events[:5] == ["ftdi.enter", "baud:125000", "line:8,0,0", "latency:2", "initialize"]
    assert events == [
      "ftdi.enter",
      "baud:125000",
      "line:8,0,0",
      "latency:2",
      "initialize",
      "eeprom",
      "ftdi.exit",
    ]
    assert ftdi.exits == 1

  async def test_configuration_failure_closes_ftdi(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("configuration failed")
    backend, ftdi = _make_backend(monkeypatch, failures={"line:8,0,0": failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed configuration reached the body")

    assert error_info.value is failure
    assert ftdi.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_initialize_failure_closes_ftdi(self, monkeypatch: pytest.MonkeyPatch) -> None:
    events: List[str] = []
    failure = RuntimeError("initialize failed")
    backend, ftdi = _make_backend(monkeypatch, events=events)
    _set_initializers(backend, events, initialize_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed initialization reached the body")

    assert error_info.value is failure
    assert ftdi.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_eeprom_failure_closes_ftdi(self, monkeypatch: pytest.MonkeyPatch) -> None:
    events: List[str] = []
    failure = RuntimeError("EEPROM failed")
    backend, ftdi = _make_backend(monkeypatch, events=events)
    _set_initializers(backend, events, eeprom_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed EEPROM request reached the body")

    assert error_info.value is failure
    assert ftdi.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_cancelled_entry_closes_ftdi(self, monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    backend, ftdi = _make_backend(monkeypatch)

    async def initialize() -> None:
      initialize_started.set()
      await anyio.sleep_forever()

    backend.initialize = initialize  # type: ignore[method-assign]

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await _cancelled_entry(backend)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await initialize_started.wait()
      scopes[0].cancel()

    assert ftdi.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_body_cancellation_closes_ftdi(self, monkeypatch: pytest.MonkeyPatch) -> None:
    body_started = anyio.Event()
    backend, ftdi = _make_backend(monkeypatch)
    _set_initializers(backend, ftdi.events)

    async def run() -> None:
      async with backend:
        body_started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert ftdi.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_sequential_reuse_runs_full_initialization(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    backend, ftdi = _make_backend(monkeypatch, events=events)
    _set_initializers(backend, events)

    async with backend:
      pass
    async with backend:
      pass

    assert events.count("ftdi.enter") == 2
    assert events.count("baud:125000") == 2
    assert events.count("line:8,0,0") == 2
    assert events.count("latency:2") == 2
    assert events.count("initialize") == 2
    assert events.count("eeprom") == 2
    assert events.count("ftdi.exit") == 2


class TestCLARIOstarTransactions(AnyioTestBase):
  async def test_concurrent_send_does_not_cross_responses(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    first_read_started = anyio.Event()
    release_first = anyio.Event()

    async def first_response() -> bytes:
      first_read_started.set()
      await release_first.wait()
      return b"first\r"

    backend, ftdi = _make_backend(monkeypatch, [first_response, b"second\r"])
    results: Dict[str, bytes] = {}

    async def send(name: str) -> None:
      results[name] = await backend.send(name.encode("ascii"))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send, "first")
      await first_read_started.wait()
      task_group.start_soon(send, "second")
      await anyio.lowlevel.checkpoint()
      assert len(ftdi.writes) == 1
      release_first.set()

    first = results["first"]
    second = results["second"]
    assert first == b"first\r" and second == b"second\r"

  async def test_cancelled_lock_wait_writes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, ftdi = _make_backend(monkeypatch)
    await backend._transaction_lock.acquire()

    with anyio.CancelScope() as scope:
      scope.cancel()
      await backend.send(b"cancelled")

    backend._transaction_lock.release()
    assert ftdi.writes == []

  async def test_cancel_after_write_consumes_response(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    response_started = anyio.Event()
    release_response = anyio.Event()
    response_consumed = anyio.Event()
    cancelled = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def response() -> bytes:
      response_started.set()
      await release_response.wait()
      response_consumed.set()
      return b"committed\r"

    backend, _ = _make_backend(monkeypatch, [response])

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend.send(b"committed")
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await response_started.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert not cancelled.is_set()
      release_response.set()

    assert response_consumed.is_set()
    assert cancelled.is_set()

  async def test_stalled_read_is_bounded_and_releases_lock(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    read_started = anyio.Event()
    release_read = anyio.Event()
    read_cancelled = anyio.Event()

    async def stalled_response() -> bytes:
      read_started.set()
      try:
        await release_read.wait()
      except anyio.get_cancelled_exc_class():
        read_cancelled.set()
        raise
      return b"late\r"

    backend, ftdi = _make_backend(monkeypatch, [stalled_response])

    async def release_for_test_cleanup() -> None:
      await anyio.sleep(0.05)
      release_read.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(release_for_test_cleanup)
      with pytest.raises(TimeoutError):
        await backend.send(b"bounded", read_timeout=0.01)

    assert read_started.is_set()
    assert read_cancelled.is_set()
    assert len(ftdi.writes) == 1
    with anyio.fail_after(0.1):
      await backend._transaction_lock.acquire()
    backend._transaction_lock.release()

  async def test_standalone_reply_retains_partial_bytes_at_deadline(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A quiet or stalled FTDI reply retains bytes already framed."""

    async def stalled_response() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend, _ = _make_backend(monkeypatch, [b"partial", stalled_response])
    with anyio.fail_after(1):
      assert await backend.read_resp(timeout=0.01) == b"partial"

  async def test_continuous_unterminated_bytes_are_bounded(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    stop_stream = anyio.Event()
    read_count = 0

    async def unterminated_response() -> bytes:
      nonlocal read_count
      read_count += 1
      return b"x" * 25 if not stop_stream.is_set() else b"\r"

    backend, _ = _make_backend(monkeypatch, repeat_read=unterminated_response)

    async def stop_for_test_cleanup() -> None:
      await anyio.sleep(0.05)
      stop_stream.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(stop_for_test_cleanup)
      with pytest.raises(TimeoutError):
        await backend.send(b"unterminated", read_timeout=0.01)

    assert read_count > 1

  async def test_cancelled_committed_send_times_out_before_waiter_writes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    read_started = anyio.Event()
    release_read = anyio.Event()
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    cancelled = anyio.Event()

    async def stalled_response() -> bytes:
      read_started.set()
      try:
        await release_read.wait()
      except anyio.get_cancelled_exc_class():
        events.append("read.cancelled")
        raise
      events.append("read.released")
      return b"late\r"

    backend, ftdi = _make_backend(monkeypatch, [stalled_response, b"second\r"])

    async def first_send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend.send(b"first", read_timeout=0.01)
        except anyio.get_cancelled_exc_class():
          events.append("outer.cancelled")
          cancelled.set()
          raise

    async def second_send() -> None:
      await backend.send(b"second", read_timeout=0.1)
      events.append("second.complete")

    async def release_for_test_cleanup() -> None:
      await anyio.sleep(0.05)
      events.append("cleanup.release")
      release_read.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(release_for_test_cleanup)
      task_group.start_soon(first_send)
      await read_started.wait()
      scopes[0].cancel()
      task_group.start_soon(second_send)
      await anyio.lowlevel.checkpoint()
      assert len(ftdi.writes) == 1

    assert cancelled.is_set()
    assert events.index("read.cancelled") < events.index("outer.cancelled")
    assert events.index("outer.cancelled") < events.index("second.complete")
    assert events.index("second.complete") < events.index("cleanup.release")


class TestCLARIOstarPolling(AnyioTestBase):
  async def test_ready_wait_returns_original_response(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = _make_backend(monkeypatch)

    async def ready_status() -> bytes:
      return b"\x00\x00\x18\x0c\x01\x05" + bytes(18)

    backend.read_command_status = ready_status  # type: ignore[method-assign]

    assert await backend._wait_for_ready_and_return(b"result") == b"result"

  async def test_stalled_status_returns_none_at_timeout(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, _ = _make_backend(monkeypatch)

    async def stalled_status() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend.read_command_status = stalled_status  # type: ignore[method-assign]
    with anyio.fail_after(1):
      assert await backend._wait_for_ready_and_return(b"result", timeout=0.01) is None

  async def test_zero_timeout_does_not_poll(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = _make_backend(monkeypatch)

    async def unexpected_status() -> bytes:
      raise AssertionError("zero timeout should not poll")

    backend.read_command_status = unexpected_status  # type: ignore[method-assign]
    assert await backend._wait_for_ready_and_return(b"result", timeout=0) is None

  async def test_measurement_wait_is_cancellable(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = _make_backend(monkeypatch)
    statuses = 0

    async def send(command: bytes, read_timeout: float = 20) -> bytes:
      del command, read_timeout
      return b"started"

    async def changing_status() -> bytes:
      nonlocal statuses
      statuses += 1
      return statuses.to_bytes(24, "big")

    backend.send = send  # type: ignore[assignment]
    backend.read_command_status = changing_status  # type: ignore[method-assign]
    backend._plate_bytes = lambda plate: bytes(62)  # type: ignore[method-assign]

    with anyio.move_on_after(0.01) as scope:
      await backend._run_luminescence(13, cast(Any, object()))

    assert scope.cancel_called


def test_legacy_alias_module_still_reexports_backend() -> None:
  with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    legacy_module = importlib.import_module("pylabrobot.legacy.plate_reading.clario_star_backend")

  assert legacy_module.CLARIOstarBackend is CLARIOstarBackend

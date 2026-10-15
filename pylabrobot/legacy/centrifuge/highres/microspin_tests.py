"""Edge-case tests for :class:`MicroSpinBackend` using a stub AnyIO byte stream.

End-to-end wire-level behaviour (command mappings, status-blocks-during-motion,
reset happy path, etc.) lives in :mod:`mock_server_tests` and runs against the
real :class:`MicroSpinMockServer` over a TCP socket. The tests in *this* file
only cover situations the mock server cannot reasonably reproduce:

* Malformed protocol bytes from the device (bad ACK line, EOF mid-response).
* Operating without :meth:`MicroSpinBackend.setup`.
* Specific ``ERROR!`` sequences whose exact text we want to assert on.
* Argument validation that never reaches the wire.
* Monkey-patched ``send_command`` for verifying timeout extension logic.
* Serialization (no socket).
* Reset's error-handling branches, which need precisely-controllable
  ``ERROR!`` responses from each step.
"""

from __future__ import annotations

import warnings
from typing import Any, Awaitable, Callable, List, Optional, Tuple, cast

import anyio
import pytest
from anyio.abc import ByteStream

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.socket import Socket
from pylabrobot.legacy.centrifuge.highres.microspin_backend import (
  MicroSpinAbortedError,
  MicroSpinBackend,
  MicroSpinError,
  MicroSpinProtocolError,
)
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO


class _FakeStream(ByteStream):
  """Captures sends and yields queued lines from an AnyIO byte stream."""

  def __init__(self, lines: List[bytes], *, hang_on_empty: bool = False) -> None:
    self.sent: bytearray = bytearray()
    self.closed = False
    self._lines: List[bytes] = list(lines)
    self._hang_on_empty = hang_on_empty

  async def send(self, data: bytes) -> None:
    self.sent.extend(data)

  async def aclose(self) -> None:
    self.closed = True

  async def send_eof(self) -> None:
    return None

  async def receive(self, max_bytes: int = 65536) -> bytes:
    if not self._lines:
      if self._hang_on_empty:
        await anyio.sleep_forever()
      raise anyio.EndOfStream
    line = self._lines.pop(0)
    if len(line) > max_bytes:
      self._lines.insert(0, line[max_bytes:])
      return line[:max_bytes]
    return line


class FakeMicroSpinSocket(MockIO):
  """Provide deterministic MicroSpin bytes and record Socket lifecycle events."""

  def __init__(
    self,
    lines: List[bytes],
    *,
    hang_on_empty: bool = False,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.stream = _FakeStream(lines, hang_on_empty=hang_on_empty)
    self.events: List[str] = []
    self.entry_error = entry_error
    self.exit_error = exit_error

  @property
  def sent(self) -> bytearray:
    """Return bytes written to the fake stream."""
    return self.stream.sent

  async def readline(self, timeout: Optional[float] = None) -> bytes:
    """Read the next complete line, accepting the Socket timeout interface."""
    del timeout
    try:
      return await self.stream.receive()
    except anyio.EndOfStream:
      return b""

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:  # type: ignore[override]
    """Write bytes to the fake stream, accepting the Socket timeout interface."""
    del timeout
    await self.stream.send(data)

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    """Record Socket acquisition and register checkpointing cleanup."""
    if kwargs:
      raise TypeError("FakeMicroSpinSocket does not accept lifespan options")
    self.events.append("socket.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._exit_socket)

  async def _exit_socket(self) -> None:
    """Record Socket cleanup after a cancellation checkpoint."""
    await anyio.lowlevel.checkpoint()
    self.events.append("socket.exit")
    await self.stream.aclose()
    if self.exit_error is not None:
      raise self.exit_error


class FailAfterMicroSpinEntry(MicroSpinBackend):
  """Fail after native entry to exercise partial-entry rollback."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    await super()._enter_lifespan(stack, **backend_kwargs)
    raise RuntimeError("post-entry failure")


def _make_backend(server_lines: List[str]) -> Tuple[MicroSpinBackend, FakeMicroSpinSocket]:
  """Build a backend pre-wired to a fake byte stream. No real socket."""
  backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
  io = FakeMicroSpinSocket([line.encode("ascii") for line in server_lines])
  backend.io = cast(Socket, io)
  return backend, io


def _sent_commands(writer: FakeMicroSpinSocket) -> List[str]:
  text = writer.sent.decode("ascii")
  return [line for line in text.split("\r\n") if line]


def test_native_backend_uses_managed_lifecycle_methods() -> None:
  assert MicroSpinBackend.setup is HasLifespan.setup
  assert MicroSpinBackend.stop is HasLifespan.stop


class TestMicroSpinLifespan(AnyioTestBase):
  async def test_lifespan_closes_socket(self) -> None:
    backend, io = _make_backend([])

    async with backend:
      assert backend.is_active

    assert io.events == ["socket.enter", "socket.exit"]
    assert not backend.is_active

  async def test_socket_entry_failure_never_reaches_body(self) -> None:
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    io = FakeMicroSpinSocket([], entry_error=RuntimeError("socket open failed"))
    backend.io = cast(Socket, io)

    with pytest.raises(RuntimeError, match="socket open failed"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert io.events == ["socket.enter"]
    assert not backend.is_active

  async def test_failure_after_socket_entry_rolls_back_socket(self) -> None:
    backend = FailAfterMicroSpinEntry(host="ignored", port=0, timeout=2.0)
    io = FakeMicroSpinSocket([])
    backend.io = cast(Socket, io)

    with pytest.raises(RuntimeError, match="post-entry failure"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert io.events == ["socket.enter", "socket.exit"]
    assert not backend.is_active

  async def test_cancelled_exit_completes_socket_close(self) -> None:
    backend, io = _make_backend([])
    body_started = anyio.Event()

    async def run_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert io.events == ["socket.enter", "socket.exit", "cancelled"]
    assert not backend.is_active

  async def test_sequential_reuse_enters_and_exits_socket_twice(self) -> None:
    backend, io = _make_backend([])

    async with backend:
      pass
    async with backend:
      pass

    assert io.events == ["socket.enter", "socket.exit", "socket.enter", "socket.exit"]
    assert not backend.is_active

  async def test_socket_exit_failure_propagates_and_leaves_backend_inactive(self) -> None:
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    io = FakeMicroSpinSocket([], exit_error=RuntimeError("socket close failed"))
    backend.io = cast(Socket, io)

    with pytest.raises(RuntimeError, match="socket close failed"):
      async with backend:
        pass

    assert io.events == ["socket.enter", "socket.exit"]
    assert not backend.is_active


class TestMicroSpinProtocolEdgeCases(AnyioTestBase):
  """Cases the real mock server cannot easily produce (malformed bytes / EOF)."""

  async def test_protocol_error_on_bad_ack(self):
    backend, _ = _make_backend(["GARBAGE\r\n"])
    with pytest.raises(MicroSpinProtocolError):
      await backend.send_command("status")

  async def test_connection_closed_mid_response(self):
    backend, _ = _make_backend(["ACK! status 1\r\n"])  # no terminator, then EOF
    with pytest.raises(ConnectionError):
      await backend.send_command("status")

  async def test_setup_required_before_send_command(self):
    backend = MicroSpinBackend(host="ignored", port=0)
    with pytest.raises(RuntimeError):
      await backend.send_command("status")

  async def test_error_response_carries_diagnostic_lines(self):
    backend, _ = _make_backend(
      [
        "ACK! spin 0 0 0 1 19\r\n",
        "Error 1: (00:00:01) -12: bad params\r\n",
        "ERROR! spin 0 0 0 1 19\r\n",
      ]
    )
    with pytest.raises(MicroSpinError) as exc_info:
      await backend.send_command("spin 0 0 0 1")
    assert exc_info.value.command_id == 19
    assert exc_info.value.command == "spin 0 0 0 1"
    assert exc_info.value.error_lines == ["Error 1: (00:00:01) -12: bad params"]

  async def test_aborted_terminator_raises_aborted_error(self):
    """The device emits ``ABORTED!`` (a third terminator) for commands
    cancelled by an abort or issued while the abort latch is set; the
    parser must distinguish these from regular ``ERROR!`` responses.
    """
    backend, _ = _make_backend(
      [
        "ACK! spin 500 100 100 5 21\r\n",
        "ABORTED! spin 500 100 100 5 21\r\n",
      ]
    )
    with pytest.raises(MicroSpinAbortedError) as exc_info:
      await backend.send_command("spin 500 100 100 5")
    assert exc_info.value.command_id == 21
    assert exc_info.value.command == "spin 500 100 100 5"
    # ABORTED! typically arrives with no preceding diagnostic data lines.
    assert exc_info.value.error_lines == []
    # And it's a MicroSpinError subclass so plain `except MicroSpinError`
    # in caller code still catches aborts.
    assert isinstance(exc_info.value, MicroSpinError)


class TestMicroSpinStreamResync(AnyioTestBase):
  """After a cancelled/timed-out command, the next command must transparently
  drain the stale response and return its own result. Without this the
  protocol desyncs and every subsequent command reads someone else's data.
  """

  async def test_stale_response_is_drained_before_next_command(self):
    # Simulate the buffer state after a `status` was cancelled mid-flight:
    # the device eventually delivers its full response, then ours arrives.
    backend, _ = _make_backend(
      [
        # Stale response for the previously-cancelled command (cmd-id 5):
        "ACK! status 5\r\n",
        "Spindle Position: 9999\r\n",
        "OK! status 5\r\n",
        # Our fresh response (cmd-id 6):
        "ACK! version 6\r\n",
        "Version: MS-1.3.3\r\n",
        "OK! version 6\r\n",
      ]
    )
    # Pretend the previous send_command was cancelled after writing "status":
    backend._pending_terminator_count = 1

    data = await backend.send_command("version")
    assert data == ["Version: MS-1.3.3"]
    # And the stale-counter is back to zero.
    assert backend._pending_terminator_count == 0

  async def test_partial_stale_response_drained(self):
    # Cancelled AFTER reading the ACK but before the terminator: the buffer
    # holds the remaining data + OK, then our response.
    backend, _ = _make_backend(
      [
        # Leftover from a partially-read previous response (cmd-id 5):
        "Spindle Position: 9999\r\n",  # data line we hadn't read yet
        "OK! status 5\r\n",  # terminator we hadn't read yet
        # Our fresh response (cmd-id 6):
        "ACK! version 6\r\n",
        "Version: MS-1.3.3\r\n",
        "OK! version 6\r\n",
      ]
    )
    backend._pending_terminator_count = 1

    data = await backend.send_command("version")
    assert data == ["Version: MS-1.3.3"]
    assert backend._pending_terminator_count == 0

  async def test_multiple_stale_responses_drained(self):
    backend, _ = _make_backend(
      [
        # Two stale responses (cmd-ids 5 and 6):
        "ACK! status 5\r\n",
        "Spindle Position: 9999\r\n",
        "OK! status 5\r\n",
        "ACK! status 6\r\n",
        "Spindle Position: 1\r\n",
        "OK! status 6\r\n",
        # Our fresh response (cmd-id 7):
        "ACK! version 7\r\n",
        "Version: MS-1.3.3\r\n",
        "OK! version 7\r\n",
      ]
    )
    backend._pending_terminator_count = 2

    data = await backend.send_command("version")
    assert data == ["Version: MS-1.3.3"]
    assert backend._pending_terminator_count == 0

  async def test_send_command_keeps_pending_count_on_cancellation(self):
    """Verify the bookkeeping that enables the drain.

    If `send_command` is cancelled by an AnyIO deadline mid-read,
    the in-flight terminator must remain in the pending count so the *next*
    call can drain it.
    """
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    io = FakeMicroSpinSocket([b"ACK! home 5\r\n"], hang_on_empty=True)
    backend.io = cast(Socket, io)

    with pytest.raises(TimeoutError):
      with anyio.fail_after(0.05):
        await backend.send_command("home")
    assert backend._pending_terminator_count == 1

  async def test_fresh_connection_discards_pending_terminator(self):
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    first_io = FakeMicroSpinSocket([b"ACK! home 5\r\n"], hang_on_empty=True)
    backend.io = cast(Socket, first_io)

    async with backend:
      with pytest.raises(TimeoutError):
        with anyio.fail_after(0.05):
          await backend.send_command("home")
      assert backend._pending_terminator_count == 1

    second_io = FakeMicroSpinSocket(
      [
        b"ACK! version 6\r\n",
        b"Version: MS-1.3.3\r\n",
        b"OK! version 6\r\n",
      ]
    )
    backend.io = cast(Socket, second_io)

    async with backend:
      assert await backend.send_command("version") == ["Version: MS-1.3.3"]

    assert _sent_commands(second_io) == ["version"]
    assert backend._pending_terminator_count == 0


class TestMicroSpinValidation(AnyioTestBase):
  """Argument validation that raises before any bytes hit the wire."""

  async def test_spin_rounds_and_clamps_percents(self):
    backend, writer = _make_backend(
      [
        "ACK! spin 250 1 100 5 9\r\n",
        "OK! spin 250 1 100 5 9\r\n",
      ]
    )
    # 0.004 rounds to 0; we clamp to a minimum of 1%.
    await backend.spin(g=250.4, duration=5.2, acceleration=0.004, deceleration=1.0)
    assert _sent_commands(writer) == ["spin 250 1 100 5"]

  async def test_spin_rejects_out_of_range_g(self):
    backend, _ = _make_backend([])
    for bad in [-1, 0, 3001, 100000]:
      with pytest.raises(ValueError):
        await backend.spin(g=bad, duration=10)

  async def test_spin_rejects_short_duration(self):
    backend, _ = _make_backend([])
    with pytest.raises(ValueError):
      await backend.spin(g=100, duration=0.5)

  async def test_spin_rejects_bad_acceleration(self):
    backend, _ = _make_backend([])
    for bad in [0, -0.1, 1.1, 2.0]:
      with pytest.raises(ValueError):
        await backend.spin(g=100, duration=10, acceleration=bad)

  async def test_spin_rejects_bad_deceleration(self):
    backend, _ = _make_backend([])
    for bad in [0, -0.1, 1.1, 2.0]:
      with pytest.raises(ValueError):
        await backend.spin(g=100, duration=10, deceleration=bad)

  async def test_spin_warns_below_low_g_threshold(self):
    backend, writer = _make_backend(
      [
        "ACK! spin 20 50 50 5 1\r\n",
        "OK! spin 20 50 50 5 1\r\n",
      ]
    )
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await backend.spin(g=20, duration=5)
    low_g_warnings = [
      w for w in caught if issubclass(w.category, UserWarning) and "×g" in str(w.message)
    ]
    assert len(low_g_warnings) == 1
    assert _sent_commands(writer) == ["spin 20 50 50 5"]

  async def test_spin_does_not_warn_at_or_above_threshold(self):
    backend, _ = _make_backend(
      [
        "ACK! spin 30 50 50 5 1\r\n",
        "OK! spin 30 50 50 5 1\r\n",
      ]
    )
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await backend.spin(g=30, duration=5)
    low_g_warnings = [
      w for w in caught if issubclass(w.category, UserWarning) and "×g" in str(w.message)
    ]
    assert low_g_warnings == []

  async def test_spin_warns_at_slow_decel(self):
    """decel in [0.20, 0.40) -> 'long spin-down' warning, command still runs."""
    backend, writer = _make_backend(
      [
        "ACK! spin 1000 100 30 10 1\r\n",
        "OK! spin 1000 100 30 10 1\r\n",
      ]
    )
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await backend.spin(g=1000, duration=10, acceleration=1.0, deceleration=0.30)
    decel_warnings = [
      w for w in caught if issubclass(w.category, UserWarning) and "deceleration" in str(w.message)
    ]
    assert len(decel_warnings) == 1, [str(w.message) for w in caught]
    assert "long spin-down" in str(decel_warnings[0].message)
    assert _sent_commands(writer) == ["spin 1000 100 30 10"]

  async def test_spin_warns_at_stuck_decel_threshold(self):
    """decel < 0.20 -> 'firmware hang' warning takes precedence."""
    backend, writer = _make_backend(
      [
        "ACK! spin 1000 100 10 10 1\r\n",
        "OK! spin 1000 100 10 10 1\r\n",
      ]
    )
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await backend.spin(g=1000, duration=10, acceleration=1.0, deceleration=0.10)
    decel_warnings = [
      w for w in caught if issubclass(w.category, UserWarning) and "deceleration" in str(w.message)
    ]
    # Exactly one decel warning: the more severe (stuck) one wins.
    assert len(decel_warnings) == 1, [str(w.message) for w in caught]
    assert "firmware bug" in str(decel_warnings[0].message)
    assert _sent_commands(writer) == ["spin 1000 100 10 10"]

  async def test_spin_does_not_warn_at_safe_decel(self):
    """decel >= 0.40 is fully safe and emits no decel warning."""
    backend, _ = _make_backend(
      [
        "ACK! spin 1000 100 40 10 1\r\n",
        "OK! spin 1000 100 40 10 1\r\n",
      ]
    )
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await backend.spin(g=1000, duration=10, acceleration=1.0, deceleration=0.40)
    decel_warnings = [
      w for w in caught if issubclass(w.category, UserWarning) and "deceleration" in str(w.message)
    ]
    assert decel_warnings == []

  async def test_maintenance_door_and_lock_methods_raise_not_implemented(self):
    """open_door / close_door / the four lock primitives are maintenance-only
    on the MicroSpin (manual §6.7). They must raise rather than silently
    sending bytes; the firmware handles door + lock state internally as
    part of `open <bucket>`, `spin`, and `home`.
    """
    backend, writer = _make_backend([])
    methods: Tuple[Callable[[], Awaitable[None]], ...] = (
      backend.open_door,
      backend.close_door,
      backend.lock_door,
      backend.unlock_door,
      backend.lock_bucket,
      backend.unlock_bucket,
    )
    for method in methods:
      with pytest.raises(NotImplementedError):
        await method()
    assert _sent_commands(writer) == []


class TestMicroSpinHelperEdgeCases(AnyioTestBase):
  """Helper behaviour with carefully-shaped responses we don't get from the mock."""

  async def test_request_errors_returns_lines_verbatim(self):
    backend, _ = _make_backend(
      [
        "ACK! errors 3 4\r\n",
        "Error 1: foo\r\n",
        "Error 2: bar\r\n",
        "Error 3: baz\r\n",
        "OK! errors 3 4\r\n",
      ]
    )
    assert await backend.request_errors(3) == [
      "Error 1: foo",
      "Error 2: bar",
      "Error 3: baz",
    ]


class TestMicroSpinResetErrorPaths(AnyioTestBase):
  """Reset's error-handling branches, which need controllable ERROR! at each step."""

  async def test_reset_swallows_abort_error_by_default(self):
    # The MicroSpin can legitimately reject an abort if there's nothing to abort.
    # `reset` should still go on to clear the button-abort latch AND wait.
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "Error 1: nothing to abort\r\n",
        "ERROR! abort 1\r\n",
        "ACK! clearbuttonabort 2\r\n",
        "OK! clearbuttonabort 2\r\n",
        "ACK! status 3\r\n",
        "Spindle Position: 0\r\n",
        "OK! status 3\r\n",
      ]
    )
    result = await backend.reset()
    assert _sent_commands(writer) == ["abort", "clearbuttonabort", "status"]
    assert result == {"Spindle Position": "0"}

  async def test_reset_propagates_abort_error_when_asked(self):
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "Error 1: nothing to abort\r\n",
        "ERROR! abort 1\r\n",
      ]
    )
    with pytest.raises(MicroSpinError):
      await backend.reset(swallow_abort_errors=False)
    assert _sent_commands(writer) == ["abort"]

  async def test_reset_propagates_clear_button_abort_error(self):
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "OK! abort 1\r\n",
        "ACK! clearbuttonabort 2\r\n",
        "Error 1: stuck\r\n",
        "ERROR! clearbuttonabort 2\r\n",
      ]
    )
    with pytest.raises(MicroSpinError):
      await backend.reset()
    assert _sent_commands(writer) == ["abort", "clearbuttonabort"]

  async def test_reset_propagates_status_error(self):
    backend, writer = _make_backend(
      [
        "ACK! abort 1\r\n",
        "OK! abort 1\r\n",
        "ACK! clearbuttonabort 2\r\n",
        "OK! clearbuttonabort 2\r\n",
        "ACK! status 3\r\n",
        "Error 1: spindle wedged\r\n",
        "ERROR! status 3\r\n",
      ]
    )
    with pytest.raises(MicroSpinError):
      await backend.reset()
    assert _sent_commands(writer) == ["abort", "clearbuttonabort", "status"]


class TestMicroSpinTimeoutExtension(AnyioTestBase):
  """Monkey-patched send_command to verify long-motion timeout extension."""

  async def test_abort_uses_extended_default_timeout(self):
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    seen: List[Tuple[str, Optional[float]]] = []

    async def fake_send(command: str, *, timeout: Optional[float] = None) -> List[str]:
      seen.append((command, timeout))
      return []

    backend.send_command = fake_send  # type: ignore[assignment]
    await backend.abort()
    assert seen == [("abort", 180.0)]

    seen.clear()
    await backend.abort(timeout=5.0)
    assert seen == [("abort", 5.0)]

  async def test_wait_for_spindle_stopped_uses_poll_interval_per_call(self):
    """Each individual ``status`` is bounded by ``poll_interval``, not by
    the overall ``timeout``."""
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    seen: List[Tuple[str, Optional[float]]] = []

    async def fake_send(command: str, *, timeout: Optional[float] = None) -> List[str]:
      seen.append((command, timeout))
      return []

    backend.send_command = fake_send  # type: ignore[method-assign]
    # Defaults: poll_interval=60, total timeout=1800. First call should
    # get poll_interval (or min(poll_interval, remaining) which == 60).
    await backend.wait_for_spindle_stopped()
    assert seen == [("status", 60.0)]

    seen.clear()
    await backend.wait_for_spindle_stopped(poll_interval=5.0, timeout=100.0)
    assert seen == [("status", 5.0)]

  async def test_wait_for_spindle_stopped_retries_on_per_call_timeout(self):
    """If the per-call status times out, we issue another one."""
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    call_count: int = 0

    async def fake_send(command: str, *, timeout: Optional[float] = None) -> List[str]:
      nonlocal call_count
      call_count += 1
      if call_count < 3:
        raise TimeoutError("still spinning")
      return ["Spindle Position: 1958", "Door Position: -457"]

    backend.send_command = fake_send  # type: ignore[method-assign]
    result = await backend.wait_for_spindle_stopped(poll_interval=0.01, timeout=10.0)
    assert call_count == 3
    assert result == {"Spindle Position": "1958", "Door Position": "-457"}

  async def test_wait_for_spindle_stopped_raises_when_total_budget_expires(self):
    """With every poll timing out and a tight overall budget, we raise."""
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)

    async def fake_send(command: str, *, timeout: Optional[float] = None) -> List[str]:
      raise TimeoutError("still spinning")

    backend.send_command = fake_send  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
      await backend.wait_for_spindle_stopped(poll_interval=0.01, timeout=0.05)

  async def test_wait_for_spindle_stopped_propagates_microspin_error(self):
    """An ERROR! from status is a real device-state error -- don't retry."""
    backend = MicroSpinBackend(host="ignored", port=0, timeout=2.0)
    call_count: int = 0

    async def fake_send(command: str, *, timeout: Optional[float] = None) -> List[str]:
      nonlocal call_count
      call_count += 1
      raise MicroSpinError("status", 1, ["Error: spindle wedged"])

    backend.send_command = fake_send  # type: ignore[method-assign]
    with pytest.raises(MicroSpinError):
      await backend.wait_for_spindle_stopped(poll_interval=10.0, timeout=60.0)
    assert call_count == 1  # NOT retried

  async def test_wait_for_spindle_stopped_rejects_non_positive_poll_interval(self):
    backend = MicroSpinBackend(host="ignored", port=0)
    for bad in (0, -1, -0.001):
      with pytest.raises(ValueError):
        await backend.wait_for_spindle_stopped(poll_interval=bad)


class TestMicroSpinConstructor(AnyioTestBase):
  def test_default_port_is_1000(self):
    """Backend, factory, and the class constant must agree on 1000."""
    from pylabrobot.legacy.centrifuge import MicroSpin

    assert MicroSpinBackend.DEFAULT_PORT == 1000
    backend = MicroSpinBackend(host="example.invalid")
    assert backend.port == 1000
    cf = MicroSpin(name="x", host="example.invalid")
    assert isinstance(cf.backend, MicroSpinBackend)
    assert cf.backend.port == 1000

  def test_port_can_be_customised(self):
    from pylabrobot.legacy.centrifuge import MicroSpin

    backend = MicroSpinBackend(host="example.invalid", port=9001)
    assert backend.port == 9001
    cf = MicroSpin(name="x", host="example.invalid", port=9001)
    assert isinstance(cf.backend, MicroSpinBackend)
    assert cf.backend.port == 9001


class TestMicroSpinSerialize(AnyioTestBase):
  def test_serialize_includes_connection_info(self):
    backend = MicroSpinBackend(host="10.0.0.5", port=1234, timeout=12.5)
    s = backend.serialize()
    assert s["host"] == "10.0.0.5"
    assert s["port"] == 1234
    assert s["timeout"] == 12.5

  def test_serialize_records_default_port(self):
    backend = MicroSpinBackend(host="10.0.0.5")
    assert backend.serialize()["port"] == 1000

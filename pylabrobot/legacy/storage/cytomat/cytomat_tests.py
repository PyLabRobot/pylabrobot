import inspect
from collections import deque
from contextlib import suppress
from typing import Any, Deque, Dict, List, Optional, Tuple, cast
from unittest.mock import patch

import anyio
import pytest

pytest.importorskip("serial")

import serial
from trio.testing import MockClock

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.storage.cytomat import cytomat as cytomat_module
from pylabrobot.legacy.storage.cytomat.constants import CytomatType, ErrorRegister
from pylabrobot.legacy.storage.cytomat.cytomat import CytomatBackend, CytomatChatterbox
from pylabrobot.legacy.storage.cytomat.errors import (
  CytomatBusyError,
  CytomatCommandUnknownError,
  CytomatProcessTimeoutError,
  CytomatTelegramStructureError,
)
from pylabrobot.legacy.storage.incubator import Incubator
from pylabrobot.resources import Coordinate, PlateCarrier
from pylabrobot.testing.concurrency import AnyioTestBase

Command = Tuple[str, str, str]

# Deadlock guard for the event-driven real-clock tests. Those finish in milliseconds, so this
# is orders of magnitude larger than any correct run, but finite: a transaction that is never
# handed on fails the test rather than hanging the run.
_REAL_TIME_DEADLOCK_GUARD = 60.0

# A real-clock reply stall longer than the shrunken budgets the overrun tests install. It is
# finite on purpose, so a backend that applies no bound at all answers late and fails an
# assertion rather than hanging the run.
_REAL_TIME_STALL = 3.0

# A reply that never realistically arrives, for the virtual-clock deadline observations. It is
# finite for the same reason, and costs no real time because those tests use a mock clock.
_STALLED_SECONDS = 86400.0

# Deadlock guard for the virtual-clock tests. Ordered bound < guard < stall, so an overlong
# bound is reported by the elapsed-time assertions rather than by the guard, while a bound that
# never fires trips the guard instead of hanging the run.
_VIRTUAL_DEADLOCK_GUARD = 7200.0

# Slack allowed around a deadline observed on a virtual clock.
_DEADLINE_SLACK = 0.05

# Slack allowed around an interval observed on a real clock. It is far smaller than the
# intervals under test, so a retry gap or poll interval that collapsed to zero cannot pass.
_REAL_TIME_SLACK = 0.45

# Overview register values, as the hex byte the device answers to `ch:bs`.
_IDLE = "00"
_BUSY = "01"
_ERROR_SET = "08"


def _command_of(data: bytes) -> str:
  """Return a written command without its carriage return, for transcript assertions."""
  return data.decode("utf-8").rstrip("\r\n")


def _write_event(command: str) -> str:
  """Return the recorded event of one command write."""
  return f"write:{command}"


def _exchange_events(command: str) -> List[str]:
  """Return the recorded events of one complete command exchange.

  The reply read contributes two events, one when it begins and one when it hands the reply
  back. A read that was entered and then abandoned records only the first, so a transcript
  distinguishes a completed exchange from one cancellation cut short.
  """
  return [_write_event(command), "read", "read-done"]


def _poll_events(count: int = 1) -> List[str]:
  """Return the recorded events of `count` overview-register polls."""
  return [event for _ in range(count) for event in _exchange_events("ch:bs")]


def _default_reply(command: str) -> str:
  """Build the device's reply to a command it accepts.

  Checks answer with their own command at the start of the reply; every other command type
  answers with the action acknowledgement.
  """
  command_type, rest = command.split(":", 1)
  token = rest.split(" ")[0]
  key = token if command_type == "ch" else "ok"
  return f"{key} {_IDLE}"


class RecordingSerial(HasLifespan):
  """Software Serial double recording lifecycle and wire events."""

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    close_error: Optional[BaseException] = None,
    replies: Optional[Dict[str, List[str]]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    blocked_event: Optional[str] = None,
    write_stall: float = 0.0,
    read_stall: float = 0.0,
  ) -> None:
    """Initialize a closed double.

    Args:
      events: Shared ordered event log, or None for a private one.
      entry_error: Exception to raise while the transport opens.
      close_error: Exception to raise when the lifespan closes the transport.
      replies: Scripted replies keyed by command text; each is consumed once, after which
        the command falls back to the synthesized acknowledgement.
      failures: Exception to raise from the named event, keyed by event.
      blocked_event: Event name whose call waits for `release_blocked`.
      write_stall: Seconds a command write waits before it returns.
      read_stall: Seconds a reply read waits before answering. Unlike `blocked_event`, this
        stall ends on its own, so a caller that never bounds the exchange answers late and
        fails a timing assertion rather than hanging the run.
    """
    super().__init__()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.close_error = close_error
    self.replies: Dict[str, Deque[str]] = {
      command: deque(values) for command, values in (replies or {}).items()
    }
    self.failures = failures or {}
    self.blocked_event = blocked_event
    self.write_stall = write_stall
    self.read_stall = read_stall
    self.serial_kwargs: Dict[str, Any] = {}
    self.writes: List[bytes] = []
    self.commands: List[str] = []
    self.reads: List[int] = []
    self.entries = 0
    self.exits = 0
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingSerial does not accept lifespan options")
    self.entries += 1
    self.events.append("serial.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    """Record the transport close and raise the injected close failure."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("serial.exit")
    if self.close_error is not None:
      raise self.close_error

  async def _block_if_requested(self, event: str) -> None:
    """Wait for release when this event is the configured blocking point."""
    if event == self.blocked_event:
      self.blocked_started.set()
      await self.release_blocked.wait()

  async def write(self, data: bytes) -> None:
    """Record one command write at its entry, then block, stall, or fail as configured.

    The event is appended before this double awaits anything of its own. A transport call is
    the moment work reaches the wire, so recording it after a scheduler hop would report it
    one hop late and hide orderings the caller genuinely produces.
    """
    command = _command_of(data)
    self.writes.append(data)
    self.commands.append(command)
    self.events.append(_write_event(command))
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested(_write_event(command))
    if self.write_stall > 0:
      await anyio.sleep(self.write_stall)
    failure = self.failures.get(_write_event(command))
    if failure is not None:
      raise failure

  async def read(self, num_bytes: int = 1) -> bytes:
    """Record one reply read at its entry and again when it answers, blocking as configured.

    The entry event is appended before this double awaits anything of its own, for the same
    reason the write's is. The second event is appended only on the way out, so a read that
    was entered and then abandoned leaves the first behind without the second. Without it an
    abandoned read and a completed one produce the same transcript, and an exchange that
    cancellation cut in half would be indistinguishable from one that ran to the end.
    """
    self.reads.append(num_bytes)
    self.events.append("read")
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("read")
    if self.read_stall > 0:
      await anyio.sleep(self.read_stall)
    failure = self.failures.get("read")
    if failure is not None:
      raise failure
    command = self.commands[-1]
    scripted = self.replies.get(command)
    reply = scripted.popleft() if scripted else _default_reply(command)
    self.events.append("read-done")
    return reply.encode("utf-8")


class SecondAcquireObservedLock:
  """Wrap a transaction lock and publish when its second acquisition is attempted."""

  def __init__(self) -> None:
    self._lock = anyio.Lock()
    self._attempts = 0
    self.second_acquire_attempted = anyio.Event()

  async def acquire(self) -> None:
    """Acquire the lock after recording the second caller's attempt."""
    self._attempts += 1
    if self._attempts == 2:
      self.second_acquire_attempted.set()
    await self._lock.acquire()

  def release(self) -> None:
    """Release the wrapped lock."""
    self._lock.release()


def make_backend(
  monkeypatch: pytest.MonkeyPatch,
  *,
  model: CytomatType = CytomatType.C2C_450_SHAKE,
  port: str = "COM_CYTOMAT",
  **double_kwargs: Any,
) -> Tuple[CytomatBackend, RecordingSerial]:
  """Build a Cytomat backend whose transport is a software Serial double."""
  io = RecordingSerial(**double_kwargs)

  def build_serial(**kwargs: Any) -> Serial:
    io.serial_kwargs = dict(kwargs)
    return cast(Serial, io)

  monkeypatch.setattr(cytomat_module, "Serial", build_serial)
  return CytomatBackend(model=model, port=port), io


def make_chatterbox(
  monkeypatch: pytest.MonkeyPatch,
  *,
  model: CytomatType = CytomatType.C2C_450_SHAKE,
) -> Tuple[CytomatChatterbox, RecordingSerial]:
  """Build a chatterbox alongside a Serial double it must never enter."""
  io = RecordingSerial()

  def build_serial(**kwargs: Any) -> Serial:
    io.serial_kwargs = dict(kwargs)
    return cast(Serial, io)

  monkeypatch.setattr(cytomat_module, "Serial", build_serial)
  return CytomatChatterbox(model=model, port="COM_CYTOMAT"), io


def _wire_events(io: RecordingSerial, baseline: int, *markers: str) -> List[str]:
  """Return the transport events recorded since `baseline`, without the test's own markers.

  Tests append their own markers to the shared log to place an observation in the ordering.
  This drops them again, so an assertion about what reached the wire reads only wire events:
  `write:*`, `read`, `serial.enter`, and `serial.exit`.
  """
  return [event for event in io.events[baseline:] if event not in markers]


async def _settle() -> None:
  """Give every other task room to reach its next transport call."""
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


class SoftwareCytomatIO(HasLifespan):
  """Transport stand-in for the command double: it opens nothing and carries no bytes."""

  def __init__(self) -> None:
    super().__init__()
    self.entries = 0
    self.exits = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the stand-in and register its close."""
    if kwargs:
      raise TypeError("SoftwareCytomatIO does not accept lifespan options")
    self.entries += 1
    stack.callback(self._close)
    await anyio.lowlevel.checkpoint()

  def _close(self) -> None:
    """Record the close of a transport that never opened anything."""
    self.exits += 1


class SoftwareCytomat(CytomatBackend):
  """Cytomat command double that never opens its Serial transport."""

  def __init__(self, model: CytomatType) -> None:
    super().__init__(model=model, port="COM_CYTOMAT")
    self.software_io = SoftwareCytomatIO()
    self.io = cast(Serial, self.software_io)
    self.commands: List[Command] = []
    self.failures: Dict[Command, List[BaseException]] = {}
    self.entered: Dict[Command, anyio.Event] = {}
    self.releases: Dict[Command, anyio.Event] = {}

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Publish this double's scripted composition without the Serial-owning parent entry.

    The parent entry owns Serial and issues `ll:in` and its completion polls. This double
    scripts commands directly, so it registers the same shaking cleanup the parent does and
    issues none of the parent's entry commands.
    """
    if kwargs:
      raise TypeError("SoftwareCytomat does not accept lifespan options")
    await stack.enter_async_context(self.software_io.lifespan())
    stack.push_shielded_async_callback(
      self._force_stop_shaking, shield_timeout=cytomat_module._CLEANUP_TIMEOUT
    )

  async def set_racks(self, racks: List[PlateCarrier]) -> None:
    self._racks = racks

  async def send_command(self, command_type: str, command: str, params: str) -> str:
    key = (command_type, command, params)
    self.commands.append(key)
    self._publish_dispatch()
    if key in self.entered:
      self.entered[key].set()
    if key in self.releases:
      await self.releases[key].wait()
    if self.failures.get(key):
      raise self.failures[key].pop(0)
    return "0" * 8


def make_frontend(backend: CytomatBackend) -> Incubator:
  return Incubator(
    backend=backend,
    name="cytomat",
    size_x=10,
    size_y=10,
    size_z=10,
    racks=[],
    loading_tray_location=Coordinate.zero(),
  )


class TestCytomatBackendLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert CytomatBackend.setup is HasLifespan.setup
    assert CytomatBackend.stop is HasLifespan.stop
    assert CytomatBackend.__aenter__ is HasLifespan.__aenter__
    assert CytomatBackend.__aexit__ is HasLifespan.__aexit__

  def test_serial_configuration_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _, io = make_backend(monkeypatch)

    assert io.serial_kwargs == {
      "port": "COM_CYTOMAT",
      "baudrate": 9600,
      "bytesize": serial.EIGHTBITS,
      "parity": serial.PARITY_NONE,
      "stopbits": serial.STOPBITS_ONE,
      "write_timeout": 1,
      "timeout": 1,
      "human_readable_device_name": "Cytomat",
    }

  async def test_rejects_lifespan_options_before_serial_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert io.events == []
    assert io.entries == 0

  async def test_entry_sequence_matches_the_legacy_setup(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      assert io.events == [
        "serial.enter",
        *_exchange_events("ll:in"),
        *_poll_events(2),
      ]

  async def test_serial_entry_failure_does_not_command_the_device(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    failure = RuntimeError("serial entry failed")
    backend, io = make_backend(monkeypatch, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed Serial entry reached the body")

    assert error_info.value is failure
    assert io.events == ["serial.enter"]
    assert io.exits == 0
    assert not backend.is_active

  async def test_initialization_failure_closes_serial(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    failure = RuntimeError("initialize failed")
    backend, io = make_backend(monkeypatch, failures={_write_event("ll:in"): failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed initialization reached the body")

    assert error_info.value is failure
    # No shaking was ever started, so the registered stop is a guarded no-op and adds no
    # command of its own before the transport closes.
    assert io.events == ["serial.enter", _write_event("ll:in"), "serial.exit"]
    assert not backend.is_active

  async def test_bare_exit_without_shaking_closes_serial_only(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.events)

    assert _wire_events(io, baseline) == ["serial.exit"]

  async def test_exit_stops_shaking_before_serial_closes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      await backend.start_shaking(12, shakers=[1])
      baseline = len(io.events)

    assert _wire_events(io, baseline) == [*_exchange_events("ll:vd"), "serial.exit"]

  async def test_a_failing_exit_stop_is_attempted_exactly_once(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The lifespan registers the shaking stop once, and a failure does not multiply it.

    A stop that succeeds clears the tracked shaking state, so a second registration of the
    same callback would find nothing to do and put nothing extra on the wire. A stop that
    fails is the case that tells them apart: `_stop_shaking_transition` retains the state as
    active and unknown precisely so the stop stays retryable, so every further registered
    copy dispatches its own `ll:vd`.
    """
    failure = RuntimeError("global stop failed")
    backend, io = make_backend(monkeypatch)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        await backend.start_shaking(12, shakers=[1])
        baseline = len(io.events)
        io.failures[_write_event("ll:vd")] = failure

    # The exit runs to the end despite the failing stop, and the cleanup failure is reported
    # rather than swallowed.
    assert error_info.value is failure
    assert io.exits == 1
    assert io.commands.count("ll:vd") == 1
    assert _wire_events(io, baseline) == [_write_event("ll:vd"), "serial.exit"]
    # The failed stop left the state retryable, so a later caller can still stop the device.
    assert backend._shaking_active
    assert backend._shaking_state_unknown

  async def test_body_cancellation_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    body_started = anyio.Event()
    backend, io = make_backend(monkeypatch)

    async def use_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_backend)
        await body_started.wait()
        task_group.cancel_scope.cancel()

    assert io.events[-2:] == ["serial.exit", "cancelled"]
    assert not backend.is_active

  async def test_close_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("serial close failed")
    backend, io = make_backend(monkeypatch, close_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert io.exits == 1
    assert not backend.is_active

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      pass
    async with backend:
      pass

    assert io.entries == 2
    assert io.exits == 2

  async def test_cleanup_bound_covers_two_exchanges(self) -> None:
    # Both against the exchange budget it is derived from, so the two cannot drift apart, and
    # against its literal value, so they cannot be moved together. The bound covers one
    # exchange the shaking owner may still be completing plus the registered stop's own
    # `ll:vd`; it is fixed and never derives from a frequency or a shake duration.
    assert cytomat_module._CLEANUP_TIMEOUT == 2 * cytomat_module._COMMAND_TIMEOUT + 4.0
    assert cytomat_module._CLEANUP_TIMEOUT == 8.0

  async def test_registered_stop_gives_up_waiting_for_the_shaking_owner(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A registered stop that cannot reach the shaking owner fails instead of waiting forever.

    The bound on the registration is what stops the cleanup waiting indefinitely for
    `_shaking_transition_lock` before it can dispatch anything. It is the only part of this
    cleanup the bound reaches: `_stop_shaking_transition` shields the `ll:vd` exchange, and
    that exchange carries `_COMMAND_TIMEOUT` instead. The holder below is released by this
    test on the bounded path and by its own exchange budget otherwise, so a cleanup with no
    bound at all finishes late and fails these assertions rather than hanging the run.
    """
    backend, io = make_backend(monkeypatch)

    async def hold_the_shaking_owner() -> None:
      # `set_shaking_frequency` holds the shaking transition lock across its exchange, which
      # is exactly the wait the registered stop has to queue behind.
      with suppress(TimeoutError):
        await backend.set_shaking_frequency(12, shakers=[1])

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(cytomat_module, "_CLEANUP_TIMEOUT", 0.05):
        # Entered under the patch, because the registration captures the bound on entry.
        lifespan = backend.lifespan()
        await lifespan.__aenter__()
        io.blocked_event = "read"
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(hold_the_shaking_owner)
          await io.blocked_started.wait()
          with pytest.raises(TimeoutError) as error_info:
            await lifespan.__aexit__(None, None, None)
          io.blocked_event = None
          io.release_blocked.set()

    # `push_shielded_async_callback` implements its bound as `anyio.fail_after`, so an
    # overrun surfaces as a bare `TimeoutError`; the failing callback is named by the
    # traceback rather than by the message.
    assert type(error_info.value) is TimeoutError
    assert str(error_info.value) == ""
    # The transport still closed, so a bounded cleanup does not leak the connection.
    assert io.exits == 1


class TestCytomatChatterboxLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert CytomatChatterbox.setup is HasLifespan.setup
    assert CytomatChatterbox.stop is HasLifespan.stop
    assert CytomatChatterbox.__aenter__ is HasLifespan.__aenter__
    assert CytomatChatterbox.__aexit__ is HasLifespan.__aexit__

  async def test_rejects_lifespan_options_before_any_output(
    self, monkeypatch: pytest.MonkeyPatch, capsys
  ) -> None:
    backend, io = make_chatterbox(monkeypatch)
    capsys.readouterr()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert capsys.readouterr().out == ""
    assert io.entries == 0

  async def test_entry_waits_without_entering_serial(
    self, monkeypatch: pytest.MonkeyPatch, capsys
  ) -> None:
    backend, io = make_chatterbox(monkeypatch)

    async with backend:
      entry_output = capsys.readouterr().out

    # The legacy chatterbox setup was exactly one completion wait, and it owns no transport.
    # It prints the assembled command, so the model's carriage return is part of the output.
    assert entry_output == "cytomat ch:bs\r\n\n"
    assert io.entries == 0
    assert io.events == []

  async def test_exit_stops_shaking_before_the_close_print(
    self, monkeypatch: pytest.MonkeyPatch, capsys
  ) -> None:
    backend, _ = make_chatterbox(monkeypatch)

    async with backend:
      await backend.start_shaking(12, shakers=[1])
      capsys.readouterr()
    exit_output = capsys.readouterr().out

    assert "cytomat ll:vd" in exit_output
    assert exit_output.index("cytomat ll:vd") < exit_output.index("closing connection to cytomat")

  async def test_bare_exit_without_shaking_prints_only_the_close(
    self, monkeypatch: pytest.MonkeyPatch, capsys
  ) -> None:
    backend, _ = make_chatterbox(monkeypatch)

    async with backend:
      capsys.readouterr()
    exit_output = capsys.readouterr().out

    assert exit_output == "closing connection to cytomat\n"


class TestCytomatFrontendComposition(AnyioTestBase):
  async def test_incubator_exit_issues_exactly_one_global_stop(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    frontend = make_frontend(backend)

    async with frontend:
      await frontend.start_shaking(12)
      baseline = len(io.events)

    # `Incubator._pre_backend_stop` force-stops shaking and the backend lifespan registers the
    # same stop, but the second call is guarded on tracked state, so only one `ll:vd` reaches
    # the wire across a full frontend exit.
    assert _wire_events(io, baseline) == [*_exchange_events("ll:vd"), "serial.exit"]
    assert io.commands.count("ll:vd") == 1

  async def test_incubator_exit_without_shaking_issues_no_stop(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    frontend = make_frontend(backend)

    async with frontend:
      baseline = len(io.events)

    assert _wire_events(io, baseline) == ["serial.exit"]


class TestCytomatCommandTransactions(AnyioTestBase):
  async def test_one_command_at_a_time_reaches_the_wire(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async def blocked_command() -> None:
      await backend.send_command("ch", "bw", "")

    async def waiting_command() -> None:
      await backend.send_command("ch", "be", "")

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(blocked_command)
          await io.blocked_started.wait()
          task_group.start_soon(waiting_command)
          await _settle()
          # The second command is holding at the transaction, not at the wire.
          assert _wire_events(io, baseline) == [_write_event("ch:bw"), "read"]
          io.blocked_event = None
          io.release_blocked.set()
      assert _wire_events(io, baseline) == [
        *_exchange_events("ch:bw"),
        *_exchange_events("ch:be"),
      ]

  async def test_a_cancelled_transaction_waiter_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def blocked_command() -> None:
      await backend.send_command("ch", "bw", "")

    async def cancelled_command() -> None:
      try:
        await backend.send_command("ch", "be", "")
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as holder_group:
          holder_group.start_soon(blocked_command)
          await io.blocked_started.wait()
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancelled_command)
            await _settle()
            task_group.cancel_scope.cancel()
          io.blocked_event = None
          io.release_blocked.set()

      assert observed == ["cancelled"]
      # The cancelled waiter never reached the wire, so `ch:be` was never written.
      assert _wire_events(io, baseline) == _exchange_events("ch:bw")

  async def test_a_committed_exchange_completes_before_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def read_register() -> None:
      try:
        await backend.send_command("ch", "bw", "")
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_register)
          await io.blocked_started.wait()
          task_group.cancel_scope.cancel()
          io.blocked_event = None
          io.release_blocked.set()

      assert observed == ["cancelled"]
      # The write had committed, so the reply was read rather than abandoned.
      assert _wire_events(io, baseline) == _exchange_events("ch:bw")

  async def test_the_transaction_is_handed_on_before_its_holder_observes_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A cancelled holder hands the transaction on before it observes its own cancellation.

    The final checkpoint sits after the release, so the next command is already free to
    reach the wire when the cancelled holder raises. Moving that checkpoint ahead of the
    release reverses the two and makes every queued command wait for one cancelled caller
    to finish unwinding.
    """
    backend, io = make_backend(monkeypatch)

    async def cancelled_holder() -> None:
      try:
        await backend.send_command("ch", "bw", "")
      except anyio.get_cancelled_exc_class():
        io.events.append("holder-cancelled")
        raise

    async def waiting_command() -> None:
      await backend.send_command("ch", "be", "")

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.events)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as waiter_group:
          async with anyio.create_task_group() as holder_group:
            holder_group.start_soon(cancelled_holder)
            # The holder's write has committed and its exchange is shielded, so the
            # cancellation below cannot reach it until the exchange is over.
            await io.blocked_started.wait()
            waiter_group.start_soon(waiting_command)
            await _settle()
            holder_group.cancel_scope.cancel()
            io.blocked_event = None
            io.release_blocked.set()

      ordering = [
        event
        for event in io.events[baseline:]
        if event in ("holder-cancelled", _write_event("ch:be"))
      ]

    assert ordering == [_write_event("ch:be"), "holder-cancelled"]

  async def test_overrun_yields_to_a_pending_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """An overrun exchange whose caller was cancelled reports the cancellation, not the overrun.

    The transaction is released before the final checkpoint, and that checkpoint is what
    delivers a cancellation which arrived while the exchange was shielded. Raising the stored
    overrun ahead of it instead would hand the caller a `TimeoutError`, so an
    `except TimeoutError` around the call would swallow a cancellation that must propagate.
    """
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def read_register() -> None:
      try:
        await backend.send_command("ch", "bw", "")
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise
      except TimeoutError as error:
        observed.append(f"timeout:{error}")
      else:
        observed.append("returned")

    async def answer_late() -> None:
      """Answer the stalled reply long after the budget below should have abandoned it.

      A backend that bounds the exchange never reaches this, because the budget expires
      first and the abandoned read is cancelled where it waits. It is here so a backend that
      applies no bound answers late and fails the assertions below, rather than hanging the
      run inside the shield where no guard can reach it.
      """
      await io.blocked_started.wait()
      await anyio.sleep(_REAL_TIME_STALL)
      io.release_blocked.set()

    async with backend:
      io.blocked_event = "read"
      # Shrunk so the shielded exchange expires on its own budget while its caller's
      # cancellation is already pending: the reply this double waits for arrives, if at all,
      # only long after that.
      with patch.object(cytomat_module, "_COMMAND_TIMEOUT", 0.05):
        # A `move_on_after` guard rather than `fail_after`, because a guard that raised
        # `TimeoutError` could not be told apart from the overrun this test is about.
        with anyio.move_on_after(_REAL_TIME_DEADLOCK_GUARD) as guard_scope:
          async with anyio.create_task_group() as late_reply_group:
            late_reply_group.start_soon(answer_late)
            async with anyio.create_task_group() as task_group:
              task_group.start_soon(read_register)
              # The write has committed and the exchange is shielded once the reply read has
              # begun, so the cancellation below cannot reach the caller until the exchange
              # is over and its overrun has been stored.
              await io.blocked_started.wait()
              task_group.cancel_scope.cancel()
            late_reply_group.cancel_scope.cancel()

      # Otherwise the assertions below could be satisfied by a run that never got this far.
      assert not guard_scope.cancelled_caught
      io.blocked_event = None

    assert observed == ["cancelled"]

  async def test_command_overrun_reports_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.read_stall = _REAL_TIME_STALL
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with patch.object(cytomat_module, "_COMMAND_TIMEOUT", 0.05):
          with pytest.raises(TimeoutError) as error_info:
            await backend.send_command("ch", "bw", "")
      io.read_stall = 0.0

    assert str(error_info.value) == "Timed out waiting for a response to 'ch:bw'"

  def test_command_timeout_is_the_sum_of_the_transport_budgets(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    _, io = make_backend(monkeypatch)

    # Both against the transport budgets it replaces, so one deadline can never make a command
    # that used to succeed newly fail, and against its literal value, so the two cannot be
    # moved together.
    assert cytomat_module._COMMAND_TIMEOUT == (
      io.serial_kwargs["write_timeout"] + io.serial_kwargs["timeout"]
    )
    assert cytomat_module._COMMAND_TIMEOUT == 2.0

  async def test_dispatch_is_published_after_the_write_and_before_the_reply(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async def read_register() -> None:
      await backend.send_command("ch", "bw", "")

    async with backend:
      io.blocked_event = "read"
      dispatched_before = backend._commands_dispatched
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_register)
          await io.blocked_started.wait()
          # The write has returned and the reply has not been interpreted, and ownership of
          # the dispatched command is already published.
          assert backend._commands_dispatched == dispatched_before + 1
          io.blocked_event = None
          io.release_blocked.set()

  async def test_a_cancelled_transaction_waiter_publishes_no_dispatch(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async def blocked_command() -> None:
      await backend.send_command("ch", "bw", "")

    async def cancelled_command() -> None:
      await backend.send_command("ch", "be", "")

    async with backend:
      io.blocked_event = "read"
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as holder_group:
          holder_group.start_soon(blocked_command)
          await io.blocked_started.wait()
          dispatched_before = backend._commands_dispatched
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancelled_command)
            await _settle()
            task_group.cancel_scope.cancel()
          assert backend._commands_dispatched == dispatched_before
          io.blocked_event = None
          io.release_blocked.set()


class TestCytomatCommandDeadline(AnyioTestBase):
  """Observe the command budget on a clock that costs no real time.

  A blocked exchange is abandoned exactly when its budget expires, so the time between
  dispatch and abandonment *is* the budget the backend applied. That budget is a cancel-scope
  deadline, which resolves its clock through the running backend, so only a backend-native
  virtual clock can advance it without real waiting. Trio supplies one; asyncio has no
  packaged equivalent, so this observation runs trio-only. The budget is backend independent,
  and dual-backend coverage of overrunning it lives in
  :class:`TestCytomatCommandTransactions`.
  """

  # This class-level clock is shared by every node in the class and its virtual time
  # accumulates across them, so the assertions below measure intervals rather than absolute
  # instants.
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_one_budget_covers_the_write_and_the_read(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      # The write consumes most of the budget before the read even begins. One deadline over
      # the whole exchange therefore expires while the read waits; a budget per phase would
      # restart for the read and take nearly twice as long.
      io.write_stall = cytomat_module._COMMAND_TIMEOUT - 0.5
      io.read_stall = _STALLED_SECONDS
      start = anyio.current_time()
      with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Timed out waiting for a response to 'ch:bw'"):
          await backend.send_command("ch", "bw", "")
      elapsed = anyio.current_time() - start
      io.write_stall = 0.0
      io.read_stall = 0.0

    assert elapsed == pytest.approx(cytomat_module._COMMAND_TIMEOUT, abs=_DEADLINE_SLACK)
    assert elapsed == pytest.approx(2.0, abs=_DEADLINE_SLACK)


class TestCytomatRetries(AnyioTestBase):
  async def test_a_busy_command_is_retried_until_it_is_accepted(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(
      monkeypatch, replies={"ch:bw": ["er 01"] * 9 + [f"bw {ErrorRegister.NO_ERROR.value}"]}
    )

    async with backend:
      baseline = len(io.commands)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        assert await backend.send_command("ch", "bw", "") == "00"

    # Nine rejected attempts, each followed by the error-register reset the protocol requires,
    # and a tenth that is accepted.
    assert io.commands[baseline:] == ["ch:bw", "rs:be"] * 9 + ["ch:bw"]

  async def test_ten_rejected_attempts_reset_the_register_and_re_raise(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch, replies={"ch:bw": ["er 01"] * 10})

    async with backend:
      baseline = len(io.commands)
      start = anyio.current_time()
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with pytest.raises(CytomatBusyError):
          await backend.send_command("ch", "bw", "")
      elapsed = anyio.current_time() - start

    assert io.commands[baseline:] == ["ch:bw", "rs:be"] * 10 + ["rs:be"]
    # Every rejected attempt is followed by its own gap, including the last one, so ten
    # attempts cost ten gaps of a tenth of a second each.
    assert elapsed == pytest.approx(1.0, abs=_REAL_TIME_SLACK)

  async def test_the_overview_register_is_retried_before_it_gives_up(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      # Every attempt at the overview register is rejected as busy, so the register loop
      # exhausts its own tries rather than the command loop's.
      io.replies["ch:bs"] = deque(["er 01"] * 200)
      baseline = len(io.commands)
      start = anyio.current_time()
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with pytest.raises(CytomatCommandUnknownError, match="Could not get overview register"):
          await backend.get_overview_register()
      elapsed = anyio.current_time() - start
      io.replies.pop("ch:bs")

    # Ten register tries, each one a complete ten-attempt command with its resets, and one
    # final reset before the register loop gives up.
    assert io.commands[baseline:] == (["ch:bs", "rs:be"] * 10 + ["rs:be"]) * 10 + ["rs:be"]
    # Ten command loops of ten gaps each, plus the register loop's own ten gaps.
    assert elapsed == pytest.approx(11.0, abs=_REAL_TIME_SLACK)


class TestCytomatTaskCompletion(AnyioTestBase):
  async def test_a_zero_timeout_still_polls_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:bs"] = deque([f"bs {_BUSY}"] * 4)
      baseline = len(io.commands)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError) as error_info:
          await backend.wait_for_task_completion(timeout=0)
      io.replies.pop("ch:bs")

    # The elapsed time is checked after the poll, so a zero budget still asks the device once.
    assert io.commands[baseline:] == ["ch:bs"]
    assert str(error_info.value) == "Cytomat did not complete task in time"

  async def test_polling_continues_until_the_busy_bit_clears(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:bs"] = deque([f"bs {_BUSY}", f"bs {_IDLE}"])
      baseline = len(io.commands)
      start = anyio.current_time()
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        state = await backend.wait_for_task_completion(timeout=60)
      elapsed = anyio.current_time() - start

    assert not state.busy_bit_set
    assert io.commands[baseline:] == ["ch:bs", "ch:bs"]
    # One poll interval separates the two polls.
    assert elapsed == pytest.approx(1.0, abs=_REAL_TIME_SLACK)

  async def test_a_set_error_bit_raises_the_mapped_error(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:bs"] = deque([f"bs {_ERROR_SET}"])
      io.replies["ch:be"] = deque([f"be {ErrorRegister.PROCESS_TIMEOUT.value}"])
      baseline = len(io.commands)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        with pytest.raises(CytomatProcessTimeoutError):
          await backend.wait_for_task_completion(timeout=60)

    assert io.commands[baseline:] == ["ch:bs", "ch:be", "rs:be"]

  async def test_an_idle_device_is_not_polled_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      baseline = len(io.commands)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        await backend.wait_for_task_completion(timeout=60)

    assert io.commands[baseline:] == ["ch:bs"]


class TestCytomatTaskCompletionClock(AnyioTestBase):
  """Observe the completion poll interval on a clock that costs no real time.

  Dual-backend coverage of the same interval lives in :class:`TestCytomatTaskCompletion`; this
  observation pins the exact value without spending it.
  """

  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_the_poll_interval_is_one_second(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:bs"] = deque([f"bs {_BUSY}"] * 3 + [f"bs {_IDLE}"])
      start = anyio.current_time()
      with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
        await backend.wait_for_task_completion(timeout=60)
      elapsed = anyio.current_time() - start

    assert elapsed == pytest.approx(3.0, abs=_DEADLINE_SLACK)


class TestCytomatCommittedActions(AnyioTestBase):
  async def test_the_completion_poll_is_not_abandoned_by_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def open_door() -> None:
      try:
        await backend.open_door()
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      io.replies["ch:bs"] = deque([f"bs {_BUSY}", f"bs {_IDLE}"])
      baseline = len(io.commands)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(open_door)
          # The action write has committed once its reply has been read, so the poll below
          # is the device's own completion, which cancellation must not abandon.
          while io.commands[baseline:] != ["ll:gp 002", "ch:bs"]:
            await _settle()
          task_group.cancel_scope.cancel()

      assert observed == ["cancelled"]
      # The device was still busy when the cancellation arrived, so the poll ran to the end.
      assert io.commands[baseline:] == ["ll:gp 002", "ch:bs", "ch:bs"]

  async def test_a_cancellation_after_dispatch_still_reconciles_the_action(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def open_door() -> None:
      try:
        await backend.open_door()
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.commands)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(open_door)
          await io.blocked_started.wait()
          # The action write has reached the wire and its reply has not been interpreted.
          task_group.cancel_scope.cancel()
          io.blocked_event = None
          io.release_blocked.set()

      assert observed == ["cancelled"]
      # The write may have reached the device, so its completion is polled before the
      # cancellation propagates.
      assert io.commands[baseline:] == ["ll:gp 002", "ch:bs"]

  async def test_a_cancelled_transaction_waiter_polls_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    observed: List[str] = []

    async def blocked_command() -> None:
      await backend.send_command("ch", "bw", "")

    async def open_door() -> None:
      try:
        await backend.open_door()
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      io.blocked_event = "read"
      baseline = len(io.commands)
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as holder_group:
          holder_group.start_soon(blocked_command)
          await io.blocked_started.wait()
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(open_door)
            await _settle()
            task_group.cancel_scope.cancel()
          io.blocked_event = None
          io.release_blocked.set()

      assert observed == ["cancelled"]
      # Nothing was dispatched, so there is no action to reconcile and no poll is owed.
      assert io.commands[baseline:] == ["ch:bw"]

  async def test_an_unrelated_dispatch_does_not_make_a_cancelled_waiter_poll(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)
    transaction_lock = SecondAcquireObservedLock()
    observed: List[str] = []

    async def unrelated_command() -> None:
      await backend.send_command("ch", "bw", "")

    async def open_door() -> None:
      try:
        await backend.open_door()
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise

    async with backend:
      backend._transaction_lock = cast(anyio.Lock, transaction_lock)
      io.blocked_event = _write_event("ch:bw")
      baseline = len(io.commands)
      with anyio.move_on_after(_REAL_TIME_DEADLOCK_GUARD) as guard_scope:
        async with anyio.create_task_group() as holder_group:
          holder_group.start_soon(unrelated_command)
          await io.blocked_started.wait()

          async with anyio.create_task_group() as action_group:
            action_group.start_soon(open_door)
            await transaction_lock.second_acquire_attempted.wait()

            write_release = io.release_blocked
            io.blocked_event = "read"
            io.blocked_started = anyio.Event()
            io.release_blocked = anyio.Event()
            write_release.set()
            await io.blocked_started.wait()

            # Only the unrelated write has returned; the action is still waiting for the lock.
            assert io.commands[baseline:] == ["ch:bw"]
            action_group.cancel_scope.cancel()
            io.release_blocked.set()

      assert not guard_scope.cancelled_caught

    assert observed == ["cancelled"]
    assert io.commands[baseline:] == ["ch:bw"]


class TestCytomatWireFormat(AnyioTestBase):
  def test_carriage_return_is_per_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
    older, _ = make_backend(monkeypatch, model=CytomatType.C2C_425)
    newer, _ = make_backend(monkeypatch, model=CytomatType.C2C_450_SHAKE)

    assert older._assemble_command("ll", "in", "") == "ll:in\r"
    assert newer._assemble_command("ll", "in", "") == "ll:in\r\n"
    assert newer._assemble_command("mv", "ts", "001") == "mv:ts 001\r\n"

  async def test_action_commands_are_unchanged(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)

    async with backend:
      await backend.open_door()
      await backend.close_door()
      await backend.shovel_in()
      await backend.shovel_out()
      await backend.action_wait_to_transfer()
      await backend.action_transfer_to_wait()
      await backend.action_wait_to_exposed()
      await backend.action_exposed_to_wait()
      await backend.init_shakers()

    assert backend.commands == [
      ("ll", "gp", "002"),
      ("ch", "bs", ""),
      ("ll", "gp", "001"),
      ("ch", "bs", ""),
      ("ll", "sp", "001"),
      ("ch", "bs", ""),
      ("ll", "sp", "002"),
      ("ch", "bs", ""),
      ("mv", "wt", ""),
      ("ch", "bs", ""),
      ("mv", "tw", ""),
      ("ch", "bs", ""),
      ("mv", "wh", ""),
      ("ch", "bs", ""),
      ("mv", "hw", ""),
      ("ch", "bs", ""),
      ("ll", "vi", ""),
    ]

  async def test_register_getters_are_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:it"] = deque(["it +37.0 +36.5"])
      baseline = len(io.commands)
      assert await backend.get_temperature() == 36.5
      await backend.get_error_register()
      await backend.get_warning_register()
      await backend.reset_error_register()

    assert io.commands[baseline:] == ["ch:it", "ch:be", "ch:bw", "rs:be"]

  async def test_a_telegram_structure_error_reads_the_error_register(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:bw"] = deque(["er 03"])
      io.replies["ch:be"] = deque([f"be {ErrorRegister.PROCESS_TIMEOUT.value}"])
      baseline = len(io.commands)
      with pytest.raises(CytomatTelegramStructureError, match="Telegram structure error"):
        await backend.send_command("ch", "bw", "")

    assert io.commands[baseline:] == ["ch:bw", "ch:be", "rs:be"]

  async def test_an_unknown_error_code_resets_the_register(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:bw"] = deque(["er 99"])
      baseline = len(io.commands)
      with pytest.raises(Exception, match="Unknown cytomat error code in response"):
        await backend.send_command("ch", "bw", "")

    assert io.commands[baseline:] == ["ch:bw", "rs:be"]

  async def test_an_empty_reply_reports_a_silent_device(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = make_backend(monkeypatch)

    async with backend:
      io.replies["ch:bw"] = deque([""])
      with pytest.raises(RuntimeError, match="is it turned on"):
        await backend.send_command("ch", "bw", "")

  async def test_setting_racks_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _ = make_backend(monkeypatch)

    with pytest.warns(UserWarning, match="configured with the exe software"):
      await backend.set_racks([])

  def test_the_deprecated_alias_still_refuses_construction(self) -> None:
    with pytest.raises(RuntimeError, match="`Cytomat` is deprecated"):
      cytomat_module.Cytomat()

  def test_unsupported_models_are_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Unsupported Cytomat model"):
      make_backend(monkeypatch, model=cast(CytomatType, "not-a-cytomat"))


class TestCytomatShakingScope(AnyioTestBase):
  def test_preserves_public_signatures(self) -> None:
    start = inspect.signature(CytomatBackend.start_shaking).parameters
    assert tuple(start) == ("self", "frequency", "shakers")
    assert start["frequency"].annotation is float
    assert start["frequency"].default is inspect.Parameter.empty
    assert start["shakers"].annotation == Optional[List[int]]
    assert start["shakers"].default is None
    assert tuple(inspect.signature(CytomatBackend.stop_shaking).parameters) == ("self",)

  def test_the_command_double_inherits_its_managed_methods(self) -> None:
    # A later migration of the parent cannot break this file's collection silently.
    assert SoftwareCytomat.setup is HasLifespan.setup
    assert SoftwareCytomat.stop is HasLifespan.stop

  async def test_durable_start_and_stop_preserve_status_results(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)

    async with backend:
      start_status = await backend.start_shaking(10, shakers=[1])
      stop_status = await backend.stop_shaking()

    assert start_status == "00000000"
    assert stop_status == "00000000"

  async def test_selected_shakers_receive_exact_frequency_then_global_start_stop(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)

    async with backend:
      async with backend.shaking(12, shakers=[2, 1]):
        assert backend._shaking_active

    assert backend.commands == [
      ("se", "pb 21", "0012"),
      ("se", "pb 20", "0012"),
      ("ll", "va", ""),
      ("ll", "vd", ""),
    ]

  async def test_all_selected_indexes_are_validated_before_any_command(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)

    async with backend:
      with pytest.raises(AssertionError, match="Shaker index"):
        async with backend.shaking(10, shakers=[1, 3]):
          pytest.fail("invalid shaker reached body")

    assert backend.commands == []

  async def test_c5c_rejects_scope_before_any_command(self) -> None:
    backend = SoftwareCytomat(CytomatType.C5C)

    async with backend:
      with pytest.raises(NotImplementedError, match="not supported"):
        async with backend.shaking(10):
          pytest.fail("unsupported model reached body")

    assert backend.commands == []

  async def test_all_scopes_conflict_even_when_shaker_lists_do_not_overlap(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)

    async with backend:
      async with backend.shaking(10, shakers=[1]):
        with pytest.raises(RuntimeError, match="already scoped"):
          async with backend.shaking(20, shakers=[2]):
            pytest.fail("second global shaking scope reached body")

  async def test_set_frequency_is_rejected_before_mutation_while_scoped(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)

    async with backend:
      async with backend.shaking(10, shakers=[1]):
        commands_before_rejection = list(backend.commands)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.set_shaking_frequency(20, shakers=[2])
        assert backend.commands == commands_before_rejection

  async def test_direct_start_and_stop_are_rejected_before_mutation_while_scoped(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)

    async with backend:
      async with backend.shaking(10, shakers=[1]):
        commands_before_rejections = list(backend.commands)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.start_shaking(20, shakers=[2])
        with pytest.raises(RuntimeError, match="scope"):
          await backend.stop_shaking()
        assert backend.commands == commands_before_rejections

  async def test_start_cancellation_uses_global_stop_before_task_finishes(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)
    start = ("ll", "va", "")
    backend.entered[start] = anyio.Event()
    backend.releases[start] = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend.shaking(10, shakers=[2]):
            pytest.fail("cancelled Cytomat start reached body")
      finally:
        finished.set()

    async with backend:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await backend.entered[start].wait()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not finished.is_set()
        backend.releases[start].set()
        await finished.wait()

    assert backend.commands == [
      ("se", "pb 21", "0010"),
      ("ll", "va", ""),
      ("ll", "vd", ""),
    ]

  async def test_failed_global_stop_retains_state_for_direct_retry(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)
    stop = ("ll", "vd", "")
    backend.failures[stop] = [RuntimeError("stop failed")]

    async with backend:
      with pytest.raises(RuntimeError, match="stop failed"):
        async with backend.shaking(10, shakers=[1]):
          pass
      assert backend._shaking_active
      assert backend._shaking_state_unknown
      assert backend._shaking_scope_token is None
      await backend.stop_shaking()
      assert not backend._shaking_active
      assert not backend._shaking_state_unknown

    assert backend.commands.count(stop) == 2

  async def test_direct_parent_cleanup_stops_and_invalidates_live_scope(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)
    lifespan = backend.lifespan()
    scope = backend.shaking(10, shakers=[1])

    await lifespan.__aenter__()
    await scope.__aenter__()
    await lifespan.__aexit__(None, None, None)
    assert backend._shaking_scope_token is None
    stop_count = backend.commands.count(("ll", "vd", ""))
    await scope.__aexit__(None, None, None)

    assert stop_count == 1
    assert backend.commands.count(("ll", "vd", "")) == stop_count

  async def test_frontend_and_cytomat_direct_scope_share_global_owner(self) -> None:
    backend = SoftwareCytomat(CytomatType.C2C_450_SHAKE)
    frontend = make_frontend(backend)

    async with frontend:
      async with frontend.shaking(10):
        with pytest.raises(RuntimeError, match="already scoped"):
          async with backend.shaking(20, shakers=[2]):
            pytest.fail("Cytomat direct API created a second token")


class TestCytomatChatterboxShaking(AnyioTestBase):
  async def test_direct_lifespan_cleanup_invalidates_scope_and_allows_reuse(self, capsys) -> None:
    backend = CytomatChatterbox(model=CytomatType.C2C_450_SHAKE, port="COM_CYTOMAT")
    lifespan = backend.lifespan()
    stale_scope = backend.shaking(10, shakers=[1])

    await lifespan.__aenter__()
    await stale_scope.__aenter__()
    capsys.readouterr()
    await lifespan.__aexit__(None, None, None)
    parent_output = capsys.readouterr().out
    assert backend._shaking_scope_token is None
    assert not backend._shaking_active
    assert "cytomat ll:vd" in parent_output
    assert "closing connection to cytomat" in parent_output
    assert parent_output.index("cytomat ll:vd") < parent_output.index(
      "closing connection to cytomat"
    )

    await stale_scope.__aexit__(None, None, None)
    assert capsys.readouterr().out == ""

    async with backend:
      async with backend.shaking(20, shakers=[2]):
        assert backend._shaking_active
    reuse_output = capsys.readouterr().out
    assert "cytomat se:pb 21 0020" in reuse_output
    assert "cytomat ll:vd" in reuse_output

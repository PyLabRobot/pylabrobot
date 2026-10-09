import re
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple, cast
from unittest.mock import patch

import anyio
import pytest
from trio.testing import MockClock

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.usb import USB
from pylabrobot.legacy.heating_shaking import HeaterShaker
from pylabrobot.legacy.heating_shaking import hamilton_backend as hamilton_module
from pylabrobot.legacy.heating_shaking.hamilton_backend import (
  HamiltonHeaterShakerBackend,
  HamiltonHeaterShakerBox,
  HamiltonHeaterShakerInterface,
  PlateLockPosition,
)
from pylabrobot.legacy.shaking import Shaker
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

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

# Deadlock guard for the virtual-clock deadline tests. Ordered bound < guard < stall, so an
# overlong bound is reported by the elapsed-time assertions rather than by the guard, while a
# bound that never fires trips the guard instead of hanging the run.
_VIRTUAL_DEADLOCK_GUARD = 7200.0

# Slack allowed around a deadline observed on a virtual clock.
_DEADLINE_SLACK = 0.05

# The framing every heater-shaker command shares: a module index, a two-letter command, a
# four-digit id, and the command's own arguments.
_FRAME = re.compile(r"^T(?P<index>\d+)(?P<command>[A-Z]{2})id(?P<id>\d{4})(?P<args>.*)$")

# Reply payloads the device appends to the echoed frame, keyed by command. Every other command
# falls back to the plain acknowledgement below.
_DEFAULT_PAYLOADS = {"RD": "rd1", "RT": "rt+0250 +0260"}

_ACKNOWLEDGEMENT = "er00"


def _frame_of(data: bytes) -> Tuple[str, str, str]:
  """Return the index, command, and id a written frame carries."""
  match = _FRAME.match(data.decode("utf-8"))
  assert match is not None, f"unframed command {data!r}"
  return match["index"], match["command"], match["id"]


def _command_of(data: bytes) -> str:
  """Return a written frame without its generated id, for transcript assertions.

  The id changes on every command, so a transcript keyed by it could not be written down.
  It is pinned separately, against the frames themselves.
  """
  match = _FRAME.match(data.decode("utf-8"))
  assert match is not None, f"unframed command {data!r}"
  return f"T{match['index']}{match['command']}{match['args']}"


def _write_event(command: str) -> str:
  """Return the recorded event of one command write."""
  return f"write:{command}"


def _exchange_events(command: str) -> List[str]:
  """Return the recorded events of one complete command exchange."""
  return [_write_event(command), "read"]


class RecordingUSB(HasLifespan):
  """Software USB double recording lifecycle and wire events."""

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    close_error: Optional[BaseException] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    blocked_event: Optional[str] = None,
    payloads: Optional[Dict[str, str]] = None,
    rd_sequence: Optional[List[str]] = None,
    write_delay: float = 0.0,
    read_stall: float = 0.0,
  ) -> None:
    """Initialize a closed double.

    Args:
      events: Shared ordered event log, or None for a private one.
      entry_error: Exception to raise while the transport opens.
      close_error: Exception to raise when the lifespan closes the transport.
      failures: Exception to raise from the named event, keyed by event.
      blocked_event: Event name whose call waits for `release_blocked`.
      payloads: Reply payloads keyed by command, overriding the defaults.
      rd_sequence: Successive `RD` payloads; the last one repeats once consumed.
      write_delay: Seconds a command write takes after it has reached the wire.
      read_stall: Seconds a reply read waits before answering. Unlike `blocked_event`, this
        stall ends on its own, so a caller that never bounds the exchange answers late and
        fails a timing assertion rather than hanging the run.
    """
    super().__init__()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.close_error = close_error
    self.failures = failures or {}
    self.blocked_event = blocked_event
    self.payloads = dict(_DEFAULT_PAYLOADS)
    if payloads is not None:
      self.payloads.update(payloads)
    self.rd_sequence: Deque[str] = deque(rd_sequence or [])
    self.write_delay = write_delay
    self.read_stall = read_stall
    self.usb_kwargs: Dict[str, Any] = {}
    self.writes: List[bytes] = []
    self.commands: List[str] = []
    self.frames: List[Tuple[str, str, str]] = []
    self.write_times: List[float] = []
    self.read_times: List[float] = []
    self.entries = 0
    self.exits = 0
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingUSB does not accept lifespan options")
    self.entries += 1
    self.events.append("usb.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("usb.enter")

  async def _close(self) -> None:
    """Record the transport close and raise the injected close failure."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("usb.exit")
    if self.close_error is not None:
      raise self.close_error

  async def _block_if_requested(self, event: str) -> None:
    """Wait for release when this event is the configured blocking point."""
    if event == self.blocked_event:
      self.blocked_started.set()
      await self.release_blocked.wait()

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Record one command write at its entry, then block, delay, or fail as configured.

    The event is appended before this double awaits anything of its own. A transport call is
    the moment work reaches the wire, so recording it after a scheduler hop would report it
    one hop late and hide orderings the caller genuinely produces.
    """
    command = _command_of(data)
    self.writes.append(data)
    self.commands.append(command)
    self.frames.append(_frame_of(data))
    self.write_times.append(anyio.current_time())
    self.events.append(_write_event(command))
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested(_write_event(command))
    if self.write_delay > 0:
      await anyio.sleep(self.write_delay)
    failure = self.failures.get(_write_event(command))
    if failure is not None:
      raise failure

  async def read(self, timeout: Optional[int] = None, size: Optional[int] = None) -> bytes:
    """Record one reply read at its entry, then stall or block, and answer the last command."""
    self.read_times.append(anyio.current_time())
    self.events.append("read")
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("read")
    if self.read_stall > 0:
      await anyio.sleep(self.read_stall)
    failure = self.failures.get("read")
    if failure is not None:
      raise failure
    if not self.frames:
      return b""
    index, command, id_ = self.frames[-1]
    return f"T{index}{command}id{id_}{self._payload(command)}".encode("utf-8")

  def _payload(self, command: str) -> str:
    """Return the payload the device appends for one command."""
    if command == "RD" and self.rd_sequence:
      payload = self.rd_sequence[0]
      if len(self.rd_sequence) > 1:
        self.rd_sequence.popleft()
      return payload
    return self.payloads.get(command, _ACKNOWLEDGEMENT)


class RecordingInterface(HasLifespan, HamiltonHeaterShakerInterface):
  """Software double for a borrowed heater-shaker interface.

  It stands in for either a `HamiltonHeaterShakerBox` or a `STARBackend`: both are
  `HasLifespan` implementers of `send_hhs_command`, and neither is owned by the device
  backend that borrows it.
  """

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    blocked_command: Optional[str] = None,
    payloads: Optional[Dict[str, str]] = None,
    rd_sequence: Optional[List[str]] = None,
    stalled_commands: Optional[Dict[str, float]] = None,
  ) -> None:
    """Initialize a closed double.

    Args:
      events: Shared ordered event log, or None for a private one.
      failures: Exception to raise from the named command, keyed by command letters.
      blocked_command: Command whose call waits for `release_blocked`.
      payloads: Reply payloads keyed by command, overriding the defaults.
      rd_sequence: Successive `RD` payloads; the last one repeats once consumed.
      stalled_commands: Seconds one command waits before answering, keyed by command. The
        stall is finite, so a caller that never bounds its cleanup answers late and fails an
        assertion rather than hanging the run.
    """
    super().__init__()
    self.events = events if events is not None else []
    self.failures = failures or {}
    self.blocked_command = blocked_command
    self.payloads = dict(_DEFAULT_PAYLOADS)
    if payloads is not None:
      self.payloads.update(payloads)
    self.rd_sequence: Deque[str] = deque(rd_sequence or [])
    self.stalled_commands = stalled_commands or {}
    self.commands: List[str] = []
    self.entries = 0
    self.exits = 0
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingInterface does not accept lifespan options")
    self.entries += 1
    self.events.append("interface.enter")
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    """Record that the interface itself was closed."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("interface.exit")

  async def send_hhs_command(self, index: int, command: str, **kwargs: Any) -> str:
    """Record one command at its entry, then block, stall, or fail as configured."""
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])
    text = f"T{index}{command}{args}"
    self.commands.append(text)
    self.events.append(_write_event(text))
    await anyio.lowlevel.checkpoint()
    if command == self.blocked_command:
      self.blocked_started.set()
      await self.release_blocked.wait()
    stall = self.stalled_commands.get(command)
    if stall is not None:
      await anyio.sleep(stall)
    failure = self.failures.get(command)
    if failure is not None:
      raise failure
    return f"T{index}{command}id0001{self._payload(command)}"

  def _payload(self, command: str) -> str:
    """Return the payload the device appends for one command."""
    if command == "RD" and self.rd_sequence:
      payload = self.rd_sequence[0]
      if len(self.rd_sequence) > 1:
        self.rd_sequence.popleft()
      return payload
    return self.payloads.get(command, _ACKNOWLEDGEMENT)


class PlainInterface(HamiltonHeaterShakerInterface):
  """An interface that implements the wire API but owns no lifespan.

  The abstract interface declares only `send_hhs_command`, so an object like this satisfies
  it while offering no way to tell whether its transport is open.
  """

  def __init__(self) -> None:
    self.commands: List[str] = []

  async def send_hhs_command(self, index: int, command: str, **kwargs: Any) -> str:
    """Record one command and answer it."""
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])
    self.commands.append(f"T{index}{command}{args}")
    await anyio.lowlevel.checkpoint()
    return f"T{index}{command}id0001{_ACKNOWLEDGEMENT}"


def _make_box(
  monkeypatch: pytest.MonkeyPatch,
  **double_kwargs: Any,
) -> Tuple[HamiltonHeaterShakerBox, RecordingUSB]:
  """Build a control box whose transport is a software USB double."""
  io = RecordingUSB(**double_kwargs)

  def build_usb(**kwargs: Any) -> USB:
    io.usb_kwargs = dict(kwargs)
    return cast(USB, io)

  monkeypatch.setattr(hamilton_module, "USB", build_usb)
  return HamiltonHeaterShakerBox(), io


def _make_backend(
  index: int = 1,
  **double_kwargs: Any,
) -> Tuple[HamiltonHeaterShakerBackend, RecordingInterface]:
  """Build a device backend borrowing a software interface double."""
  interface = RecordingInterface(**double_kwargs)
  return HamiltonHeaterShakerBackend(index=index, interface=interface), interface


def _make_heater_shaker(
  backend: HamiltonHeaterShakerBackend, name: str = "test_hhs"
) -> HeaterShaker:
  """Wrap the backend in the frontend that owns its shutdown actions."""
  return HeaterShaker(
    name=name,
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


def _make_shaker(backend: HamiltonHeaterShakerBackend, name: str = "test_hhs_shaker") -> Shaker:
  """Wrap the backend in the Wave 12 shaking owner."""
  return Shaker(
    name=name,
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


def _wire_events(io: RecordingUSB, baseline: int, *markers: str) -> List[str]:
  """Return the transport events recorded since `baseline`, without the test's own markers.

  Tests append their own markers to the shared log to place an observation in the ordering.
  This drops them again, so an assertion about what reached the wire reads only wire events:
  `write:*`, `read`, `usb.enter`, and `usb.exit`.
  """
  return [event for event in io.events[baseline:] if event not in markers]


async def _settle() -> None:
  """Give every other task room to reach its next transport call."""
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


class TestHamiltonHeaterShakerBoxLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert HamiltonHeaterShakerBox.__aenter__ is HasLifespan.__aenter__
    assert HamiltonHeaterShakerBox.__aexit__ is HasLifespan.__aexit__
    assert HamiltonHeaterShakerBox.setup is HasLifespan.setup
    assert HamiltonHeaterShakerBox.stop is HasLifespan.stop

  async def test_usb_configuration_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _, io = _make_box(monkeypatch)

    assert io.usb_kwargs == {
      "human_readable_device_name": "Hamilton Heater Shaker Box",
      "id_vendor": 0x8AF,
      "id_product": 0x8002,
      "device_address": None,
      "serial_number": None,
    }

  async def test_usb_configuration_forwards_constructor_arguments(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    io = RecordingUSB()

    def build_usb(**kwargs: Any) -> USB:
      io.usb_kwargs = dict(kwargs)
      return cast(USB, io)

    monkeypatch.setattr(hamilton_module, "USB", build_usb)
    HamiltonHeaterShakerBox(
      id_vendor=0x1234, id_product=0x5678, device_address=7, serial_number="SN1"
    )

    assert io.usb_kwargs == {
      "human_readable_device_name": "Hamilton Heater Shaker Box",
      "id_vendor": 0x1234,
      "id_product": 0x5678,
      "device_address": 7,
      "serial_number": "SN1",
    }

  async def test_rejects_lifespan_options_before_usb_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    box, io = _make_box(monkeypatch)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with box.lifespan(unknown=True):
        pytest.fail("invalid options reached the body")

    assert io.events == []
    assert io.entries == 0

  async def test_entry_opens_usb_and_issues_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, io = _make_box(monkeypatch)

    async with box:
      assert io.events == ["usb.enter"]

    # The box owns the transport and nothing else: entry opens it and issues no command, and
    # exit closes it and issues no command.
    assert io.events == ["usb.enter", "usb.exit"]
    assert io.commands == []
    assert io.entries == 1
    assert io.exits == 1

  async def test_usb_entry_failure_does_not_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("usb entry failed")
    box, io = _make_box(monkeypatch, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with box:
        pytest.fail("failed USB entry reached the body")

    assert error_info.value is failure
    assert io.events == ["usb.enter"]
    assert io.exits == 0
    assert not box.is_active

  async def test_entry_cancellation_closes_usb(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, io = _make_box(monkeypatch, blocked_event="usb.enter")
    scopes: List[anyio.CancelScope] = []

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with box:
          pytest.fail("cancelled entry reached the body")

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter)
        await io.blocked_started.wait()
        scopes[0].cancel()

    assert io.events[-1] == "usb.exit"
    assert io.exits == 1
    assert io.commands == []
    assert not box.is_active

  async def test_body_cancellation_closes_usb(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, io = _make_box(monkeypatch)
    body_started = anyio.Event()

    async def run() -> None:
      try:
        async with box:
          await box.send_hhs_command(index=1, command="TO")
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        await body_started.wait()
        task_group.cancel_scope.cancel()

    assert io.events == ["usb.enter", *_exchange_events("T1TO"), "usb.exit", "cancelled"]
    assert io.exits == 1
    assert not box.is_active

  async def test_close_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("usb close failed")
    box, io = _make_box(monkeypatch, close_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with box:
        pass

    assert error_info.value is failure
    assert io.events == ["usb.enter", "usb.exit"]
    assert not box.is_active

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, io = _make_box(monkeypatch)

    async with box:
      await box.send_hhs_command(index=1, command="LI")
    async with box:
      await box.send_hhs_command(index=1, command="SI")

    assert io.events == [
      "usb.enter",
      *_exchange_events("T1LI"),
      "usb.exit",
      "usb.enter",
      *_exchange_events("T1SI"),
      "usb.exit",
    ]
    assert io.entries == 2
    assert io.exits == 2


class TestHamiltonHeaterShakerBoxWireFormat(AnyioTestBase):
  """Pin the bytes the device sees, which this migration preserves."""

  async def test_command_frames_are_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, io = _make_box(monkeypatch)

    async with box:
      await box.send_hhs_command(index=1, command="LI")
      await box.send_hhs_command(index=2, command="SB", st=0, sv="0800", sr="01000")
      await box.send_hhs_command(index=1, command="TA", ta="0370")

    # The id is four digits, generated per command and increasing in write order; the rest of
    # the frame is the module index, the command, and its keyword arguments in order.
    assert io.writes == [
      b"T1LIid0001",
      b"T2SBid0002st0sv0800sr01000",
      b"T1TAid0003ta0370",
    ]

  async def test_response_is_decoded_as_utf8(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, _ = _make_box(monkeypatch)

    async with box:
      assert await box.send_hhs_command(index=1, command="RT") == "T1RTid0001rt+0250 +0260"

  def test_generated_ids_wrap_at_ten_thousand(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, _ = _make_box(monkeypatch)

    ids = [box._generate_id() for _ in range(10_002)]

    assert ids[:3] == [1, 2, 3]
    assert ids[9_998] == 9_999
    assert ids[9_999] == 0
    assert ids[10_001] == 2


class TestHamiltonHeaterShakerBoxTransactions(AnyioTestBase):
  async def test_transactions_do_not_interleave(self, monkeypatch: pytest.MonkeyPatch) -> None:
    box, io = _make_box(monkeypatch)

    async with box:
      io.blocked_event = "read"
      baseline = len(io.events)

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(box.send_hhs_command, 1, "TO")
          await io.blocked_started.wait()
          task_group.start_soon(box.send_hhs_command, 1, "SC")
          await _settle()
          # The queued command may not reach the wire while the first exchange holds the
          # transaction.
          assert io.commands == ["T1TO"]
          io.release_blocked.set()

      assert io.events[baseline:] == [*_exchange_events("T1TO"), *_exchange_events("T1SC")]
      io.blocked_event = None

  async def test_cancelled_command_completes_its_exchange(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    box, io = _make_box(monkeypatch)
    cancelled = anyio.Event()
    completed = False
    scopes: List[anyio.CancelScope] = []

    async with box:
      io.blocked_event = "read"

      async def deactivate() -> None:
        nonlocal completed
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await box.send_hhs_command(index=1, command="TO")
          except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise
          completed = True

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(deactivate)
          await io.blocked_started.wait()
          scopes[0].cancel()
          await _settle()
          # The committed exchange is not abandoned: cancellation waits for the reply.
          assert not cancelled.is_set()
          io.release_blocked.set()

      assert cancelled.is_set()
      assert not completed
      io.blocked_event = None

  async def test_lock_is_released_before_pending_cancellation_propagates(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    box, io = _make_box(monkeypatch)
    scopes: List[anyio.CancelScope] = []

    async with box:
      io.blocked_event = "read"
      baseline = len(io.events)

      async def deactivate() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await box.send_hhs_command(index=1, command="TO")
          except anyio.get_cancelled_exc_class():
            io.events.append("T1TO.cancelled")
            raise
          pytest.fail("the cancelled command returned")

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(deactivate)
          await io.blocked_started.wait()
          scopes[0].cancel()
          task_group.start_soon(box.send_hhs_command, 1, "SC")
          await _settle()
          assert io.events[baseline:] == _exchange_events("T1TO")
          io.release_blocked.set()

      tail = io.events[baseline:]
      # The transaction is handed to the waiting command before the cancelled caller learns
      # it was cancelled, so a queued command is never delayed by one cancellation delivery.
      assert tail[:2] == _exchange_events("T1TO")
      assert tail[2] == _write_event("T1SC")
      assert "T1TO.cancelled" in tail[3:]
      # Where the cancellation delivery falls relative to the queued command's *reply read*
      # is not pinned, because it is a scheduler artifact rather than a property of the box:
      # measured, asyncio delivers it between that write and its read, trio after the read.
      # What both agree on, and what this test is for, is the two assertions above.
      assert _wire_events(io, baseline, "T1TO.cancelled") == [
        *_exchange_events("T1TO"),
        *_exchange_events("T1SC"),
      ]

      io.blocked_event = None

  async def test_command_cancelled_while_waiting_for_the_transaction_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A command cancelled while waiting for the transaction puts nothing on the wire.

    This pins the outcome, not one mechanism: the box stops such a waiter twice over, through
    a cancellable acquire and through the checkpoint that follows it, and either alone is
    enough. Shielding the acquire on its own therefore still passes here, as does dropping
    either checkpoint on its own. What this catches is losing the last stop standing:
    shielding the lock acquisition and then deleting the post-acquire checkpoint lets the
    cancelled waiter write, and no other test in this file observes that pair.
    """
    box, io = _make_box(monkeypatch)
    scopes: List[anyio.CancelScope] = []
    queued = anyio.Event()

    async with box:
      io.blocked_event = "read"
      baseline = len(io.events)

      async def stop_shaking() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          # Set before the call, so the wait below returns only once this task has started.
          # `Event.set` does not yield, so it says nothing about how far the task got; the
          # `_settle()` after the wait is what carries it to its blocking point, waiting for
          # the transaction.
          queued.set()
          try:
            await box.send_hhs_command(index=1, command="SC")
          except anyio.get_cancelled_exc_class():
            io.events.append("T1SC.cancelled")
            raise
          pytest.fail("the cancelled command returned")

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(box.send_hhs_command, 1, "TO")
          await io.blocked_started.wait()
          task_group.start_soon(stop_shaking)
          await queued.wait()
          await _settle()
          try:
            assert _wire_events(io, baseline, "T1SC.cancelled") == _exchange_events("T1TO")
            scopes[0].cancel()
            await _settle()
            # Cancelling a command that never owned the transaction cannot put its bytes on
            # the wire, neither while it waits nor when the transaction is handed on.
            assert _wire_events(io, baseline, "T1SC.cancelled") == _exchange_events("T1TO")
          finally:
            # Released even when an assertion above fails, because the held exchange is
            # shielded and would otherwise outlive the guard.
            io.release_blocked.set()

      assert "T1SC.cancelled" in io.events[baseline:]
      assert io.commands == ["T1TO"]
      assert _wire_events(io, baseline, "T1SC.cancelled") == _exchange_events("T1TO")

      io.blocked_event = None
      # It consumes no id either, because the id is generated inside the transaction: the next
      # command continues the sequence rather than skipping the number the waiter would have
      # taken had it been given one on the way in.
      await box.send_hhs_command(index=1, command="RD")
      assert [frame[2] for frame in io.frames] == ["0001", "0002"]

  async def test_command_overrun_reports_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
    # This test reads the overrun error's text rather than the bound's value, so it keeps both
    # backends on a real clock. The stall is a finite sleep, so a box that applies no bound at
    # all answers late and fails this test instead of hanging it.
    box, io = _make_box(monkeypatch, read_stall=_REAL_TIME_STALL)

    async with box:
      with patch.object(hamilton_module, "_COMMAND_TIMEOUT", 0.05):
        with pytest.raises(TimeoutError, match="Timed out waiting for response to 'SB'"):
          await box.send_hhs_command(index=1, command="SB", st=0, sv="0800", sr="01000")
      io.read_stall = 0.0

    assert io.commands == ["T1SBst0sv0800sr01000"]
    assert io.events[-1] == "usb.exit"

  async def test_overrun_yields_to_a_pending_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """An overrun exchange whose caller was cancelled reports the cancellation, not the overrun.

    The transaction is released before the final checkpoint, and that checkpoint is what
    delivers a cancellation which arrived while the exchange was shielded. Raising the stored
    overrun ahead of it instead would hand the caller a `TimeoutError`, so an
    `except TimeoutError` around the call would swallow a cancellation that must propagate.
    """
    box, io = _make_box(monkeypatch, blocked_event="read")
    observed: List[str] = []

    async def deactivate() -> None:
      try:
        await box.send_hhs_command(index=1, command="TO")
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise
      except TimeoutError as error:
        observed.append(f"timeout:{error}")
      else:
        observed.append("returned")

    async def answer_late() -> None:
      """Answer the stalled reply long after the budget below should have abandoned it.

      A box that bounds the exchange never reaches this, because the budget expires first
      and the abandoned read is cancelled where it waits. It is here so a box that applies
      no bound answers late and fails the assertions below, rather than hanging the run
      inside the shield where no guard can reach it.
      """
      await io.blocked_started.wait()
      await anyio.sleep(_REAL_TIME_STALL)
      io.release_blocked.set()

    async with box:
      # Shrunk so the shielded exchange expires on its own budget while its caller's
      # cancellation is already pending: the reply this double waits for arrives, if at all,
      # only long after that.
      with patch.object(hamilton_module, "_COMMAND_TIMEOUT", 0.05):
        # A `move_on_after` guard rather than `fail_after`, because a guard that raised
        # `TimeoutError` could not be told apart from the overrun this test is about.
        with anyio.move_on_after(_REAL_TIME_DEADLOCK_GUARD) as guard_scope:
          async with anyio.create_task_group() as late_reply_group:
            late_reply_group.start_soon(answer_late)
            async with anyio.create_task_group() as task_group:
              task_group.start_soon(deactivate)
              # The write has committed and the exchange is shielded once the reply read
              # has begun, so the cancellation below cannot reach the caller until the
              # exchange is over and its overrun has been stored.
              await io.blocked_started.wait()
              task_group.cancel_scope.cancel()
            late_reply_group.cancel_scope.cancel()

      # Otherwise the assertions below could be satisfied by a run that never got this far.
      assert not guard_scope.cancelled_caught
      io.blocked_event = None

    assert observed == ["cancelled"]
    assert io.commands == ["T1TO"]

  async def test_command_timeout_is_the_sum_of_the_transport_budgets(self) -> None:
    transport = USB(
      id_vendor=0x8AF, id_product=0x8002, human_readable_device_name="Hamilton Heater Shaker Box"
    )
    assert transport.read_timeout is None
    with pytest.warns(DeprecationWarning):
      legacy = USB(
        id_vendor=0x8AF,
        id_product=0x8002,
        human_readable_device_name="Hamilton Heater Shaker Box",
        read_timeout=30,
      )

    # Both against the transport budgets it replaces, so one deadline can never make a command
    # that used to succeed newly fail, and against its literal value, so the two cannot be
    # moved together.
    assert legacy.read_timeout is not None
    assert hamilton_module._COMMAND_TIMEOUT == legacy.write_timeout + legacy.read_timeout
    assert hamilton_module._COMMAND_TIMEOUT == 60.0


class TestHamiltonHeaterShakerBoxDeadline(AnyioTestBase):
  """Observe the command budget on a clock that costs no real time.

  A blocked exchange is abandoned exactly when its budget expires, so the time between
  dispatch and abandonment *is* the budget the box applied. That budget is a cancel-scope
  deadline, which resolves its clock through the running backend, so only a backend-native
  virtual clock can advance it without real waiting. Trio supplies one; asyncio has no
  packaged equivalent, so this observation runs trio-only. The budget is backend independent,
  and dual-backend coverage of overrunning it lives in
  :class:`TestHamiltonHeaterShakerBoxTransactions`.
  """

  # This class-level clock is shared by every node in the class and its virtual time
  # accumulates across them, so the assertions below measure intervals rather than absolute
  # instants.
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_one_budget_covers_the_write_and_the_read(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # The write consumes most of the budget before the read even begins. One deadline over the
    # whole exchange therefore expires while the read waits; a budget per phase would restart
    # for the read and take half as long again.
    write_delay = hamilton_module._COMMAND_TIMEOUT - 10.0
    box, io = _make_box(monkeypatch, write_delay=write_delay, read_stall=_STALLED_SECONDS)

    async with box:
      start = anyio.current_time()
      # The clock is virtual, so this guard costs no real time. A box that applies no bound
      # cannot be interrupted by it, because the exchange is shielded; it is instead released
      # by the stall itself, which is longer than the guard.
      with anyio.fail_after(_VIRTUAL_DEADLOCK_GUARD):
        with pytest.raises(TimeoutError, match="Timed out waiting for response to 'TO'"):
          await box.send_hhs_command(index=1, command="TO")
      elapsed = anyio.current_time() - start
      io.read_stall = 0.0
      io.write_delay = 0.0

    assert elapsed == pytest.approx(hamilton_module._COMMAND_TIMEOUT, abs=_DEADLINE_SLACK)
    assert elapsed == pytest.approx(60.0, abs=_DEADLINE_SLACK)


class TestHamiltonHeaterShakerBackendLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert HamiltonHeaterShakerBackend.__aenter__ is HasLifespan.__aenter__
    assert HamiltonHeaterShakerBackend.__aexit__ is HasLifespan.__aexit__
    assert HamiltonHeaterShakerBackend.setup is HasLifespan.setup
    assert HamiltonHeaterShakerBackend.stop is HasLifespan.stop

  async def test_rejects_lifespan_options_before_any_command(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      with pytest.raises(TypeError, match="unexpected keyword argument"):
        async with backend.lifespan(unknown=True):
          pytest.fail("invalid options reached the body")

    assert interface.commands == []

  async def test_entry_initializes_the_lock_then_the_shaker_drive(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        assert interface.commands == ["T1LI", "T1SI"]

  async def test_bare_backend_exit_issues_no_command(self) -> None:
    """A bare backend lifespan issues nothing at exit.

    The legacy `stop()` this migration replaces was literally `pass`, and the `HeaterShaker`
    frontend already owns deactivation and force-stopping shaking through `_pre_backend_stop`,
    while `Shaker.shaking` owns unlocking the plate. Registering any of those here would issue
    them twice on every frontend exit.
    """
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        entry = list(interface.commands)
      assert entry == ["T1LI", "T1SI"]
      assert interface.commands == entry
      assert interface.exits == 0

    assert interface.commands == ["T1LI", "T1SI"]

  async def test_bare_backend_exit_issues_no_command_after_shaking(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        await backend.start_shaking(speed=900)
        started = list(interface.commands)
      # Even after the device has been left shaking and heating, the backend adds nothing at
      # exit: that residual is exactly what the legacy `stop()` left behind.
      assert interface.commands == started

  async def test_requires_an_active_interface(self) -> None:
    backend, interface = _make_backend()

    with pytest.raises(RuntimeError, match="active"):
      async with backend:
        pytest.fail("an inactive interface reached the body")

    assert interface.commands == []
    assert not backend.is_active

  async def test_requires_an_interface_that_owns_a_lifespan(self) -> None:
    # The abstract interface declares only `send_hhs_command`, so an implementation can offer
    # no way to tell whether its transport is open. Both real implementers are `HasLifespan`,
    # and a borrowed interface that cannot report activity is rejected rather than trusted.
    interface = PlainInterface()
    backend = HamiltonHeaterShakerBackend(index=1, interface=cast(Any, interface))

    with pytest.raises(RuntimeError, match="active"):
      async with backend:
        pytest.fail("an interface without a lifespan reached the body")

    assert interface.commands == []
    assert not backend.is_active

  async def test_interface_closed_before_entry_is_rejected(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      pass

    with pytest.raises(RuntimeError, match="active"):
      async with backend:
        pytest.fail("a closed interface reached the body")

    assert interface.commands == []

  async def test_does_not_close_the_borrowed_interface(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        pass
      # The borrowed interface outlives the device backend that used it: several devices share
      # one box, so a child that closed it would cut off its siblings.
      assert interface.is_active
      assert interface.exits == 0
      assert interface.entries == 1

      async with backend:
        pass
      assert interface.is_active
      assert interface.entries == 1

    assert interface.exits == 1

  async def test_initialization_failure_leaves_the_interface_open(self) -> None:
    failure = RuntimeError("shaker drive init failed")
    backend, interface = _make_backend(failures={"SI": failure})

    async with interface:
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          pytest.fail("failed entry reached the body")

      assert error_info.value is failure
      assert interface.commands == ["T1LI", "T1SI"]
      assert interface.is_active
      assert interface.exits == 0
      assert not backend.is_active

  async def test_sequential_reuse(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        pass
      async with backend:
        pass

    assert interface.commands == ["T1LI", "T1SI", "T1LI", "T1SI"]

  async def test_index_must_be_non_negative(self) -> None:
    with pytest.raises(AssertionError, match="Shaker index must be non-negative"):
      HamiltonHeaterShakerBackend(index=-1, interface=RecordingInterface())

  async def test_serialize_is_unchanged(self) -> None:
    backend, _ = _make_backend(index=3)

    with pytest.warns(UserWarning, match="The interface is not serialized."):
      serialized = backend.serialize()

    assert serialized == {
      "type": "HamiltonHeaterShakerBackend",
      "index": 3,
      "interface": None,
    }

  async def test_capability_flags_are_unchanged(self) -> None:
    backend, _ = _make_backend()

    assert backend.supports_locking is True
    assert backend.supports_active_cooling is False


class TestHamiltonHeaterShakerFrontendComposition(AnyioTestBase):
  async def test_frontend_exit_issues_each_cleanup_once(self) -> None:
    # The frontend's `_pre_backend_stop` unconditionally deactivates temperature control and
    # force-stops shaking. The backend must not register those actions again: doing so issues
    # every one of them twice on every frontend exit.
    backend, interface = _make_backend()
    heater_shaker = _make_heater_shaker(backend)

    async with interface:
      async with heater_shaker:
        entry = list(interface.commands)
        assert entry == ["T1LI", "T1SI"]

      exit_commands = interface.commands[len(entry) :]

      # Measured, not assumed: `_pre_backend_stop` attempts `deactivate` (`TO`) and then the
      # force-stop (`SC`, `SW`), in that order. The plate lock is never touched at exit.
      assert exit_commands == ["T1TO", "T1SC", "T1SW"]
      assert exit_commands.count("T1TO") == 1
      assert exit_commands.count("T1SC") == 1
      assert exit_commands.count("T1SW") == 1
      assert not any(command.startswith("T1LP") for command in exit_commands)
      # Asserted inside the interface's own lifespan: the frontend and the backend beneath it
      # have both exited, and the borrowed interface is still open.
      assert interface.exits == 0
      assert interface.is_active

    # It closes only when its own owner leaves, one lifespan later.
    assert interface.exits == 1

  async def test_frontend_exit_after_shaking_issues_each_cleanup_once(self) -> None:
    backend, interface = _make_backend()
    heater_shaker = _make_heater_shaker(backend, name="shaken_hhs")

    async with interface:
      async with heater_shaker:
        await heater_shaker.shake(900)
        entry = list(interface.commands)

      exit_commands = interface.commands[len(entry) :]

    assert exit_commands == ["T1TO", "T1SC", "T1SW"]

  async def test_shaking_scope_owns_the_plate_unlock(self) -> None:
    # The plate unlock has an owner already, and it is not the backend lifespan: the Wave 12
    # shaking scope registers it whenever the backend supports locking.
    backend, interface = _make_backend()
    shaker = _make_shaker(backend)

    async with interface:
      async with shaker:
        async with shaker.shaking(900):
          assert interface.commands.count("T1LPlp0") == 0
        assert interface.commands.count("T1LPlp0") == 1
        assert interface.commands.count("T1SC") == 1
        assert interface.commands.count("T1SW") == 1
        scoped = list(interface.commands)

      # Measured against the pre-migration code, not assumed: leaving the shaker lifespan
      # after a scope that already stopped cleanly adds nothing at all. `Shaker`'s
      # `_pre_backend_stop` is guarded on tracked shaking state, unlike the unconditional
      # `HeaterShaker` one, and the scope cleared that state on its way out. So this slice is
      # empty only as long as the backend registers no lifespan cleanup of its own: a
      # registered `stop_shaking` or `unlock_plate` would show up right here.
      assert interface.commands[len(scoped) :] == []


class TestHamiltonHeaterShakerCommands(AnyioTestBase):
  async def test_command_transcript_is_unchanged(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        await backend.lock_plate()
        await backend.unlock_plate()
        await backend.set_temperature(37)
        assert await backend.get_current_temperature() == 25.0
        assert await backend.get_edge_temperature() == 26.0
        assert await backend.get_is_shaking() is True
        await backend.stop_shaking()
        await backend.deactivate()

    assert interface.commands == [
      "T1LI",
      "T1SI",
      "T1LPlp1",
      "T1LPlp0",
      "T1TAta0370",
      "T1RT",
      "T1RT",
      "T1RD",
      "T1SC",
      "T1SW",
      "T1TO",
    ]

  async def test_start_shaking_pads_speed_and_acceleration(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        await backend.start_shaking(speed=900, direction=1, acceleration=750)

    # Speed is padded to four digits and acceleration to five, in that keyword order.
    assert interface.commands[2:] == ["T1LPlp1", "T1SBst1sv0900sr00750", "T1RD"]

  async def test_temperature_parsing_is_unchanged(self) -> None:
    backend, interface = _make_backend(payloads={"RT": "rt+1234 -0567"})

    async with interface:
      async with backend:
        assert await backend._get_current_temperature() == {"middle": 123.4, "edge": -56.7}

  async def test_is_shaking_reads_the_last_character(self) -> None:
    backend, interface = _make_backend(payloads={"RD": "rd0"})

    async with interface:
      async with backend:
        assert await backend.get_is_shaking() is False

  async def test_set_temperature_bounds_are_unchanged(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        with pytest.raises(AssertionError):
          await backend.set_temperature(0)
        with pytest.raises(AssertionError):
          await backend.set_temperature(105.1)
        await backend.set_temperature(105)

    assert interface.commands[2:] == ["T1TAta1050"]

  async def test_plate_lock_positions_are_unchanged(self) -> None:
    assert PlateLockPosition.LOCKED.value == 1
    assert PlateLockPosition.UNLOCKED.value == 0

  async def test_shake_is_deprecated_and_delegates(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        with pytest.warns(DeprecationWarning) as warnings_info:
          await backend.shake(speed=900, direction=1, acceleration=750, timeout=5)

    assert str(warnings_info[0].message) == (
      "HamiltonHeaterShakerBackend.shake() is deprecated and will be removed in a future "
      "release. Use start_shaking() instead."
    )
    assert interface.commands[2:] == ["T1LPlp1", "T1SBst1sv0900sr00750", "T1RD"]


class TestHamiltonHeaterShakerStartShaking(AnyioTestBase):
  async def test_start_locks_the_plate_before_validating(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        with pytest.raises(AssertionError, match="Speed must be between 20 and 2_000"):
          await backend.start_shaking(speed=10)

    # The plate lock is issued first, before the parameters are checked, and a start that
    # never committed issues no stop.
    assert interface.commands[2:] == ["T1LPlp1"]

  async def test_validation_messages_are_unchanged(self) -> None:
    backend, interface = _make_backend()

    async with interface:
      async with backend:
        with pytest.raises(AssertionError, match="Speed must be between 20 and 2_000"):
          await backend.start_shaking(speed=2_001)
        with pytest.raises(AssertionError, match="Direction must be 0 or 1"):
          await backend.start_shaking(direction=cast(Any, 2))
        with pytest.raises(AssertionError, match="Acceleration must be between 500 and 10_000"):
          await backend.start_shaking(acceleration=499)
        with pytest.raises(AssertionError, match="Acceleration must be between 500 and 10_000"):
          await backend.start_shaking(acceleration=10_001)

    assert [command for command in interface.commands if command.startswith("T1SB")] == []
    assert [command for command in interface.commands if command in ("T1SC", "T1SW")] == []

  async def test_start_retries_until_the_device_reports_shaking(self) -> None:
    backend, interface = _make_backend(rd_sequence=["rd0", "rd0", "rd1"])

    async with interface:
      async with backend:
        await backend.start_shaking(speed=800)

    # Each attempt is one `SB` followed by one `RD`, with no pause between attempts.
    assert interface.commands[2:] == [
      "T1LPlp1",
      "T1SBst0sv0800sr01000",
      "T1RD",
      "T1SBst0sv0800sr01000",
      "T1RD",
      "T1SBst0sv0800sr01000",
      "T1RD",
    ]

  async def test_start_without_a_timeout_retries_indefinitely(self) -> None:
    backend, interface = _make_backend(rd_sequence=["rd0"] * 5 + ["rd1"])

    async with interface:
      async with backend:
        with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
          await backend.start_shaking(speed=800, timeout=None)

    # `None` remains legal and remains unbounded: the loop keeps trying past any budget a
    # bounded start would have applied.
    assert interface.commands.count("T1SBst0sv0800sr01000") == 6

  async def test_start_checks_its_budget_after_each_attempt(self) -> None:
    backend, interface = _make_backend(rd_sequence=["rd0"])

    async with interface:
      async with backend:
        with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
          with pytest.raises(TimeoutError, match="Failed to start shaking within timeout"):
            await backend.start_shaking(speed=800, timeout=0)

    # A zero budget still performs exactly one attempt, because the elapsed time is checked
    # after the attempt rather than before it.
    assert interface.commands.count("T1SBst0sv0800sr01000") == 1

  async def test_start_timeout_stops_the_committed_start(self) -> None:
    backend, interface = _make_backend(rd_sequence=["rd0"])

    async with interface:
      async with backend:
        with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
          with pytest.raises(TimeoutError, match="Failed to start shaking within timeout"):
            await backend.start_shaking(speed=800, timeout=0)

    # An `SB` committed, so the operation owns stopping it before the failure propagates.
    assert interface.commands[2:] == [
      "T1LPlp1",
      "T1SBst0sv0800sr01000",
      "T1RD",
      "T1SC",
      "T1SW",
    ]

  async def test_cancelled_start_stops_the_committed_start(self) -> None:
    backend, interface = _make_backend(rd_sequence=["rd0"], blocked_command="RD")
    scopes: List[anyio.CancelScope] = []

    async with interface:
      async with backend:
        baseline = len(interface.commands)

        async def start() -> None:
          with anyio.CancelScope() as scope:
            scopes.append(scope)
            try:
              await backend.start_shaking(speed=800)
            except anyio.get_cancelled_exc_class():
              interface.events.append("start.cancelled")
              raise
            pytest.fail("the cancelled start returned")

        with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(start)
            await interface.blocked_started.wait()
            scopes[0].cancel()
            interface.blocked_command = None
            interface.release_blocked.set()

        # The `SB` committed before the cancellation arrived, so the operation stops it
        # rather than leaving the device shaking.
        assert interface.commands[baseline:] == [
          "T1LPlp1",
          "T1SBst0sv0800sr01000",
          "T1RD",
          "T1SC",
          "T1SW",
        ]
        assert interface.events[-1] == "start.cancelled"

  async def test_start_cancelled_before_committing_issues_no_stop(self) -> None:
    backend, interface = _make_backend(blocked_command="LP")
    scopes: List[anyio.CancelScope] = []

    async with interface:
      async with backend:
        baseline = len(interface.commands)

        async def start() -> None:
          with anyio.CancelScope() as scope:
            scopes.append(scope)
            try:
              await backend.start_shaking(speed=800)
            except anyio.get_cancelled_exc_class():
              interface.events.append("start.cancelled")
              raise
            pytest.fail("the cancelled start returned")

        with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(start)
            await interface.blocked_started.wait()
            scopes[0].cancel()
            interface.blocked_command = None
            interface.release_blocked.set()

        # No `SB` ever reached the wire, so there is nothing to stop and no stop is issued.
        assert interface.commands[baseline:] == ["T1LPlp1"]
        assert interface.events[-1] == "start.cancelled"

  async def test_failed_start_command_stops_the_ambiguous_start(self) -> None:
    failure = RuntimeError("SB failed")
    backend, interface = _make_backend(failures={"SB": failure})

    async with interface:
      async with backend:
        baseline = len(interface.commands)
        with pytest.raises(RuntimeError) as error_info:
          await backend.start_shaking(speed=800)

    # A write that fails is conservatively ambiguous: the device may have received it, so the
    # operation stops what it may have started and still reports the original failure.
    assert error_info.value is failure
    assert interface.commands[baseline:] == ["T1LPlp1", "T1SBst0sv0800sr01000", "T1SC", "T1SW"]

  async def test_failed_plate_lock_issues_no_stop(self) -> None:
    failure = RuntimeError("LP failed")
    backend, interface = _make_backend(failures={"LP": failure})

    async with interface:
      async with backend:
        baseline = len(interface.commands)
        with pytest.raises(RuntimeError) as error_info:
          await backend.start_shaking(speed=800)

    assert error_info.value is failure
    assert interface.commands[baseline:] == ["T1LPlp1"]

  async def test_stop_of_a_committed_start_is_bounded(self) -> None:
    # The stop the operation owns is shielded, so an unbounded one could never be interrupted.
    # The stall is a finite sleep, so a backend that applies no bound at all completes the
    # stop late and fails this test instead of hanging it.
    backend, interface = _make_backend(
      rd_sequence=["rd0"], stalled_commands={"SC": _REAL_TIME_STALL}
    )

    async with interface:
      async with backend:
        with patch.object(hamilton_module, "_START_CLEANUP_TIMEOUT", 0.05):
          with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
            with pytest.raises(TimeoutError) as error_info:
              await backend.start_shaking(speed=800, timeout=0)

    assert str(error_info.value) == (
      "Could not confirm shaking started by this operation was stopped"
    )
    # The failure this cleanup was unwinding is still reachable from it.
    assert isinstance(error_info.value.__context__, TimeoutError)
    assert str(error_info.value.__context__) == "Failed to start shaking within timeout"

  async def test_start_cleanup_timeout_covers_three_exchanges(self) -> None:
    # Both against the exchange budget it is derived from, so the two cannot drift apart, and
    # against its literal value, so they cannot be moved together. The stop dispatches two
    # commands and may queue behind one in-flight exchange, and it is a fixed budget for
    # confirming them: it never derives from a speed, duration, or retry count.
    assert hamilton_module._START_CLEANUP_TIMEOUT == 3 * hamilton_module._COMMAND_TIMEOUT + 4.0
    assert hamilton_module._START_CLEANUP_TIMEOUT == 184.0

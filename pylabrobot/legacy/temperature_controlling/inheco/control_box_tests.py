from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.boundary import BoundaryRead
from pylabrobot.io.hid import HID
from pylabrobot.legacy.temperature_controlling.inheco import control_box as control_box_module
from pylabrobot.legacy.temperature_controlling.inheco.control_box import InhecoTECControlBox
from pylabrobot.testing.concurrency import AnyioTestBase

# Deadlock guard for the event-driven transaction tests. Those finish in milliseconds, so this
# is orders of magnitude larger than any correct run, but finite: a transaction that is never
# handed on fails the test rather than hanging the run.
_REAL_TIME_DEADLOCK_GUARD = 60.0

# A reply stall longer than any command budget these tests exercise. It is finite on purpose,
# so a control box that applies no deadline at all answers late and fails an assertion rather
# than hanging the run.
_REAL_TIME_STALL = 5.0

# Slack allowed around a deadline observed on a real clock. It is smaller than the difference
# between one shared deadline and a per-phase budget, so the two cannot be confused.
_DEADLINE_SLACK = 0.35


def _write_event(command: str) -> str:
  """Return the recorded event of one command packet write."""
  return f"write:{command}"


def _exchange_events(command: str) -> List[str]:
  """Return the recorded events of one complete single-packet command exchange."""
  return [_write_event(command), "read"]


def _command_of(data: bytes) -> str:
  """Recover the command text a single packet carries, without its checksum byte."""
  return data.rstrip(b"\x00")[:-1].decode("ascii", errors="replace")


def _reply_for(command: str, *, status: str = "0", payload: str = "value") -> bytes:
  """Build the device's modified-echo reply for a command.

  The device answers the first four characters of every command in lower case, followed by
  an error-status character, the payload, and a final checksum byte.
  """
  return f"{command[:4].lower()}{status}{payload}C".encode("ascii") + b"\x00"


class RecordingHID(HasLifespan):
  """Software HID double recording lifecycle and wire events."""

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    close_error: Optional[BaseException] = None,
    replies: Optional[List[bytes]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    blocked_event: Optional[str] = None,
    write_delay: float = 0.0,
    read_stall: float = 0.0,
  ) -> None:
    """Initialize a closed double.

    Args:
      events: Shared ordered event log, or None for a private one.
      entry_error: Exception to raise while the transport opens.
      close_error: Exception to raise when the lifespan closes the transport.
      replies: Scripted packets, used before the synthesized modified echo.
      failures: Exception to raise from the named event, keyed by event.
      blocked_event: Event name whose call waits for `release_blocked`.
      write_delay: Seconds a packet write takes after it has reached the wire.
      read_stall: Seconds a reply read waits before answering. Unlike `blocked_event`, this
        stall ends on its own, so a caller that never bounds the exchange answers late and
        fails a timing assertion rather than hanging the run.
    """
    super().__init__()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.close_error = close_error
    self.replies: Deque[bytes] = deque(replies or [])
    self.failures = failures or {}
    self.blocked_event = blocked_event
    self.write_delay = write_delay
    self.read_stall = read_stall
    self.hid_kwargs: Dict[str, Any] = {}
    self.writes: List[Tuple[bytes, bytes]] = []
    self.commands: List[str] = []
    self.reads: List[Tuple[int, Optional[int]]] = []
    self.write_times: List[float] = []
    self.read_times: List[float] = []
    self.entries = 0
    self.exits = 0
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingHID does not accept lifespan options")
    self.entries += 1
    self.events.append("hid.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    """Record the transport close and raise the injected close failure."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("hid.exit")
    if self.close_error is not None:
      raise self.close_error

  async def _block_if_requested(self, event: str) -> None:
    """Wait for release when this event is the configured blocking point."""
    if event == self.blocked_event:
      self.blocked_started.set()
      await self.release_blocked.wait()

  async def write(self, data: bytes, report_id: bytes = b"\x00") -> None:
    """Record one packet write at its entry, then block or fail as configured.

    The event is appended before this double awaits anything of its own. A transport call is
    the moment work reaches the wire, so recording it after a scheduler hop would report it
    one hop late and hide orderings the caller genuinely produces.
    """
    command = _command_of(data)
    self.writes.append((data, report_id))
    self.commands.append(command)
    self.write_times.append(anyio.current_time())
    self.events.append(_write_event(command))
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested(_write_event(command))
    if self.write_delay > 0:
      await anyio.sleep(self.write_delay)
    failure = self.failures.get(_write_event(command))
    if failure is not None:
      raise failure

  async def read(self, size: int, timeout: Optional[int] = None) -> bytes:
    """Record one reply read at its entry, then stall or block, and answer the last command."""
    self.reads.append((size, timeout))
    self.read_times.append(anyio.current_time())
    self.events.append("read")
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested("read")
    if self.read_stall > 0:
      await anyio.sleep(self.read_stall)
    failure = self.failures.get("read")
    if failure is not None:
      raise failure
    if self.replies:
      return self.replies.popleft()
    if not self.commands:
      return b""
    return _reply_for(self.commands[-1])

  async def read_to_boundary(self) -> BoundaryRead:
    """Return one complete fake HID report."""
    return BoundaryRead(await self.read(64), True, True, False)


def make_control_box(
  monkeypatch: pytest.MonkeyPatch,
  *,
  vid: int = 0x03EB,
  pid: int = 0x2023,
  serial_number: Optional[str] = None,
  **double_kwargs: Any,
) -> Tuple[InhecoTECControlBox, RecordingHID]:
  """Build a control box whose transport is a software HID double."""
  io = RecordingHID(**double_kwargs)

  def build_hid(**kwargs: Any) -> HID:
    io.hid_kwargs = dict(kwargs)
    return cast(HID, io)

  monkeypatch.setattr(control_box_module, "HID", build_hid)
  return InhecoTECControlBox(vid=vid, pid=pid, serial_number=serial_number), io


def _packets_of(io: RecordingHID) -> List[Tuple[bytes, bytes]]:
  """Return the packets written to the wire, as data and report id."""
  return list(io.writes)


def _wire_events(io: RecordingHID, baseline: int) -> List[str]:
  """Return the transport events recorded since `baseline`, without the test's own markers.

  Tests append cancellation markers to the shared log to place an observation in the
  ordering. This drops them again, so an assertion about what reached the wire reads only
  wire events: `write:*`, `read`, `hid.enter`, and `hid.exit`.
  """
  return [event for event in io.events[baseline:] if not event.endswith(".cancelled")]


async def settle() -> None:
  """Give every other task room to reach its next transport call."""
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


class TestInhecoTECControlBoxLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert InhecoTECControlBox.setup is HasLifespan.setup
    assert InhecoTECControlBox.stop is HasLifespan.stop
    assert InhecoTECControlBox.__aenter__ is HasLifespan.__aenter__
    assert InhecoTECControlBox.__aexit__ is HasLifespan.__aexit__

  async def test_hid_configuration_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _, io = make_control_box(monkeypatch, serial_number="ABC123")

    assert io.hid_kwargs == {
      "human_readable_device_name": "Inheco Control Box",
      "vid": 0x03EB,
      "pid": 0x2023,
      "serial_number": "ABC123",
      "max_input_report_size": 64,
    }

  async def test_rejects_lifespan_options_before_hid_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with control_box.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert io.events == []
    assert io.entries == 0

  async def test_hid_entry_failure_does_not_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("hid entry failed")
    control_box, io = make_control_box(monkeypatch, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with control_box:
        pytest.fail("failed HID entry reached the body")

    assert error_info.value is failure
    assert io.events == ["hid.enter"]
    assert io.exits == 0
    assert not control_box.is_active

  async def test_body_cancellation_closes_hid(self, monkeypatch: pytest.MonkeyPatch) -> None:
    body_started = anyio.Event()
    control_box, io = make_control_box(monkeypatch)

    async def use_control_box() -> None:
      try:
        async with control_box:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        io.events.append("cancelled")
        raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_control_box)
        await body_started.wait()
        task_group.cancel_scope.cancel()

    assert io.events == ["hid.enter", "hid.exit", "cancelled"]
    assert not control_box.is_active

  async def test_close_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("hid close failed")
    control_box, io = make_control_box(monkeypatch, close_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with control_box:
        pass

    assert error_info.value is failure
    assert io.events == ["hid.enter", "hid.exit"]
    assert not control_box.is_active

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      await control_box.reset_action_display()
    async with control_box:
      await control_box.reset_action_display()

    assert io.events == [
      "hid.enter",
      *_exchange_events("0ASD"),
      "hid.exit",
      "hid.enter",
      *_exchange_events("0ASD"),
      "hid.exit",
    ]
    assert io.entries == 2
    assert io.exits == 2


class TestInhecoTECControlBoxWireFormat(AnyioTestBase):
  """Pin the bytes and the reply handling the device sees, which this migration preserves."""

  async def test_command_packets_are_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      await control_box.send_command("0ASD")
      await control_box.send_command("2STT370")
      await control_box.send_command("2ASE1")

    # The report id is the first buffer slot, which the packet generator only ever leaves at
    # zero; `bytes(0)` is empty, so every packet is written with an empty report id.
    assert _packets_of(io) == [
      (b"0ASD\xf1\x00\x00\x00", b""),
      (b"2STT370\xc7", b""),
      (b"2ASE1\x8d\x00\x00", b""),
    ]

  async def test_multi_packet_command_payloads_are_unchanged(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A command longer than one packet writes the generator's repeated final buffer.

    The generator appends the same mutable buffer once per packet, so every packet carries
    the final chunk. This is pre-existing behavior, pinned here so the migration cannot be
    blamed for it and so a future fix is a visible, deliberate change.
    """
    control_box, io = make_control_box(monkeypatch, replies=[b"1stt0okC\x00"])

    async with control_box:
      assert await control_box.send_command("1STT1234567890") == "ok"

    assert _packets_of(io) == [
      (b";\x00\x00\x00\x00\x00\x00\x00", b""),
      (b";\x00\x00\x00\x00\x00\x00\x00", b""),
      (b";\x00\x00\x00\x00\x00\x00\x00", b""),
    ]

  async def test_lowercase_command_characters_are_raised(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch, replies=[_reply_for("0ASD")])

    async with control_box:
      await control_box.send_command("0asd")

    assert _packets_of(io) == [(b"0ASD\xf1\x00\x00\x00", b"")]

  async def test_read_size_and_timeout_arguments(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the read size and the per-read timeout the control box forwards to the transport.

    The size is unchanged. The timeout is not: base computed `int(timeout - elapsed)` from a
    wall clock it kept itself, which truncates to `timeout - 1` on the very first read, so a
    `timeout=3` command forwarded `2` and a `timeout=7` command forwarded `6` (measured at
    base: `[(64, 2), (64, 2)]` and `[(64, 6), (64, 6)]`). The single exchange deadline removed
    that bookkeeping, so the caller's value is now forwarded directly. The change only
    lengthens how long one individual read may block, and the exchange as a whole is still
    bounded by the caller's budget, so no command that previously succeeded can newly fail.
    """
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      await control_box.send_command("0ASD")
      await control_box.send_command("0ASD", timeout=7)

    assert io.reads == [(64, None), (64, None)]

  async def test_response_slice_is_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch, replies=[_reply_for("0ASD", payload="payload")])

    async with control_box:
      assert await control_box.send_command("0ASD") == "payload"

    assert io.reads == [(64, None)]

  async def test_error_status_raises_the_documented_error(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, _ = make_control_box(monkeypatch, replies=[_reply_for("0ASD", status="4")])

    async with control_box:
      with pytest.raises(RuntimeError) as error_info:
        await control_box.send_command("0ASD")

    assert str(error_info.value) == "Error response from device: 0asd4valueC"

  async def test_continuation_packets_are_joined(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch, replies=[b"0asd0va#", b"lueC\x00\x00"])

    async with control_box:
      assert await control_box.send_command("0ASD") == "value"

    assert io.reads == [(64, None), (64, None)]

  async def test_unterminated_packets_are_reported_and_joined(
    self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
    control_box, io = make_control_box(monkeypatch, replies=[b"0asd0va", b"lueC\x00"])

    async with control_box:
      assert await control_box.send_command("0ASD") == "value"

    assert "weird packet, please report b'0asd0va'" in capsys.readouterr().out
    assert io.reads == [(64, None), (64, None)]

  async def test_wrong_and_short_echoes_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(
      monkeypatch,
      replies=[b"0as\x00", b"1asd0wrongC\x00", b"0asd0rightC\x00"],
    )

    async with control_box:
      assert await control_box.send_command("0ASD") == "right"

    assert len(io.reads) == 3

  async def test_touchscreen_commands_are_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      await control_box.set_touchscreen(True)
      await control_box.set_touchscreen(False)

    assert io.commands == ["0ADD1", "0ASD", "0ADD0", "0ASD"]
    assert _packets_of(io)[:2] == [
      (b"0ADD1\xfa\x00\x00", b""),
      (b"0ASD\xf1\x00\x00\x00", b""),
    ]
    assert _packets_of(io)[2] == (b"0ADD0\xa4\x00\x00", b"")


class TestInhecoTECControlBoxTransactions(AnyioTestBase):
  async def test_command_packets_are_serialized(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch, blocked_event="read")
    results: Dict[str, str] = {}

    async def first() -> None:
      results["first"] = await control_box.send_command("0ADD1")

    async def second() -> None:
      results["second"] = await control_box.send_command("2ASE0")

    async with control_box:
      baseline = len(io.events)

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(first)
          await io.blocked_started.wait()
          task_group.start_soon(second)
          await settle()
          try:
            # The first exchange owns the transaction, so the second command has put nothing
            # on the wire: no interleaved packet group and no interleaved reply.
            assert io.events[baseline:] == _exchange_events("0ADD1")
          finally:
            io.release_blocked.set()

      assert io.events[baseline:] == [
        *_exchange_events("0ADD1"),
        *_exchange_events("2ASE0"),
      ]
      assert results == {"first": "value", "second": "value"}
      io.blocked_event = None

  async def test_pre_cancelled_command_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      baseline = len(io.events)
      with anyio.CancelScope() as scope:
        scope.cancel()
        await control_box.send_command("0ASD")

      assert io.events[baseline:] == []
      assert io.writes == []

  async def test_cancelled_command_reconciles_response(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch, blocked_event="read")
    cancelled = anyio.Event()
    completed = False
    scopes: List[anyio.CancelScope] = []

    async def send() -> None:
      nonlocal completed
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await control_box.send_command("0ASD")
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise
        completed = True

    async with control_box:
      baseline = len(io.events)

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send)
          await io.blocked_started.wait()
          scopes[0].cancel()
          await settle()
          try:
            # The committed exchange is not abandoned: cancellation waits for the reply.
            assert not cancelled.is_set()
          finally:
            io.release_blocked.set()

      assert cancelled.is_set()
      assert not completed
      assert io.events[baseline:] == _exchange_events("0ASD")

      io.blocked_event = None
      assert await control_box.send_command("2ASE0") == "value"

  async def test_command_cancelled_while_waiting_for_the_transaction_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A command cancelled while waiting for the transaction puts nothing on the wire.

    This pins the outcome, not one mechanism: the control box stops such a waiter twice
    over, through a cancellable acquire and through the checkpoint that follows it, and
    either alone is enough. What this catches is losing the last stop standing.
    """
    control_box, io = make_control_box(monkeypatch, blocked_event="read")
    scopes: List[anyio.CancelScope] = []
    queued = anyio.Event()

    async def queued_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        # Set before the call, so the wait below returns only once this task has started.
        # `Event.set` does not yield, so the `settle()` after the wait is what carries it
        # to its blocking point, waiting for the transaction.
        queued.set()
        try:
          await control_box.send_command("2ASE0")
        except anyio.get_cancelled_exc_class():
          io.events.append("2ASE0.cancelled")
          raise
        pytest.fail("the cancelled command returned")

    async with control_box:
      baseline = len(io.events)

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(control_box.send_command, "0ADD1")
          await io.blocked_started.wait()
          task_group.start_soon(queued_command)
          await queued.wait()
          await settle()
          try:
            assert _wire_events(io, baseline) == _exchange_events("0ADD1")
            scopes[0].cancel()
            await settle()
            # Cancelling a command that never owned the transaction cannot put its bytes on
            # the wire, neither while it waits nor when the transaction is handed on.
            assert _wire_events(io, baseline) == _exchange_events("0ADD1")
          finally:
            # Released even when an assertion above fails, because the held exchange is
            # shielded and would otherwise outlive the guard.
            io.release_blocked.set()

      assert "2ASE0.cancelled" in io.events[baseline:]
      assert io.commands == ["0ADD1"]
      assert _wire_events(io, baseline) == _exchange_events("0ADD1")
      io.blocked_event = None

  async def test_lock_is_released_before_pending_cancellation_propagates(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch, blocked_event="read")
    scopes: List[anyio.CancelScope] = []

    async def send_first() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await control_box.send_command("0ADD1")
        except anyio.get_cancelled_exc_class():
          io.events.append("0ADD1.cancelled")
          raise
        pytest.fail("the cancelled command returned")

    async with control_box:
      baseline = len(io.events)

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send_first)
          await io.blocked_started.wait()
          scopes[0].cancel()
          task_group.start_soon(control_box.send_command, "2ASE0")
          await settle()
          try:
            # The cancelled command still holds the transaction, so the queued command is
            # waiting for it and nothing else has reached the wire.
            assert _wire_events(io, baseline) == _exchange_events("0ADD1")
          finally:
            io.release_blocked.set()

      tail = io.events[baseline:]
      # The transaction is handed to the waiting command before the cancelled caller learns
      # it was cancelled, so a queued command is never delayed by one cancellation delivery.
      # Releasing the transaction schedules the waiter, and only then does the cancelled
      # caller reach the checkpoint that delivers its cancellation, so the queued write
      # precedes the marker on both backends. Where the marker falls after that write is a
      # race between two independent tasks that no contract governs, so nothing here pins it.
      assert tail[:2] == _exchange_events("0ADD1")
      assert tail[2] == _write_event("2ASE0")
      assert "0ADD1.cancelled" in tail[3:]
      assert _wire_events(io, baseline) == [
        *_exchange_events("0ADD1"),
        *_exchange_events("2ASE0"),
      ]
      io.blocked_event = None


class TestInhecoTECControlBoxDeadline(AnyioTestBase):
  async def test_response_timeout_reports_the_documented_message(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch, read_stall=_REAL_TIME_STALL)

    async with control_box:
      with pytest.raises(TimeoutError) as error_info:
        await control_box.send_command("0ASD", timeout=1)
      io.read_stall = 0.0

    assert str(error_info.value) == "Timeout while waiting for response from device."
    assert io.commands == ["0ASD"]

  async def test_one_deadline_covers_writes_and_response(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The write and the response reconciliation share one budget, not one budget each.

    The write is charged to the same deadline as the reply, so a command whose write alone
    consumes most of the budget fails at the budget rather than at write time plus another
    full budget.
    """
    control_box, io = make_control_box(
      monkeypatch,
      write_delay=0.6,
      read_stall=_REAL_TIME_STALL,
    )

    async with control_box:
      start = anyio.current_time()
      with pytest.raises(TimeoutError, match="Timeout while waiting for response from device"):
        await control_box.send_command("0ASD", timeout=1)
      elapsed = anyio.current_time() - start
      io.read_stall = 0.0
      io.write_delay = 0.0

    assert elapsed == pytest.approx(1.0, abs=_DEADLINE_SLACK)
    assert io.commands == ["0ASD"]

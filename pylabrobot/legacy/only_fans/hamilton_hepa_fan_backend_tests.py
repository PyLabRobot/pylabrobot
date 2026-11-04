from typing import Any, Dict, List, Optional, Tuple, cast
from unittest.mock import patch

import anyio
import pytest
from trio.testing import MockClock

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.legacy.only_fans import Fan
from pylabrobot.legacy.only_fans import hamilton_hepa_fan_backend as hepa_module
from pylabrobot.legacy.only_fans.hamilton_hepa_fan_backend import HamiltonHepaFanBackend
from pylabrobot.testing.concurrency import AnyioTestBase

# The FTDI configuration the backend applies, in the order it applies it.
_CONFIGURATION_EVENTS = [
  "baud:9600",
  "line:8,0,0",
  "latency:16",
  "flowctrl:512",
  "dtr:True",
  "rts:True",
]

# The five initialization packets, in wire order.
_SETUP_PACKETS = [
  bytes.fromhex("55c10102234b"),
  bytes.fromhex("55c10108086a"),
  bytes.fromhex("55c101096a09"),
  bytes.fromhex("55c1010a2f4f"),
  bytes.fromhex("1561018a"),
]

_ON_PACKET = bytes.fromhex("354101ff75")
_OFF_PACKET = bytes.fromhex("55c10111007b")

# Representative entries of the intensity-indexed speed table.
_SPEED_PACKETS = {
  0: bytes.fromhex("55c10111007b"),
  50: bytes.fromhex("55c101117f04"),
  100: bytes.fromhex("55c10111fe85"),
}

_READ_EVENT = "read:64"

# A reply that never realistically arrives, for the deadline test. It is nonetheless finite
# on purpose: a backend that applies no bound at all eventually completes it and fails that
# test rather than hanging it. That test runs on a virtual clock, so this costs no real time.
_STALLED_READ_SECONDS = 86400.0

# Deadlock guard for the deadline test. The three values are deliberately ordered
# bound < guard < stall: the guard is larger than any bound the test could plausibly observe,
# so an overlong bound is still reported by the elapsed-time assertions rather than by the
# guard, while a missing bound trips the guard instead of hanging the run.
_DEADLOCK_GUARD = 7200.0

# Deadlock guard for the real-clock transaction tests. Those tests are event-driven and finish
# in milliseconds, so this is orders of magnitude larger than any correct run, but finite: a
# wakeup the backend never delivers fails the run instead of hanging it.
_REAL_TIME_DEADLOCK_GUARD = 60.0


def _exchange_events(packet: bytes) -> List[str]:
  """Return the recorded events of one complete command exchange."""
  return [f"write:{packet.hex()}", _READ_EVENT]


class RecordingFTDI(HasLifespan):
  """Software FTDI double recording lifecycle, configuration, and wire events."""

  def __init__(
    self,
    *,
    events: Optional[List[str]] = None,
    failures: Optional[Dict[str, BaseException]] = None,
    close_error: Optional[BaseException] = None,
    blocked_event: Optional[str] = None,
    read_stall: float = 0.0,
    stall_after_write: Optional[bytes] = None,
  ) -> None:
    """Initialize a closed double.

    Args:
      events: Shared ordered event log, or None for a private one.
      failures: Exception to raise from the named event, keyed by event.
      close_error: Exception to raise when the lifespan closes the transport.
      blocked_event: Event name whose call waits for `release_blocked`.
      read_stall: Seconds a read waits before returning a reply. Unlike `blocked_event`,
        this stall ends on its own, so a caller that never bounds the exchange completes
        late and fails a timing assertion rather than hanging the run.
      stall_after_write: Restrict `read_stall` to the read that follows this packet, or
        None to stall every read.
    """
    super().__init__()
    self.events = events if events is not None else []
    self.failures = failures or {}
    self.close_error = close_error
    self.blocked_event = blocked_event
    self.read_stall = read_stall
    self.stall_after_write = stall_after_write
    self.writes: List[bytes] = []
    self.read_sizes: List[int] = []
    self.write_times: List[float] = []
    self.read_times: List[float] = []
    self.entries = 0
    self.exits = 0
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Open the double and register its close."""
    if kwargs:
      raise TypeError("RecordingFTDI does not accept lifespan options")
    self.entries += 1
    self.events.append("ftdi.enter")
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    """Record the transport close and raise the injected close failure."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("ftdi.exit")
    if self.close_error is not None:
      raise self.close_error

  async def _block_if_requested(self, event: str) -> None:
    """Wait for release when this event is the configured blocking point."""
    if event == self.blocked_event:
      self.blocked_started.set()
      await self.release_blocked.wait()

  async def _record(self, event: str) -> None:
    """Record one configuration call and raise its injected failure."""
    await anyio.lowlevel.checkpoint()
    self.events.append(event)
    await self._block_if_requested(event)
    failure = self.failures.get(event)
    if failure is not None:
      raise failure

  async def set_baudrate(self, baudrate: int) -> None:
    await self._record(f"baud:{baudrate}")

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    await self._record(f"line:{bits},{stopbits},{parity}")

  async def set_latency_timer(self, latency: int) -> None:
    await self._record(f"latency:{latency}")

  async def set_flowctrl(self, flowctrl: int) -> None:
    await self._record(f"flowctrl:{flowctrl}")

  async def set_dtr(self, level: bool) -> None:
    await self._record(f"dtr:{level}")

  async def set_rts(self, level: bool) -> None:
    await self._record(f"rts:{level}")

  async def write(self, data: bytes) -> int:
    """Record one write, its dispatch time, and raise its injected failure.

    The write is recorded at call entry, before this double yields. A write is the moment a
    command reaches the wire, so recording it after a scheduler hop of the double's own would
    report it one hop late and hide orderings the caller genuinely produces.
    """
    self.writes.append(data)
    self.write_times.append(anyio.current_time())
    event = f"write:{data.hex()}"
    self.events.append(event)
    await anyio.lowlevel.checkpoint()
    await self._block_if_requested(event)
    failure = self.failures.get(event)
    if failure is not None:
      raise failure
    return len(data)

  async def _stall_if_requested(self) -> None:
    """Wait out a configured reply stall, optionally only after one specific write."""
    if self.read_stall <= 0:
      return
    if self.stall_after_write is not None and (
      not self.writes or self.writes[-1] != self.stall_after_write
    ):
      return
    await anyio.sleep(self.read_stall)

  async def read(self, num_bytes: int = 1) -> bytes:
    """Record one read and its arrival time, blocking or stalling as configured."""
    await anyio.lowlevel.checkpoint()
    self.read_sizes.append(num_bytes)
    self.read_times.append(anyio.current_time())
    event = f"read:{num_bytes}"
    self.events.append(event)
    await self._block_if_requested(event)
    await self._stall_if_requested()
    return b""


def _make_backend(
  monkeypatch: pytest.MonkeyPatch, **ftdi_kwargs: Any
) -> Tuple[HamiltonHepaFanBackend, RecordingFTDI]:
  """Build a backend whose transport is a software FTDI double."""
  io = RecordingFTDI(**ftdi_kwargs)
  monkeypatch.setattr(hepa_module, "FTDI", lambda **kwargs: cast(FTDI, io))
  return HamiltonHepaFanBackend(), io


async def _settle() -> None:
  """Give every other task room to reach its next transport call."""
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


class TestHamiltonHepaFanLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert HamiltonHepaFanBackend.__aenter__ is HasLifespan.__aenter__
    assert HamiltonHepaFanBackend.__aexit__ is HasLifespan.__aexit__
    assert HamiltonHepaFanBackend.setup is HasLifespan.setup
    assert HamiltonHepaFanBackend.stop is HasLifespan.stop

  async def test_rejects_lifespan_options_before_ftdi_entry(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options reached the body")

    assert io.events == []
    assert io.writes == []
    assert io.entries == 0

  async def test_setup_transcript_and_normal_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend:
      assert io.writes == _SETUP_PACKETS

    assert io.writes == [
      bytes.fromhex("55c10102234b"),
      bytes.fromhex("55c10108086a"),
      bytes.fromhex("55c101096a09"),
      bytes.fromhex("55c1010a2f4f"),
      bytes.fromhex("1561018a"),
    ]
    setup_exchanges = [event for packet in _SETUP_PACKETS for event in _exchange_events(packet)]
    # Normal exit closes the transport with no further write: the Fan frontend, not the
    # backend, owns turning the fan off.
    assert io.events == ["ftdi.enter", *_CONFIGURATION_EVENTS, *setup_exchanges, "ftdi.exit"]
    assert io.read_sizes == [64] * 5
    assert io.exits == 1

  @pytest.mark.parametrize(
    "failure_event",
    [
      "baud:9600",
      "line:8,0,0",
      "latency:16",
      "flowctrl:512",
      "dtr:True",
      "rts:True",
      "write:55c10102234b",
      "write:55c10108086a",
      "write:55c101096a09",
      "write:55c1010a2f4f",
      "write:1561018a",
    ],
  )
  async def test_configuration_failure_closes_ftdi(
    self, monkeypatch: pytest.MonkeyPatch, failure_event: str
  ) -> None:
    failure = RuntimeError(f"{failure_event} failed")
    backend, io = _make_backend(monkeypatch, failures={failure_event: failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed entry reached the body")

    assert error_info.value is failure
    assert io.events[-1] == "ftdi.exit"
    assert io.exits == 1
    assert not backend.is_active

  async def test_entry_cancellation_closes_ftdi(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch, blocked_event="latency:16")
    scopes: List[anyio.CancelScope] = []

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          pytest.fail("cancelled entry reached the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await io.blocked_started.wait()
      scopes[0].cancel()

    assert io.events[-1] == "ftdi.exit"
    assert io.exits == 1
    assert io.writes == []
    assert not backend.is_active

  async def test_body_cancellation_closes_ftdi_without_extra_writes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    body_started = anyio.Event()
    speed_packet = _SPEED_PACKETS[50]

    async def run() -> None:
      async with backend:
        await backend.turn_on(50)
        body_started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert io.writes == [*_SETUP_PACKETS, _ON_PACKET, speed_packet]
    last_write = max(index for index, event in enumerate(io.events) if event.startswith("write:"))
    # The last body write is followed only by its own read and the transport close.
    assert io.events[last_write:] == [*_exchange_events(speed_packet), "ftdi.exit"]
    assert io.exits == 1
    assert not backend.is_active

  async def test_fan_frontend_exit_issues_single_turn_off_before_ftdi_close(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    fan = Fan(backend=backend)

    async with fan:
      assert io.writes == _SETUP_PACKETS

    off_event = f"write:{_OFF_PACKET.hex()}"
    assert io.writes.count(_OFF_PACKET) == 1
    assert io.events.count(off_event) == 1
    assert io.events[-3:] == [*_exchange_events(_OFF_PACKET), "ftdi.exit"]
    assert io.exits == 1

  async def test_close_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("ftdi close failed")
    backend, io = _make_backend(monkeypatch, close_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert io.events[-1] == "ftdi.exit"
    assert not backend.is_active

  async def test_sequential_reuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend:
      pass
    async with backend:
      pass

    assert io.entries == 2
    assert io.exits == 2
    for event in _CONFIGURATION_EVENTS:
      assert io.events.count(event) == 2
    assert io.writes == _SETUP_PACKETS * 2


class TestHamiltonHepaFanTransactions(AnyioTestBase):
  async def test_concurrent_send_transactions_do_not_interleave(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend:
      io.blocked_event = _READ_EVENT
      baseline = len(io.writes)

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(backend.send, _ON_PACKET)
        await io.blocked_started.wait()
        task_group.start_soon(backend.send, _OFF_PACKET)
        await _settle()
        # The second command may not reach the wire while the first exchange holds the
        # transaction.
        assert io.writes[baseline:] == [_ON_PACKET]
        io.release_blocked.set()

      assert io.writes[baseline:] == [_ON_PACKET, _OFF_PACKET]

      io.blocked_event = None

  async def test_cancelled_send_reconciles_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, io = _make_backend(monkeypatch)
    cancelled = anyio.Event()
    completed = False
    scopes: List[anyio.CancelScope] = []

    async with backend:
      io.blocked_event = _READ_EVENT
      baseline = len(io.events)

      async def send_on() -> None:
        nonlocal completed
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.send(_ON_PACKET)
          except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise
          completed = True

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(send_on)
        await io.blocked_started.wait()
        scopes[0].cancel()
        await _settle()
        # The committed exchange is not abandoned: cancellation waits for the read.
        assert not cancelled.is_set()
        io.release_blocked.set()

      assert cancelled.is_set()
      assert not completed
      assert io.events[baseline:] == _exchange_events(_ON_PACKET)

      io.blocked_event = None
      await backend.turn_off()
      assert io.events[baseline:] == [
        *_exchange_events(_ON_PACKET),
        *_exchange_events(_OFF_PACKET),
      ]

  async def test_lock_is_released_before_pending_cancellation_propagates(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)
    scopes: List[anyio.CancelScope] = []

    async with backend:
      io.blocked_event = _READ_EVENT
      baseline = len(io.events)

      async def send_on() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.send(_ON_PACKET)
          except anyio.get_cancelled_exc_class():
            io.events.append("on.cancelled")
            raise
          pytest.fail("the cancelled send returned")

      # The guard is finite so a transaction that is never handed on fails this test rather
      # than hanging the run; a correct run reaches the assertions in milliseconds.
      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(send_on)
          await io.blocked_started.wait()
          scopes[0].cancel()
          task_group.start_soon(backend.send, _OFF_PACKET)
          await _settle()
          # The cancelled sender still holds the transaction, so the queued command is
          # waiting for it and nothing else has reached the wire.
          assert io.events[baseline:] == _exchange_events(_ON_PACKET)
          io.release_blocked.set()

      # The transaction is handed to the waiting command before the cancelled sender's own
      # caller learns it was cancelled, so a queued command is never delayed by one
      # cancellation delivery.
      assert io.events[baseline:] == [
        *_exchange_events(_ON_PACKET),
        f"write:{_OFF_PACKET.hex()}",
        "on.cancelled",
        _READ_EVENT,
      ]

      io.blocked_event = None

  async def test_exchange_overrun_names_the_hepa_command(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    # This test reads the overrun error's text, not the bound's value, so it shrinks the
    # bound rather than observing the real one and keeps both backends on a real clock. The
    # stall is a finite sleep rather than a blocking event, so a backend that applies no
    # bound at all completes late and fails this test instead of hanging it.
    backend, io = _make_backend(monkeypatch)

    async with backend:
      io.read_stall = 0.5
      baseline = len(io.writes)
      with patch.object(hepa_module, "_HEPA_COMMAND_TIMEOUT", 0.05):
        with pytest.raises(TimeoutError) as error_info:
          await backend.turn_off()
      io.read_stall = 0.0
      assert io.writes[baseline:] == [_OFF_PACKET]

    message = str(error_info.value)
    assert "HEPA command exchange" in message
    assert _OFF_PACKET.hex() in message
    assert "was not confirmed" in message

  async def test_exchange_waits_the_inter_frame_delay(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend:
      assert len(io.write_times) == len(io.read_times)
      exchange = len(io.write_times)
      await backend.turn_off()

    interval = io.read_times[exchange] - io.write_times[exchange]
    # The fixed 0.1 s inter-frame delay separates the write from the reply read. The lower
    # bound is tight because `anyio.sleep` never returns early; the upper bound is loose
    # because a real clock also charges this interval for scheduling, so it only has to
    # exclude a delay an order of magnitude too long.
    assert interval >= 0.09
    assert interval < 0.5

  async def test_turn_on_validation_and_speed_packets_unchanged(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, io = _make_backend(monkeypatch)

    async with backend:
      baseline = len(io.writes)
      for invalid in (-1, 101, 50.5, 1000):
        with pytest.raises(ValueError, match="Intensity is not an int value between 0 and 100"):
          await backend.turn_on(invalid)
      assert io.writes[baseline:] == []

      for intensity, speed_packet in _SPEED_PACKETS.items():
        before = len(io.writes)
        await backend.turn_on(intensity)
        assert io.writes[before:] == [_ON_PACKET, speed_packet]


class TestHamiltonHepaCommandDeadline(AnyioTestBase):
  """Observe the command exchange's bound on a clock that costs no real time.

  A blocked exchange is abandoned exactly when its bound expires, so the time between
  dispatch and abandonment *is* the bound the backend applied. The bound is a cancel-scope
  deadline, which resolves its clock through the running backend, so only a backend-native
  virtual clock can advance it without real waiting. Trio supplies one; asyncio has no
  packaged equivalent, so this one test runs trio-only. The bound is backend-independent,
  and the surrounding transaction behavior keeps dual-backend coverage in
  :class:`TestHamiltonHepaFanTransactions`.
  """

  # This class-level clock is shared by every node in the class and its virtual time
  # accumulates across them, so the assertions below measure intervals rather than absolute
  # instants. Adding an absolute-time assertion here would require a per-node clock.
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_send_deadline_is_finite_and_fixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
    elapsed_per_intensity: List[float] = []

    for intensity, speed_packet in _SPEED_PACKETS.items():
      backend, io = _make_backend(monkeypatch)
      async with backend:
        # Stall only the speed packet's reply, so the exchange under observation is the one
        # the requested intensity selected.
        io.read_stall = _STALLED_READ_SECONDS
        io.stall_after_write = speed_packet
        start = anyio.current_time()
        # The clock is virtual, so this guard costs no real time. A backend that applies no
        # bound cannot be interrupted by it, because the exchange is shielded; it is instead
        # released by the stall itself, which is longer than the guard.
        with anyio.fail_after(_DEADLOCK_GUARD):
          with pytest.raises(TimeoutError, match="HEPA command"):
            await backend.turn_on(intensity)
        elapsed_per_intensity.append(anyio.current_time() - start)
        io.read_stall = 0.0
        io.stall_after_write = None

    for elapsed in elapsed_per_intensity:
      # Both against the constant, so the two cannot drift apart, and against its literal
      # value, so they cannot be moved together.
      assert elapsed == pytest.approx(hepa_module._HEPA_COMMAND_TIMEOUT, abs=0.5)
      assert elapsed == pytest.approx(30.0, abs=0.5)
    # The bound is a fixed constant, so it must not vary with the requested intensity.
    assert elapsed_per_intensity[1] == pytest.approx(elapsed_per_intensity[0], abs=0.5)
    assert elapsed_per_intensity[2] == pytest.approx(elapsed_per_intensity[0], abs=0.5)

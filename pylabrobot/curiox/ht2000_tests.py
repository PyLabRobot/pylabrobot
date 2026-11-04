from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.curiox import ht2000 as ht2000_module
from pylabrobot.curiox.ht2000 import CurioxHT2000, CurioxHT2000Error
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase

ReplyEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]
WriteHook = Callable[[], Awaitable[None]]


def ping_reply(mode: str = "0", status: str = "0", error: str = "00") -> bytes:
  """Return a literal 15-byte ping reply."""
  buf = bytearray(b"\x00" * 15)
  buf[8] = ord(mode)
  buf[9] = ord(status)
  buf[10:12] = error.encode("ascii")
  return bytes(buf)


def ack_reply(status: str = "0") -> bytes:
  """Return a literal 11-byte acknowledgement."""
  buf = bytearray(b"\x00" * 11)
  buf[7] = ord(status)
  return bytes(buf)


def report_reply(
  mode: str = "0",
  status: str = "1",
  tray_out: bool = True,
  loaded: bool = True,
) -> bytes:
  """Return a literal 42-byte status report."""
  buf = bytearray(b"\x00" * 42)
  buf[8] = ord(mode)
  buf[9] = ord(status)
  buf[12] = ord("1" if tray_out else "0")
  buf[14] = ord("1" if loaded else "0")
  return bytes(buf)


class FakeHT2000Serial(HasLifespan):
  """Replay fixed-length replies and record lifecycle and protocol events."""

  def __init__(
    self,
    replies: Optional[List[ReplyEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
    after_write: Optional[Dict[str, WriteHook]] = None,
    read_chunk_size: Optional[int] = None,
  ) -> None:
    super().__init__()
    self.replies: Deque[ReplyEffect] = deque(replies or [])
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.after_write = after_write or {}
    self.read_chunk_size = read_chunk_size
    self.port = "FAKE"
    self.writes: List[bytes] = []
    self._pending: Deque[ReplyEffect] = deque()
    self._rx = bytearray()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeHT2000Serial does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error
    if self.block_entry is not None:
      self.block_entry.set()
      await anyio.sleep_forever()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    payload = data[7:-3].decode("ascii")
    self.writes.append(data)
    self.events.append(f"write:{payload}")
    if self.replies:
      self._pending.append(self.replies.popleft())
    hook = self.after_write.get(payload)
    if hook is not None:
      await hook()

  async def read(self, num_bytes: int = 1) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self._rx and self._pending:
      effect = self._pending.popleft()
      if isinstance(effect, BaseException):
        raise effect
      reply = await effect() if callable(effect) else effect
      self._rx.extend(reply)
      self.events.append("reply.committed")
    count = num_bytes if self.read_chunk_size is None else min(num_bytes, self.read_chunk_size)
    chunk = bytes(self._rx[:count])
    del self._rx[:count]
    if not chunk:
      raise anyio.EndOfStream
    if chunk and not self._rx:
      self.events.append("reply.consumed")
    return chunk


def make_device(
  replies: Optional[List[ReplyEffect]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  after_write: Optional[Dict[str, WriteHook]] = None,
  timeout: float = 0.05,
  read_chunk_size: Optional[int] = None,
) -> tuple[CurioxHT2000, FakeHT2000Serial]:
  """Build an HT2000 backed by a hardware-free native serial resource."""
  device = CurioxHT2000(
    port="FAKE",
    timeout=timeout,
    wash_start_settle=0,
    prime_settle=0,
    mode_switch_settle=0,
    set_settle=0,
    poll_interval=0,
  )
  fake_io = FakeHT2000Serial(
    replies,
    events,
    entry_error,
    exit_error,
    block_entry,
    after_write,
    read_chunk_size,
  )
  device.io = cast(Serial, fake_io)
  return device, fake_io


def written_payloads(fake_io: FakeHT2000Serial) -> List[str]:
  """Return every written frame's ASCII payload in order."""
  return [frame[7:-3].decode("ascii") for frame in fake_io.writes]


class TestHT2000Frames:
  def test_build_frame_ping(self) -> None:
    assert CurioxHT2000._build_frame("0") == bytes(
      [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x01, 0x30, 0x00, 0x31, 0xFF]
    )

  def test_build_frame_wash(self) -> None:
    assert CurioxHT2000._build_frame("210") == bytes(
      [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x03, 0x32, 0x31, 0x30, 0x00, 0x96, 0xFF]
    )


class TestHT2000Protocol(AnyioTestBase):
  async def test_eof_in_fixed_length_reply_reports_partial_count(self) -> None:
    """A closed serial stream cannot look like a complete reply."""
    device, fake = make_device()
    fake._rx.extend(b"A")
    with pytest.raises(CurioxHT2000Error, match="expected 2 bytes, got 1"):
      await device._read_exact(2)

  async def test_ping_ready(self) -> None:
    device, _ = make_device([ping_reply(mode="0", status="0")], read_chunk_size=2)

    assert await device.ping() == ("ready", "operation", None)

  async def test_ping_error_decodes_code(self) -> None:
    device, _ = make_device([ping_reply(status="2", error="01")])

    status, _mode, error = await device.ping()

    assert status == "error"
    assert error == "No plate."

  async def test_wash_uploads_parameters_and_runs(self) -> None:
    device, fake_io = make_device(
      [
        ping_reply(mode="0", status="0"),
        ack_reply(),
        ack_reply(),
        report_reply(status="1", tray_out=True, loaded=True),
        report_reply(status="0", tray_out=True, loaded=True),
      ]
    )

    await device.wash(wash_number=5, initial_volume=40, flow_rate=8, channel=2)

    params = "1050400802025060090000012"
    assert written_payloads(fake_io) == ["0", params, "210", "4", "4"]

  async def test_wash_waits_for_cycle_completion(self) -> None:
    device, fake_io = make_device(
      [
        ping_reply(mode="0", status="0"),
        ack_reply(),
        ack_reply(),
        report_reply(status="1", tray_out=True, loaded=True),
        report_reply(status="1", tray_out=True, loaded=True),
        report_reply(status="0", tray_out=True, loaded=True),
      ]
    )

    await device.wash()

    assert written_payloads(fake_io)[2:] == ["210", "4", "4", "4"]

  async def test_wait_for_wash_raises_on_missing_plate(self) -> None:
    device, _ = make_device(
      [
        ping_reply(mode="0", status="0"),
        ack_reply(),
        ack_reply(),
        report_reply(status="1", tray_out=True, loaded=False),
      ]
    )

    with pytest.raises(CurioxHT2000Error, match="No plate on the tray"):
      await device.wash()

  async def test_move_tray_out_when_already_out_is_noop(self) -> None:
    device, fake_io = make_device([report_reply(tray_out=True, loaded=True)])

    await device.move_tray_out()

    assert written_payloads(fake_io) == ["4"]

  async def test_move_tray_out_toggles_when_in(self) -> None:
    device, fake_io = make_device(
      [
        report_reply(tray_out=False, loaded=True),
        ack_reply(),
        report_reply(tray_out=True, loaded=True),
      ]
    )

    await device.move_tray_out()

    assert written_payloads(fake_io) == ["4", "211", "4"]

  async def test_move_tray_in_toggles_when_out(self) -> None:
    device, fake_io = make_device(
      [
        report_reply(tray_out=True, loaded=True),
        ack_reply(),
        report_reply(tray_out=False, loaded=True),
      ]
    )

    await device.move_tray_in()

    assert written_payloads(fake_io) == ["4", "211", "4"]

  async def test_move_tray_raises_if_position_not_reached(self) -> None:
    device, _ = make_device(
      [
        report_reply(tray_out=False, loaded=True),
        ack_reply(),
        report_reply(tray_out=False, loaded=True),
      ]
    )

    with pytest.raises(CurioxHT2000Error, match="Tray did not reach"):
      await device.move_tray_out()

  async def test_enquire_report_decodes_tray(self) -> None:
    device, _ = make_device([report_reply(status="1", tray_out=False, loaded=True)])

    report = await device.enquire_report()

    assert report.tray_position == "in"
    assert report.tray_loaded

  @pytest.mark.parametrize(
    "override",
    [
      {"wash_number": 0},
      {"wash_number": 20},
      {"initial_volume": 100},
      {"flow_rate": 1},
      {"channel": 6},
      {"channel": 10},
    ],
  )
  async def test_wash_parameter_validation(self, override: Dict[str, int]) -> None:
    device, fake_io = make_device()
    parameters = {"wash_number": 3, "initial_volume": 50, "flow_rate": 5, "channel": 0}
    parameters.update(override)

    with pytest.raises(ValueError):
      await device._set_wash_parameters(**parameters)

    assert fake_io.writes == []


class TestHT2000Lifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert CurioxHT2000.__aenter__ is HasLifespan.__aenter__
    assert CurioxHT2000.__aexit__ is HasLifespan.__aexit__
    assert CurioxHT2000.setup is HasLifespan.setup
    assert CurioxHT2000.stop is HasLifespan.stop

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    device, _ = make_device(events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pytest.fail("failed serial entry reached body")

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]

  async def test_ping_failure_closes_serial(self) -> None:
    events: List[str] = []
    device, _ = make_device([ping_reply(status="2", error="01")], events)

    with pytest.raises(CurioxHT2000Error, match="reported an error on connect: No plate"):
      async with device:
        pytest.fail("failed ping reached body")

    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_cancelled_ping_consumes_reply_before_serial_close(self) -> None:
    events: List[str] = []
    read_started = anyio.Event()
    release_reply = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def blocked_ping() -> bytes:
      read_started.set()
      await release_reply.wait()
      events.append("ping.released")
      return ping_reply()

    device, _ = make_device([blocked_ping], events)

    async def enter_device() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with device:
            pytest.fail("cancelled ping reached body")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_device)
      with anyio.fail_after(0.2):
        await read_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_reply.set()

    assert events.index("reply.consumed") < events.index("serial.exit")
    assert events.index("serial.exit") < events.index("cancelled")

  async def test_body_cancellation_closes_serial_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, _ = make_device([ping_reply()], events)

    async def use_device() -> None:
      try:
        async with device:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_device)
      with anyio.fail_after(0.2):
        await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-2:] == ["serial.exit", "cancelled"]
    assert not device.is_active

  async def test_sequential_reuse_enters_and_pings_twice(self) -> None:
    events: List[str] = []
    device, fake_io = make_device([ping_reply(), ping_reply()], events)

    async with device:
      pass
    async with device:
      pass

    assert written_payloads(fake_io) == ["0", "0"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, _ = make_device([ping_reply()], events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert events[-1] == "serial.exit"
    assert not device.is_active

  async def test_rejects_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached body")

    assert events == []


class TestHT2000Transactions(AnyioTestBase):
  async def test_complete_transactions_do_not_interleave(self) -> None:
    events: List[str] = []
    first_read_started = anyio.Event()
    release_first_reply = anyio.Event()
    second_started = anyio.Event()

    async def blocked_ping() -> bytes:
      first_read_started.set()
      await release_first_reply.wait()
      return ping_reply()

    device, fake_io = make_device([blocked_ping, ack_reply()], events)

    async def second_command() -> None:
      second_started.set()
      await device.home()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(device.ping)
      await first_read_started.wait()
      task_group.start_soon(second_command)
      await second_started.wait()
      await anyio.sleep(0)
      try:
        assert written_payloads(fake_io) == ["0"]
      finally:
        release_first_reply.set()

    assert written_payloads(fake_io) == ["0", "201"]
    assert events.index("reply.consumed") < events.index("write:201")

  @pytest.mark.parametrize(
    ("name", "payload"),
    [
      ("home", "201"),
      ("tray toggle", "211"),
      ("aspirator drain", "231"),
      ("spill tray drain", "232"),
      ("prime", "221"),
      ("parameter upload", "1030500500025060090000012"),
    ],
  )
  async def test_committed_operation_consumes_ack_before_cancellation(
    self, name: str, payload: str
  ) -> None:
    del name
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, fake_io = make_device([ack_reply()], events, after_write={payload: hold_write})

    async def operation() -> None:
      if payload == "201":
        await device.home()
      elif payload == "211":
        await device._toggle_tray()
      elif payload == "231":
        await device.drain_aspirator()
      elif payload == "232":
        await device.drain_spill_tray()
      elif payload == "221":
        await device.prime()
      else:
        await device._set_wash_parameters(3, 50, 5, 0)

    async def cancel_operation() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await operation()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(cancel_operation)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert written_payloads(fake_io) == [payload]
    assert events[-2:] == ["reply.consumed", "cancelled"]

  async def test_committed_mode_switch_consumes_ack_before_cancellation(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, fake_io = make_device(
      [ping_reply(mode="1"), ack_reply()], events, after_write={"202": hold_write}
    )

    async def run_wash() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.wash()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_wash)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert written_payloads(fake_io) == ["0", "202"]
    assert events[-2:] == ["reply.consumed", "cancelled"]

  async def test_committed_wash_start_consumes_ack_before_cancellation(self) -> None:
    events: List[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_write() -> None:
      write_started.set()
      await release_write.wait()

    device, fake_io = make_device(
      [
        ping_reply(),
        ack_reply(),
        ack_reply(),
        report_reply(status="1"),
        report_reply(status="0"),
      ],
      events,
      after_write={"210": hold_write},
    )

    async def run_wash() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.wash()
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_wash)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_write.set()

    assert written_payloads(fake_io) == [
      "0",
      "1030500500025060090000012",
      "210",
      "4",
      "4",
    ]
    assert events[-2:] == ["reply.consumed", "cancelled"]

  async def test_cancelled_wash_reconciles_bounded_completion_poll(self) -> None:
    events: List[str] = []
    poll_started = anyio.Event()
    release_poll = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def blocked_running_report() -> bytes:
      poll_started.set()
      await release_poll.wait()
      return report_reply(status="1")

    device, fake_io = make_device(
      [
        ping_reply(),
        ack_reply(),
        ack_reply(),
        blocked_running_report,
        report_reply(status="0"),
      ],
      events,
    )

    async def run_wash() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.wash(max_operation_duration=1)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_wash)
      await poll_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "cancelled" not in events
      release_poll.set()

    assert written_payloads(fake_io)[-2:] == ["4", "4"]
    assert events[-2:] == ["reply.consumed", "cancelled"]

  async def test_pending_cancellation_before_wash_shield_writes_no_standard_wash(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    original_sleep = anyio.sleep
    scopes: List[anyio.CancelScope] = []

    async def cancel_after_settle(delay: float) -> None:
      if delay == 17:
        scopes[0].cancel()
        return
      await original_sleep(delay)

    monkeypatch.setattr(ht2000_module.anyio, "sleep", cancel_after_settle)
    device, fake_io = make_device(
      [
        ping_reply(),
        ack_reply(),
        ack_reply(),
        report_reply(status="1"),
        report_reply(status="0"),
      ]
    )
    device.set_settle = 17
    returned = False

    with anyio.CancelScope() as scope:
      scopes.append(scope)
      await device.wash()
      returned = True

    assert not returned
    assert written_payloads(fake_io) == ["0", "1030500500025060090000012"]

  async def test_pending_cancellation_writes_no_command(self) -> None:
    device, fake_io = make_device([ack_reply()])
    returned = False

    with anyio.CancelScope() as scope:
      scope.cancel()
      await device.home()
      returned = True

    assert not returned
    assert fake_io.writes == []

  async def test_ack_reconciliation_propagates_nested_transport_timeout(self) -> None:
    failure = TimeoutError("transport read timed out")
    device, _ = make_device([failure], timeout=1)

    with pytest.raises(TimeoutError) as error_info:
      await device.home()

    assert error_info.value is failure

  async def test_ack_deadline_overlap_preserves_transport_timeout(self) -> None:
    failure = TimeoutError("transport timeout after driver deadline")

    async def delayed_transport_timeout() -> bytes:
      with anyio.CancelScope(shield=True):
        await anyio.sleep(0.02)
      raise failure

    device, _ = make_device([delayed_transport_timeout], timeout=0.01)

    with pytest.raises(TimeoutError) as error_info:
      await device.home()

    assert error_info.value is failure

  async def test_ack_reconciliation_translates_its_own_deadline(self) -> None:
    async def blocked_reply() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    device, _ = make_device([blocked_reply], timeout=0.01)

    with pytest.raises(CurioxHT2000Error, match="No acknowledgement from HT2000"):
      await device.home()

  async def test_wash_poll_propagates_nested_transport_timeout(self) -> None:
    failure = TimeoutError("transport read timed out")
    device, _ = make_device([failure], timeout=1)

    with pytest.raises(TimeoutError) as error_info:
      await device._wait_for_wash(1)

    assert error_info.value is failure

  async def test_wash_deadline_overlap_preserves_transport_timeout(self) -> None:
    failure = TimeoutError("transport timeout after wash deadline")

    async def delayed_transport_timeout() -> bytes:
      with anyio.CancelScope(shield=True):
        await anyio.sleep(0.02)
      raise failure

    device, _ = make_device([delayed_transport_timeout], timeout=1)

    with pytest.raises(TimeoutError) as error_info:
      await device._wait_for_wash(0.01)

    assert error_info.value is failure

  async def test_wash_poll_translates_its_own_deadline(self) -> None:
    device, _ = make_device([report_reply(status="1")])

    with pytest.raises(CurioxHT2000Error, match="Timed out waiting for the wash to complete"):
      await device._wait_for_wash(0)

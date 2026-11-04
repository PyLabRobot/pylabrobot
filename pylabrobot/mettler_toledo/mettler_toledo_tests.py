"""Software-only MT-SICS serial ownership and transaction regressions."""

from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.mettler_toledo.scales import MettlerToledoError, MettlerToledoResponse, MTSICSDriver
from pylabrobot.testing.concurrency import AnyioTestBase

LineEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]

STARTUP: List[LineEffect] = [
  b'I4 A "SN"\r\n',
  b"I0 B 0 @\r\n",
  b"I0 B 0 M21\r\n",
  b"I0 B 0 I2\r\n",
  b"I0 B 0 I3\r\n",
  b"I0 A 0 I0\r\n",
  b'I2 A "WXS205SDU WXA-Bridge 220.00900 g"\r\n',
  b'I2 A "WXS205SDU WXA-Bridge 220.00900 g"\r\n',
  b'I3 A "1.10 18.6.4"\r\n',
  b"M21 A\r\n",
]


class FakeSerialIO(HasLifespan):
  """Replay MT-SICS lines and record the order of serial operations."""

  def __init__(
    self,
    lines: Optional[List[LineEffect]] = None,
    events: Optional[List[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
  ) -> None:
    super().__init__()
    self.lines = list(lines or [])
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.writes: List[bytes] = []
    self.port = "FAKE"
    self.human_readable_device_name = "Fake scale"

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
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
    self.writes.append(data)
    self.events.append(f"write:{data!r}")

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("buffer.reset")

  async def readline(self) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self.lines:
      return b""
    effect = self.lines.pop(0)
    if isinstance(effect, BaseException):
      raise effect
    line = await effect() if callable(effect) else effect
    self.events.append(f"read:{line!r}")
    return line


def make_balance(
  lines: Optional[List[LineEffect]] = None,
  events: Optional[List[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
) -> tuple[MTSICSDriver, FakeSerialIO]:
  """Build a driver with a software-only serial transport."""
  balance = MTSICSDriver(port="FAKE")
  fake_io = FakeSerialIO(lines, events, entry_error, exit_error, block_entry)
  balance.io = cast(Serial, fake_io)
  return balance, fake_io


def ready(serial: bytes = b'I4 A "SN"\r\n') -> List[LineEffect]:
  """Replay entry discovery and the exit reset."""
  return [serial, *STARTUP[1:], serial]


class TestMettlerToledoProtocol(AnyioTestBase):
  async def test_command_frames_and_parses_response(self) -> None:
    balance, io = make_balance([b"D A\r\n"])
    assert await balance.send_command('D "ready"') == [MettlerToledoResponse("D", "A")]
    assert io.writes == [b'D "ready"\r\n']

  async def test_stable_and_dynamic_weight_use_grams(self) -> None:
    balance, io = make_balance([b"S S 12.340 g\r\n", b"SC D -0.125 g\r\n"])
    assert await balance.read_stable_weight() == 12.34
    assert await balance.read_dynamic_weight(1.5) == -0.125
    assert io.writes == [b"S\r\n", b"SC 1500\r\n"]

  @pytest.mark.parametrize(
    ("response", "message"),
    [
      (b"S +\r\n", "overload"),
      (b"S -\r\n", "underload"),
      (b"ES\r\n", "Syntax error"),
      (b"ET\r\n", "Transmission error"),
    ],
  )
  async def test_wire_errors_are_mapped(self, response: bytes, message: str) -> None:
    balance, _ = make_balance([response])
    with pytest.raises(MettlerToledoError, match=message):
      await balance.send_command("S")

  async def test_command_timeout_is_finite(self) -> None:
    balance, _ = make_balance()
    with pytest.raises(TimeoutError):
      await balance.send_command("S", timeout=0)


class TestMettlerToledoLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert MTSICSDriver.__aenter__ is HasLifespan.__aenter__
    assert MTSICSDriver.__aexit__ is HasLifespan.__aexit__
    assert MTSICSDriver.setup is HasLifespan.setup
    assert MTSICSDriver.stop is HasLifespan.stop

  async def test_normal_exit_resets_before_serial_closes(self) -> None:
    events: List[str] = []
    balance, io = make_balance(ready(), events)
    async with balance:
      assert balance.serial_number == "SN"
      assert balance.capacity == 220.009
      events.append("body")
    assert io.writes == [
      b"@\r\n",
      b"I0\r\n",
      b"I2\r\n",
      b"I2\r\n",
      b"I3\r\n",
      b"M21 0 0\r\n",
      b"@\r\n",
    ]
    assert events.index("body") < events.index("write:b'@\\r\\n'", events.index("body"))
    assert events[-1] == "serial.exit"
    assert not balance.is_active

  async def test_serial_entry_failure_rolls_back(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial entry failed")
    balance, _ = make_balance(events=events, entry_error=failure)
    with pytest.raises(RuntimeError) as info:
      async with balance:
        pytest.fail("failed serial entry reached body")
    assert info.value is failure
    assert events == ["serial.enter", "serial.exit"]

  async def test_discovery_failure_resets_and_closes(self) -> None:
    events: List[str] = []
    failure = RuntimeError("discovery failed")
    balance, io = make_balance([STARTUP[0], failure, STARTUP[0]], events)
    with pytest.raises(RuntimeError) as info:
      async with balance:
        pytest.fail("failed discovery reached body")
    assert info.value is failure
    assert io.writes == [b"@\r\n", b"I0\r\n", b"@\r\n"]
    assert events[-1] == "serial.exit"

  async def test_exit_reset_io_failure_still_closes_serial(self) -> None:
    events: List[str] = []
    balance, io = make_balance([*STARTUP, OSError("disconnected")], events)
    async with balance:
      pass
    assert io.writes[-1] == b"@\r\n"
    assert events[-1] == "serial.exit"
    assert not balance.is_active

  async def test_cancelled_serial_entry_rolls_back(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    balance, _ = make_balance(events=events, block_entry=entry_started)

    async def enter() -> None:
      async with balance:
        pytest.fail("cancelled entry reached body")

    async with anyio.create_task_group() as group:
      group.start_soon(enter)
      await entry_started.wait()
      group.cancel_scope.cancel()
    assert events == ["serial.enter", "serial.exit"]

  async def test_cancelled_discovery_reply_reconciles_before_reset_and_close(self) -> None:
    events: List[str] = []
    started, release = anyio.Event(), anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def discovery_reply() -> bytes:
      started.set()
      await release.wait()
      events.append("reply.released")
      return b"I0 A 0 I0\r\n"

    balance, _ = make_balance([STARTUP[0], discovery_reply, *STARTUP[6:], STARTUP[0]], events)

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with balance:
            pytest.fail("cancelled entry reached body")
        except anyio.get_cancelled_exc_class():
          events.append("entry.cancelled")
          raise

    async with anyio.create_task_group() as group:
      group.start_soon(enter)
      await started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "entry.cancelled" not in events
      release.set()
    assert events.index("reply.released") < events.index("serial.exit")
    assert events.index("serial.exit") < events.index("entry.cancelled")

  async def test_cancelled_body_resets_before_close(self) -> None:
    events: List[str] = []
    started = anyio.Event()
    balance, _ = make_balance(ready(), events)

    async def use() -> None:
      try:
        async with balance:
          started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("body.cancelled")
        raise

    async with anyio.create_task_group() as group:
      group.start_soon(use)
      await started.wait()
      group.cancel_scope.cancel()
    assert events[-2:] == ["serial.exit", "body.cancelled"]
    assert events.index("write:b'@\\r\\n'", events.index("write:b'M21 0 0\\r\\n'")) < events.index(
      "serial.exit"
    )

  async def test_sequential_reuse_discovers_and_closes_twice(self) -> None:
    events: List[str] = []
    balance, io = make_balance(ready(b'I4 A "SN-1"\r\n') + ready(b'I4 A "SN-2"\r\n'), events)
    async with balance:
      assert balance.serial_number == "SN-1"
    async with balance:
      assert balance.serial_number == "SN-2"
    assert io.writes.count(b"@\r\n") == 4
    assert events.count("serial.enter") == events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates(self) -> None:
    failure = RuntimeError("serial close failed")
    balance, _ = make_balance(ready(), exit_error=failure)
    with pytest.raises(RuntimeError) as info:
      async with balance:
        pass
    assert info.value is failure
    assert not balance.is_active

  async def test_rejects_unknown_options_before_serial_entry(self) -> None:
    events: List[str] = []
    balance, _ = make_balance(events=events)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with balance.lifespan(unknown=True):
        pytest.fail("invalid entry reached body")
    assert events == []


class TestMettlerToledoTransactions(AnyioTestBase):
  async def test_cancelled_multiline_request_drains_before_next_write(self) -> None:
    events: List[str] = []
    started, release = anyio.Event(), anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def final_reply() -> bytes:
      started.set()
      await release.wait()
      events.append("final.released")
      return b"I0 A 0 I0\r\n"

    balance, io = make_balance([b"I0 B 0 S\r\n", final_reply, b"S S 2.000 g\r\n"], events)

    async def first() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await balance.send_command("I0")
        except anyio.get_cancelled_exc_class():
          events.append("first.cancelled")
          raise

    async def second() -> None:
      assert await balance.send_command("S") == [MettlerToledoResponse("S", "S", ["2.000", "g"])]

    async with anyio.create_task_group() as group:
      group.start_soon(first)
      await started.wait()
      group.start_soon(second)
      scopes[0].cancel()
      await anyio.sleep(0)
      assert io.writes == [b"I0\r\n"]
      release.set()
    assert events.index("final.released") < events.index("write:b'S\\r\\n'")
    assert "first.cancelled" in events

  async def test_reset_waits_for_in_flight_request_before_clearing_buffer(self) -> None:
    events: List[str] = []
    started, release = anyio.Event(), anyio.Event()

    async def weight_reply() -> bytes:
      started.set()
      await release.wait()
      return b"S S 1.000 g\r\n"

    balance, io = make_balance([weight_reply, b'I4 A "SN"\r\n'], events)

    async def weight() -> None:
      assert await balance.read_weight() == 1.0

    async with anyio.create_task_group() as group:
      group.start_soon(weight)
      await started.wait()
      group.start_soon(balance.reset)
      await anyio.sleep(0)
      assert "buffer.reset" not in events
      release.set()
    assert io.writes == [b"S\r\n", b"@\r\n"]
    assert events.index("read:b'S S 1.000 g\\r\\n'") < events.index("buffer.reset")

  async def test_cancelled_reset_waiting_for_lock_does_not_clear_buffer(self) -> None:
    events: List[str] = []
    started, reset_ready, release = anyio.Event(), anyio.Event(), anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def weight_reply() -> bytes:
      started.set()
      await release.wait()
      return b"S S 1.000 g\r\n"

    balance, io = make_balance([weight_reply], events)

    async def pending_reset() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        reset_ready.set()
        try:
          await balance.reset()
        except anyio.get_cancelled_exc_class():
          events.append("reset.cancelled")
          raise

    async with anyio.create_task_group() as group:
      group.start_soon(balance.read_weight)
      await started.wait()
      group.start_soon(pending_reset)
      await reset_ready.wait()
      assert "buffer.reset" not in events
      scopes[0].cancel()
      release.set()
    assert io.writes == [b"S\r\n"]
    assert "buffer.reset" not in events
    assert "reset.cancelled" in events

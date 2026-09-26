from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.azenta.xpeel import XPeel
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase

LineEffect = Union[bytes, Callable[[], Awaitable[bytes]]]


class FakeSerialIO(HasLifespan):
  """Replay XPeel lines and record serial lifecycle and wire events."""

  def __init__(
    self,
    lines: Optional[List[LineEffect]] = None,
    events: Optional[List[str]] = None,
    exit_error: Optional[BaseException] = None,
    block_entry: Optional[anyio.Event] = None,
  ) -> None:
    super().__init__()
    self.lines = list(lines or [])
    self.events = events if events is not None else []
    self.exit_error = exit_error
    self.block_entry = block_entry
    self.writes: List[bytes] = []
    self.reset_count = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    if self.block_entry is not None:
      self.block_entry.set()
      await anyio.sleep_forever()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def reset_input_buffer(self) -> None:
    self.reset_count += 1
    self.events.append("serial.reset_input")

  async def write(self, data: bytes) -> None:
    self.writes.append(data)
    self.events.append(f"write:{data!r}")

  async def readline(self) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self.lines:
      return b""
    effect = self.lines.pop(0)
    return await effect() if callable(effect) else effect


def make_device(
  lines: Optional[List[LineEffect]] = None,
  events: Optional[List[str]] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  timeout: float = 0.02,
) -> tuple[XPeel, FakeSerialIO]:
  """Build an XPeel backed by a hardware-free serial transport."""
  device = XPeel("FAKE", timeout=timeout)
  fake_io = FakeSerialIO(lines, events, exit_error, block_entry)
  device.io = cast(Serial, fake_io)
  return device, fake_io


class TestXPeelProtocol(AnyioTestBase):
  async def test_command_adds_terminator_and_resets_input(self) -> None:
    device, fake_io = make_device([b"version:1.2\r\n"])

    assert await device.send_command("*version") == ["version:1.2"]
    assert fake_io.writes == [b"*version\r\n"]
    assert fake_io.reset_count == 1

  async def test_ack_only_command_stops_after_ack(self) -> None:
    device, fake_io = make_device([b"*ack\r\n", b"unused\r\n"])

    assert await device.send_command("*moveout", expect_ack=True) == ["*ack"]
    assert len(fake_io.lines) == 1

  async def test_ack_and_ready_command_waits_for_ready_and_describes_error(self) -> None:
    device, _ = make_device([b"*ack\r\n", b"*ready:4\r\n"])

    assert await device.send_command("*sealcheck", wait_for_ready=True) == [
      "*ack",
      "*ready:4 [Seal not removed]",
    ]

  async def test_command_timeout_returns_responses_received_so_far(self) -> None:
    device, _ = make_device([b"*ack\r\n"], timeout=0)

    assert await device.send_command("*reset", wait_for_ready=True) == []

  async def test_status_and_error_parsing(self) -> None:
    device, _ = make_device([b"*stat:0,20,52\r\n"])

    assert await device.request_status() == (0, 20, 52)
    assert XPeel.parse_ready_line("*ready:51") == (
      51,
      "Emergency stop: cover open or hardware problem",
    )
    assert XPeel.describe_error(999) == "Unknown error code 999"
    assert XPeel.parse_ready_line("not-ready") is None


class TestXPeelLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert XPeel.__aenter__ is HasLifespan.__aenter__
    assert XPeel.__aexit__ is HasLifespan.__aexit__
    assert XPeel.setup is HasLifespan.setup
    assert XPeel.stop is HasLifespan.stop

  async def test_normal_exit_closes_serial(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    async with device:
      events.append("body")

    assert events == ["serial.enter", "body", "serial.exit"]
    assert not device.is_active

  async def test_entry_cancellation_closes_serial(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    device, _ = make_device(events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, device)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_body_cancellation_closes_serial_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    device, _ = make_device(events=events)

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
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit", "cancelled"]
    assert not device.is_active

  async def test_sequential_reuse_opens_and_closes_twice(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    async with device:
      pass
    async with device:
      pass

    assert events == ["serial.enter", "serial.exit", "serial.enter", "serial.exit"]

  async def test_serial_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    device, _ = make_device(events=events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with device:
        pass

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not device.is_active

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    device, _ = make_device(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with device.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []


async def _enter_and_wait(device: XPeel) -> None:
  async with device:
    pytest.fail("cancelled entry reached the body")

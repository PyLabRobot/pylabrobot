import logging
from typing import Any, Awaitable, Callable, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.peeling.xpeel_backend import XPeelBackend
from pylabrobot.testing.concurrency import AnyioTestBase

LineEffect = Union[bytes, Callable[[], Awaitable[bytes]]]


class RecordingHandler(logging.Handler):
  """Collect formatted log messages emitted by the backend."""

  def __init__(self) -> None:
    super().__init__()
    self.messages: List[str] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.messages.append(record.getMessage())


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
    self.cancel_after_reset: Optional[anyio.CancelScope] = None

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    await super()._enter_lifespan(stack)
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
    if self.cancel_after_reset is not None:
      self.cancel_after_reset.cancel()

  async def write(self, data: bytes) -> None:
    self.writes.append(data)
    self.events.append(f"write:{data!r}")

  async def readline(self) -> bytes:
    await anyio.lowlevel.checkpoint()
    if not self.lines:
      return b""
    effect = self.lines.pop(0)
    return await effect() if callable(effect) else effect


def make_backend(
  lines: Optional[List[LineEffect]] = None,
  events: Optional[List[str]] = None,
  exit_error: Optional[BaseException] = None,
  block_entry: Optional[anyio.Event] = None,
  timeout: float = 0.02,
) -> tuple[XPeelBackend, FakeSerialIO, RecordingHandler]:
  """Build a legacy XPeel backend with a hardware-free serial transport."""
  recording_logger = logging.Logger("legacy-xpeel-test")
  recording_handler = RecordingHandler()
  recording_logger.addHandler(recording_handler)
  backend = XPeelBackend(port="FAKE", logger=recording_logger, timeout=timeout)
  fake_io = FakeSerialIO(lines, events, exit_error, block_entry)
  backend.io = cast(Serial, fake_io)
  return backend, fake_io, recording_handler


def delayed_line(
  line: bytes,
  started: anyio.Event,
  release: anyio.Event,
  events: Optional[List[str]] = None,
) -> Callable[[], Awaitable[bytes]]:
  """Return one line only after the test releases its response boundary."""

  async def read_delayed_line() -> bytes:
    started.set()
    await release.wait()
    if events is not None:
      events.append("ready.completed")
    return line

  return read_delayed_line


async def _enter_and_wait(backend: XPeelBackend) -> None:
  async with backend:
    pytest.fail("cancelled entry reached the body")


class TestLegacyXPeelProtocol(AnyioTestBase):
  async def test_command_adds_crlf_and_resets_input(self) -> None:
    backend, fake_io, _ = make_backend([b"version:1.2\r\n"])

    assert await backend._send_command("*version") == ["version:1.2"]
    assert fake_io.writes == [b"*version\r\n"]
    assert fake_io.reset_count == 1

  async def test_ack_only_stops_after_ack(self) -> None:
    backend, fake_io, _ = make_backend([b"*ack\r\n", b"unused\r\n"])

    assert await backend._send_command("*moveout", expect_ack=True) == ["*ack"]
    assert fake_io.lines == [b"unused\r\n"]

  async def test_ack_then_ready_waits_for_ready(self, capsys: pytest.CaptureFixture[str]) -> None:
    backend, _, recording_handler = make_backend([b"*ack\r\n", b"*ready:04,00,00\r\n"])

    assert await backend._send_command("*reset", expect_ack=True, wait_for_ready=True) == [
      "*ack",
      "*ready:04,00,00 [Seal not removed]",
    ]
    assert capsys.readouterr().out == (
      "Received: *ack\nReceived: *ready:04,00,00 [Seal not removed]\n"
    )
    assert recording_handler.messages[-3:] == [
      "Sending command: *reset",
      "Received: *ack",
      "Received: *ready:04,00,00 [Seal not removed]",
    ]

  async def test_timeout_returns_lines_received_so_far(self) -> None:
    backend, _, recording_handler = make_backend([b"*ack\r\n"], timeout=0.01)

    assert await backend._send_command("*reset", expect_ack=True, wait_for_ready=True) == ["*ack"]
    assert recording_handler.messages[-1] == "Timed out waiting for response to *reset after 0.01s"

  async def test_status_and_error_parsing(self) -> None:
    backend, _, _ = make_backend([b"*stat:00,20,52\r\n"])

    assert await backend.get_status() == (0, 20, 52)
    assert XPeelBackend.parse_ready_line("*ready:51") == (
      51,
      "Emergency stop: cover open or hardware problem",
    )
    assert XPeelBackend.describe_error(999) == "Unknown error code 999"
    assert XPeelBackend.parse_ready_line("not-ready") is None

  async def test_legacy_operation_command_bytes(self) -> None:
    ready = b"*ready:00,00,00\r\n"
    backend, fake_io, _ = make_backend([item for _ in range(14) for item in (b"*ack\r\n", ready)])
    operations: List[Callable[[], Awaitable[object]]] = [
      backend.reset,
      backend.restart,
      backend.peel,
      backend.seal_check,
      backend.enable_plate_check,
      lambda: backend.enable_plate_check(False),
      backend.get_seal_sensor_status,
      lambda: backend.set_seal_threshold_upper(7),
      lambda: backend.set_seal_threshold_lower(8),
      backend.move_conveyor_out,
      backend.move_conveyor_in,
      backend.move_elevator_down,
      backend.move_elevator_up,
      backend.advance_tape,
    ]

    for operation in operations:
      await operation()

    assert fake_io.writes == [
      b"*reset\r\n",
      b"*restart\r\n",
      b"*xpeel:41\r\n",
      b"*sealcheck\r\n",
      b"*platecheck:y\r\n",
      b"*platecheck:n\r\n",
      b"*sealstat\r\n",
      b"*sealhigher:007\r\n",
      b"*seallower:008\r\n",
      b"*moveout\r\n",
      b"*movein\r\n",
      b"*movedown\r\n",
      b"*moveup\r\n",
      b"*movespool\r\n",
    ]

    tape_backend, tape_io, _ = make_backend([b"*ack\r\n", b"*tape:12,34\r\n"], timeout=0.001)
    assert await tape_backend.get_tape_remaining() == (120, 340)
    assert tape_io.writes == [b"*tapeleft\r\n"]


class TestLegacyXPeelLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert XPeelBackend.setup is HasLifespan.setup
    assert XPeelBackend.stop is HasLifespan.stop

  async def test_normal_exit_closes_serial(self) -> None:
    events: List[str] = []
    backend, _, _ = make_backend(events=events)

    async with backend:
      events.append("body")

    assert events == ["serial.enter", "body", "serial.exit"]
    assert not backend.is_active

  async def test_entry_cancellation_closes_serial(self) -> None:
    events: List[str] = []
    entry_started = anyio.Event()
    backend, _, _ = make_backend(events=events, block_entry=entry_started)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, backend)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit"]
    assert not backend.is_active

  async def test_body_cancellation_closes_before_propagation(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    backend, _, _ = make_backend(events=events)

    async def use_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["serial.enter", "serial.exit", "cancelled"]
    assert not backend.is_active

  async def test_sequential_reuse(self) -> None:
    events: List[str] = []
    backend, _, _ = make_backend(events=events)

    async with backend:
      pass
    async with backend:
      pass

    assert events == ["serial.enter", "serial.exit", "serial.enter", "serial.exit"]

  async def test_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    backend, _, _ = make_backend(events=events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert events == ["serial.enter", "serial.exit"]
    assert not backend.is_active

  async def test_unknown_options_fail_before_serial_entry(self) -> None:
    events: List[str] = []
    backend, _, _ = make_backend(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []


class TestLegacyXPeelTransactions(AnyioTestBase):
  async def test_concurrent_commands_do_not_reset_or_write_before_prior_ready(self) -> None:
    first_response_started = anyio.Event()
    release_first_response = anyio.Event()
    backend, fake_io, _ = make_backend(
      [
        b"*ack\r\n",
        delayed_line(b"*ready:00,00,00\r\n", first_response_started, release_first_response),
        b"*ack\r\n",
        b"*ready:00,00,00\r\n",
      ]
    )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(backend.move_conveyor_out)
      await first_response_started.wait()
      task_group.start_soon(backend.move_conveyor_in)
      await anyio.sleep(0)
      assert fake_io.writes == [b"*moveout\r\n"]
      assert fake_io.reset_count == 1
      release_first_response.set()

    assert fake_io.writes == [b"*moveout\r\n", b"*movein\r\n"]
    assert fake_io.reset_count == 2

  async def test_pre_write_cancellation_does_not_write(self) -> None:
    backend, fake_io, _ = make_backend([b"version:1.2\r\n"])

    with anyio.CancelScope() as scope:
      fake_io.cancel_after_reset = scope
      await backend._send_command("*version")

    assert scope.cancel_called
    assert fake_io.reset_count == 1
    assert fake_io.writes == []

  async def test_cancellation_after_motion_write_reconciles_ready_before_propagating(self) -> None:
    events: List[str] = []
    response_started = anyio.Event()
    release_response = anyio.Event()
    backend, fake_io, _ = make_backend(
      [
        b"*ack\r\n",
        delayed_line(b"*ready:00,00,00\r\n", response_started, release_response, events),
      ],
      events=events,
    )

    async def move() -> None:
      try:
        await backend.move_conveyor_out()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(move)
      await response_started.wait()
      task_group.cancel_scope.cancel()
      with anyio.CancelScope(shield=True):
        await anyio.sleep(0)
        assert "cancelled" not in events
        release_response.set()

    assert events[-2:] == ["ready.completed", "cancelled"]
    assert fake_io.writes == [b"*moveout\r\n"]


class TestXPeelBackendGetStatus(AnyioTestBase):
  async def test_returns_the_three_error_codes(self) -> None:
    backend, _, _ = make_backend([b"*ready:07,20,00\r\n"])

    assert await backend.get_status() == (7, 20, 0)


class TestXPeelBackendPeel(AnyioTestBase):
  async def test_sends_the_adhere_time_as_a_code(self) -> None:
    for adhere_time, code in ((2.5, 1), (5.0, 2), (7.5, 3), (10.0, 4)):
      backend, io, _ = make_backend([b"*ack\r\n", b"*ready:00,00,00\r\n"])

      await backend.peel(begin_location=0, fast=False, adhere_time=adhere_time)

      assert io.writes == [f"*xpeel:4{code}\r\n".encode("ascii")]

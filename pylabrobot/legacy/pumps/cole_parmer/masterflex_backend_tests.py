from typing import Any, List, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.pumps.cole_parmer.masterflex_backend import MasterflexBackend
from pylabrobot.testing.concurrency import AnyioTestBase


class FakeSerialIO(HasLifespan):
  """Record legacy Masterflex serial traffic and lifecycle events without hardware."""

  def __init__(
    self,
    reply: bytes = b"reply",
    events: Optional[List[str]] = None,
    fail_write: Optional[bytes] = None,
    block_write: Optional[bytes] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.reply = reply
    self.events = events if events is not None else []
    self.fail_write = fail_write
    self.block_write = block_write
    self.exit_error = exit_error
    self.writes: List[bytes] = []
    self.port = "FAKE"
    self.write_started = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeSerialIO does not accept lifespan options")
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes) -> None:
    self.events.append(f"write:{data!r}")
    self.writes.append(data)
    if data == self.fail_write:
      raise RuntimeError(f"write failed: {data!r}")
    if data == self.block_write:
      self.write_started.set()
      await anyio.sleep_forever()

  async def read(self) -> bytes:
    await anyio.lowlevel.checkpoint()
    return self.reply


def make_backend(
  reply: bytes = b"reply",
  events: Optional[List[str]] = None,
  fail_write: Optional[bytes] = None,
  block_write: Optional[bytes] = None,
  exit_error: Optional[BaseException] = None,
) -> tuple[MasterflexBackend, FakeSerialIO]:
  """Build a legacy Masterflex backend with a software serial transport."""
  backend = MasterflexBackend("FAKE")
  fake_io = FakeSerialIO(reply, events, fail_write, block_write, exit_error)
  backend.io = cast(Serial, fake_io)
  return backend, fake_io


class TestLegacyMasterflexLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert MasterflexBackend.setup is HasLifespan.setup
    assert MasterflexBackend.stop is HasLifespan.stop

  async def test_initialization_frames_precede_body_and_serial_close(self) -> None:
    events: List[str] = []
    backend, fake_io = make_backend(events=events)

    async with backend:
      events.append("body")

    assert fake_io.writes == [b"\x05", b"\x05P02\r"]
    assert events == [
      "serial.enter",
      "write:b'\\x05'",
      "write:b'\\x05P02\\r'",
      "body",
      "serial.exit",
    ]

  async def test_send_command_awaits_and_returns_reply_bytes(self) -> None:
    backend, fake_io = make_backend(reply=b"ready")

    assert await backend.send_command("H") == b"ready"
    assert fake_io.writes == [b"\x02P02H\r"]

  @pytest.mark.parametrize("failed_frame", [b"\x05", b"\x05P02\r"])
  async def test_initialization_failure_closes_serial(self, failed_frame: bytes) -> None:
    events: List[str] = []
    backend, fake_io = make_backend(events=events, fail_write=failed_frame)

    with pytest.raises(RuntimeError, match="write failed"):
      async with backend:
        pytest.fail("failed initialization reached the body")

    expected = [b"\x05"] if failed_frame == b"\x05" else [b"\x05", b"\x05P02\r"]
    assert fake_io.writes == expected
    assert events[-1] == "serial.exit"

  @pytest.mark.parametrize("blocked_frame", [b"\x05", b"\x05P02\r"])
  async def test_initialization_cancellation_closes_serial(self, blocked_frame: bytes) -> None:
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    backend, fake_io = make_backend(events=events, block_write=blocked_frame)

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          pytest.fail("cancelled initialization reached the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await fake_io.write_started.wait()
      scopes[0].cancel()

    expected = [b"\x05"] if blocked_frame == b"\x05" else [b"\x05", b"\x05P02\r"]
    assert fake_io.writes == expected
    assert events[-1] == "serial.exit"

  async def test_body_cancellation_closes_serial_before_propagating(self) -> None:
    events: List[str] = []
    body_started = anyio.Event()
    backend, _ = make_backend(events=events)

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

    assert events[-2:] == ["serial.exit", "cancelled"]

  async def test_sequential_reuse_initializes_twice(self) -> None:
    events: List[str] = []
    backend, fake_io = make_backend(events=events)

    async with backend:
      pass
    async with backend:
      pass

    assert fake_io.writes == [b"\x05", b"\x05P02\r", b"\x05", b"\x05P02\r"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_serial_close_failure_propagates(self) -> None:
    events: List[str] = []
    failure = RuntimeError("serial close failed")
    backend, _ = make_backend(events=events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert events[-1] == "serial.exit"

  async def test_unknown_lifespan_options_fail_before_serial_entry(self) -> None:
    events: List[str] = []
    backend, _ = make_backend(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []

  async def test_zero_speed_awaits_halt_command(self) -> None:
    backend, fake_io = make_backend(reply=b"halted")

    await backend.run_continuously(speed=0)

    assert fake_io.writes == [b"\x02P02H\r"]

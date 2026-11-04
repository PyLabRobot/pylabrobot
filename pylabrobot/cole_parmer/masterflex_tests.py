from typing import Any, List, Optional, cast

import anyio
import pytest

from pylabrobot.cole_parmer.masterflex import Masterflex
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.testing.concurrency import AnyioTestBase


class FakeSerialIO(HasLifespan):
  """Record Masterflex serial traffic and lifecycle events without hardware."""

  def __init__(
    self,
    events: Optional[List[str]] = None,
    fail_write: Optional[bytes] = None,
    block_write: Optional[bytes] = None,
  ) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.fail_write = fail_write
    self.block_write = block_write
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

  async def write(self, data: bytes) -> None:
    self.events.append(f"write:{data!r}")
    self.writes.append(data)
    if data == self.fail_write:
      raise RuntimeError(f"write failed: {data!r}")
    if data == self.block_write:
      self.write_started.set()
      await anyio.sleep_forever()

  async def read(self) -> bytes:
    return b"reply"


def make_pump(
  events: Optional[List[str]] = None,
  fail_write: Optional[bytes] = None,
  block_write: Optional[bytes] = None,
) -> tuple[Masterflex, FakeSerialIO]:
  """Build a Masterflex backed by a software serial transport."""
  pump = Masterflex("FAKE")
  fake_io = FakeSerialIO(events, fail_write, block_write)
  pump.io = cast(Serial, fake_io)
  return pump, fake_io


class TestMasterflexLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert Masterflex.__aenter__ is HasLifespan.__aenter__
    assert Masterflex.__aexit__ is HasLifespan.__aexit__
    assert Masterflex.setup is HasLifespan.setup
    assert Masterflex.stop is HasLifespan.stop

  async def test_writes_two_initialization_frames_and_closes_on_normal_exit(self) -> None:
    events: List[str] = []
    pump, fake_io = make_pump(events)

    async with pump:
      events.append("body")

    assert fake_io.writes == [b"\x05", b"\x05P02\r"]
    assert events == [
      "serial.enter",
      "write:b'\\x05'",
      "write:b'\\x05P02\\r'",
      "body",
      "serial.exit",
    ]
    assert not pump.is_active

  @pytest.mark.parametrize("failed_frame", [b"\x05", b"\x05P02\r"])
  async def test_frame_failure_closes_serial(self, failed_frame: bytes) -> None:
    events: List[str] = []
    pump, fake_io = make_pump(events, fail_write=failed_frame)

    with pytest.raises(RuntimeError, match="write failed"):
      async with pump:
        pytest.fail("failed initialization reached the body")

    expected = [b"\x05"] if failed_frame == b"\x05" else [b"\x05", b"\x05P02\r"]
    assert fake_io.writes == expected
    assert events[-1] == "serial.exit"
    assert not pump.is_active

  @pytest.mark.parametrize("blocked_frame", [b"\x05", b"\x05P02\r"])
  async def test_cancellation_during_each_frame_closes_serial(self, blocked_frame: bytes) -> None:
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []
    pump, fake_io = make_pump(events, block_write=blocked_frame)

    async def enter_pump() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with pump:
          pytest.fail("cancelled initialization reached the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_pump)
      await fake_io.write_started.wait()
      scopes[0].cancel()

    expected = [b"\x05"] if blocked_frame == b"\x05" else [b"\x05", b"\x05P02\r"]
    assert fake_io.writes == expected
    assert events[-1] == "serial.exit"
    assert not pump.is_active

  async def test_sequential_reuse_initializes_and_closes_twice(self) -> None:
    events: List[str] = []
    pump, fake_io = make_pump(events)

    async with pump:
      pass
    async with pump:
      pass

    assert fake_io.writes == [b"\x05", b"\x05P02\r", b"\x05", b"\x05P02\r"]
    assert events.count("serial.enter") == 2
    assert events.count("serial.exit") == 2

  async def test_rejects_unknown_lifespan_options_before_serial_entry(self) -> None:
    events: List[str] = []
    pump, _ = make_pump(events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with pump.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []

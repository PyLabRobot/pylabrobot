from collections import deque
from typing import Any, Awaitable, Callable, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.inheco.control_box import InhecoTECControlBox
from pylabrobot.inheco.cpac import inheco_cpac_ultraflat
from pylabrobot.inheco.thermoshake import inheco_thermoshake
from pylabrobot.io.boundary import BoundaryRead
from pylabrobot.io.hid import HID
from pylabrobot.testing.concurrency import AnyioTestBase

ReadEffect = bytes | Callable[[], Awaitable[bytes]]
WriteEffect = Callable[[], Awaitable[None]]


class FakeHID(HasLifespan):
  """Hardware-free HID transport with observable lifecycle events."""

  def __init__(
    self,
    events: Optional[list[str]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    replies: Optional[list[ReadEffect]] = None,
    write_effect: Optional[WriteEffect] = None,
  ) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.replies = deque(replies or [])
    self.write_effect = write_effect
    self.reads: list[tuple[int, Optional[float]]] = []
    self.writes: list[tuple[bytes, bytes]] = []

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeHID does not accept lifespan options")
    self.events.append("hid.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("hid.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def read(self, size: int, timeout: Optional[float] = None) -> bytes:
    self.reads.append((size, timeout))
    self.events.append("read.started")
    await anyio.lowlevel.checkpoint()
    if not self.replies:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    effect = self.replies.popleft()
    response = await effect() if callable(effect) else effect
    self.events.append("read.complete")
    return response

  async def read_to_boundary(self) -> BoundaryRead:
    """Return one fake physical HID report."""
    return BoundaryRead(await self.read(64), True, True, False)

  async def write(self, data: bytes, report_id: bytes = b"\x00") -> None:
    self.events.append("write.started")
    if self.write_effect is not None:
      await self.write_effect()
    else:
      await anyio.lowlevel.checkpoint()
    self.writes.append((data, report_id))
    self.events.append("write.complete")


def make_control_box(
  events: Optional[list[str]] = None,
  entry_error: Optional[BaseException] = None,
  exit_error: Optional[BaseException] = None,
  replies: Optional[list[ReadEffect]] = None,
  write_effect: Optional[WriteEffect] = None,
) -> tuple[InhecoTECControlBox, FakeHID]:
  """Build a control box backed by a software-only HID transport."""
  control_box = InhecoTECControlBox()
  fake_hid = FakeHID(
    events=events,
    entry_error=entry_error,
    exit_error=exit_error,
    replies=replies,
    write_effect=write_effect,
  )
  control_box.io = cast(HID, fake_hid)
  return control_box, fake_hid


class TestInhecoTECControlBoxProtocol(AnyioTestBase):
  async def test_protocol_terminator_assembles_two_complete_reports(self) -> None:
    box, fake = make_control_box(replies=[b"abc#", b"def\x00"])
    async with box:
      assert await box._read_until_end(1) == "abcdef"
    assert len(fake.reads) == 2

  async def test_rejects_incomplete_input_report(self) -> None:
    box, fake = make_control_box()

    async def incomplete() -> BoundaryRead:
      return BoundaryRead(b"abc", True, False, True)

    fake.read_to_boundary = incomplete  # type: ignore[method-assign]
    async with box:
      with pytest.raises(RuntimeError, match="Incomplete Inheco HID report"):
        await box._read_until_end(1)

  async def test_borrowed_children_share_complete_control_box_transactions(self) -> None:
    first_read_started = anyio.Event()
    release_first_reply = anyio.Event()

    async def first_reply() -> bytes:
      first_read_started.set()
      await release_first_reply.wait()
      return b"1rfv0cpacC\x00"

    control_box, fake_hid = make_control_box(replies=[first_reply, b"2rfv0thermoC\x00"])
    cpac = inheco_cpac_ultraflat("cpac", control_box=control_box, index=1)
    thermoshake = inheco_thermoshake("thermoshake", control_box=control_box, index=2)
    results: dict[str, str] = {}

    async def query_cpac() -> None:
      results["cpac"] = await cpac.request_device_info(0)

    async def query_thermoshake() -> None:
      results["thermoshake"] = await thermoshake.request_device_info(0)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(query_cpac)
      await first_read_started.wait()
      task_group.start_soon(query_thermoshake)
      await anyio.sleep(0.01)
      try:
        assert len(fake_hid.writes) == 1
      finally:
        release_first_reply.set()

    assert results == {"cpac": "cpac", "thermoshake": "thermo"}
    assert [bytes(data[:5]) for data, _ in fake_hid.writes] == [
      b"1RFV0",
      b"2RFV0",
    ]

  async def test_pre_cancelled_command_writes_nothing(self) -> None:
    events: list[str] = []
    control_box, fake_hid = make_control_box(events=events, replies=[b"0asd0valueC\x00"])

    with anyio.CancelScope() as scope:
      scope.cancel()
      await control_box.send_command("0ASD")

    assert fake_hid.writes == []
    assert events == []

  async def test_cancellation_during_write_consumes_response_before_propagating(self) -> None:
    events: list[str] = []
    write_started = anyio.Event()
    release_write = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def block_write() -> None:
      write_started.set()
      await release_write.wait()

    control_box, _ = make_control_box(
      events=events,
      replies=[b"0asd0valueC\x00"],
      write_effect=block_write,
    )

    async def send_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await control_box.send_command("0ASD")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send_command)
      await write_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      release_write.set()

    assert events == [
      "write.started",
      "write.complete",
      "read.started",
      "read.complete",
      "cancelled",
    ]

  async def test_cancellation_during_response_consumes_response_before_propagating(self) -> None:
    events: list[str] = []
    read_started = anyio.Event()
    release_response = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def block_response() -> bytes:
      read_started.set()
      await release_response.wait()
      return b"0asd0valueC\x00"

    control_box, _ = make_control_box(events=events, replies=[block_response])

    async def send_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await control_box.send_command("0ASD")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send_command)
      await read_started.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      release_response.set()

    assert events == [
      "write.started",
      "write.complete",
      "read.started",
      "read.complete",
      "cancelled",
    ]

  async def test_read_until_end_joins_continuation_packets(self) -> None:
    control_box, fake_hid = make_control_box(replies=[b"0asd0va#", b"lueC\x00\x00"])

    assert await control_box._read_until_end(timeout=1) == "0asd0valueC"
    assert fake_hid.reads == [(64, None), (64, None)]

  async def test_read_until_end_exhausted_timeout_raises(self) -> None:
    control_box, fake_hid = make_control_box(replies=[b"unused\x00"])

    with pytest.raises(TimeoutError):
      await control_box._read_until_end(timeout=0)

    assert fake_hid.reads == []

  async def test_read_response_ignores_short_and_wrong_echoes(self) -> None:
    control_box, fake_hid = make_control_box(
      replies=[b"0as\x00", b"1asd0wrongC\x00", b"0asd0valueC\x00"]
    )

    assert (
      await control_box._read_response("0ASD", timeout=0.05)  # type: ignore[arg-type]
      == "0asd0valueC"
    )
    assert len(fake_hid.reads) == 3

  async def test_read_response_uses_fractional_timeout_budget(self) -> None:
    blocked = anyio.Event()

    async def block_read() -> bytes:
      blocked.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    control_box, fake_hid = make_control_box(replies=[b"x\x00", block_read])

    with pytest.raises(TimeoutError):
      await control_box._read_response("0ASD", timeout=0.01)  # type: ignore[arg-type]

    assert blocked.is_set()
    assert fake_hid.reads == [(64, None), (64, None)]


class TestInhecoTECControlBoxLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert InhecoTECControlBox.setup is HasLifespan.setup
    assert InhecoTECControlBox.stop is HasLifespan.stop
    assert InhecoTECControlBox.__aenter__ is HasLifespan.__aenter__
    assert InhecoTECControlBox.__aexit__ is HasLifespan.__aexit__

  async def test_hid_entry_failure_does_not_exit(self) -> None:
    events: list[str] = []
    failure = RuntimeError("hid entry failed")
    control_box, _ = make_control_box(events=events, entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with control_box:
        pytest.fail("failed HID entry reached the body")

    assert error_info.value is failure
    assert events == ["hid.enter"]
    assert not control_box.is_active

  async def test_body_cancellation_closes_hid(self) -> None:
    events: list[str] = []
    body_started = anyio.Event()
    control_box, _ = make_control_box(events=events)

    async def use_control_box() -> None:
      try:
        async with control_box:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_control_box)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["hid.enter", "hid.exit", "cancelled"]
    assert not control_box.is_active

  async def test_sequential_reuse(self) -> None:
    events: list[str] = []
    control_box, _ = make_control_box(events=events)

    async with control_box:
      pass
    async with control_box:
      pass

    assert events == ["hid.enter", "hid.exit", "hid.enter", "hid.exit"]

  async def test_hid_close_failure_propagates(self) -> None:
    events: list[str] = []
    failure = RuntimeError("hid close failed")
    control_box, _ = make_control_box(events=events, exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with control_box:
        pass

    assert error_info.value is failure
    assert events == ["hid.enter", "hid.exit"]
    assert not control_box.is_active

  async def test_rejects_lifespan_options_before_hid_entry(self) -> None:
    events: list[str] = []
    control_box, _ = make_control_box(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with control_box.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert events == []

import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional, Union, cast

import anyio
import pytest

from pylabrobot.hamilton.protocol.text.router import (
  HamiltonTask,
  ReplyRouter,
  USBReplyReader,
  WriteNotDispatchedError,
)
from pylabrobot.io.boundary import BoundaryCursor, BoundaryRead
from pylabrobot.io.usb import USB
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

if sys.version_info < (3, 11):
  from exceptiongroup import BaseExceptionGroup


class RouterIO(MockIO):
  """A controllable, loop-neutral transport for reply-router tests."""

  def __init__(self) -> None:
    """Create blocked reads and observable read/write lifecycle events."""
    super().__init__()
    self._reply_send, self._reply_receive = anyio.create_memory_object_stream[
      Union[bytes, BaseException]
    ](float("inf"))
    self.read_started = anyio.Event()
    self.write_started = anyio.Event()
    self.events: list[str] = []
    self.boundary_reads = 0
    self.read.side_effect = self._read_reply
    self.write.side_effect = self._write_command

  async def _read_reply(self, size: int) -> bytes:
    """Wait for a test-provided packet without imposing a transport deadline."""
    assert size == 64
    self.events.append("read")
    self.read_started.set()
    reply = await self._reply_receive.receive()
    if isinstance(reply, BaseException):
      raise reply
    return reply

  async def read_to_boundary(
    self, *, boundary: str, consume_immediate_boundary: bool = True
  ) -> BoundaryRead:
    """Collect test packets through a short packet or ZLP."""
    if boundary == "packet" and not consume_immediate_boundary:
      return BoundaryRead(b"", True, True, False)
    assert boundary == "short_packet_end"
    data = bytearray()
    while True:
      self.boundary_reads += 1
      packet = await self._read_reply(64)
      data.extend(packet)
      if len(packet) < 64:
        return BoundaryRead(bytes(data), True, True, False)

  async def _write_command(self, data: bytes, *, timeout: Optional[int]) -> None:
    """Record when the transport accepts a command."""
    del data, timeout
    self.events.append("write")
    self.write_started.set()

  async def publish(self, reply: Union[str, bytes]) -> None:
    """Make one reply available to the blocked reader."""
    if isinstance(reply, str):
      reply = reply.encode()
    await self._reply_send.send(reply)

  async def fail(self, error: BaseException) -> None:
    """Raise one fatal failure from the blocked reader."""
    await self._reply_send.send(error)


class CursorUSB(RouterIO):
  """Model USB's initial packet mark and retained bytes on interrupted reads."""

  def __init__(self) -> None:
    super().__init__()
    self.cursor = BoundaryCursor(frozenset({"packet"}))
    self.interrupt = False
    self.block_after_packet: Optional[anyio.Event] = None

  async def read_to_boundary(
    self, *, boundary: str, consume_immediate_boundary: bool = True
  ) -> BoundaryRead:
    """Acquire packet marks without changing the cursor on cancellation."""
    while True:
      result = self.cursor.take_to_boundary(
        None,
        boundary=boundary,
        consume_immediate_boundary=consume_immediate_boundary,
        discard=False,
      )
      if result is not None:
        assert isinstance(result, BoundaryRead)
        return result
      packet = await self._read_reply(64)
      self.cursor.append(
        packet,
        frozenset({"packet", "short_packet_end"}) if len(packet) < 64 else frozenset({"packet"}),
      )
      if self.block_after_packet is not None:
        self.block_after_packet.set()
        await anyio.sleep_forever()
      if self.interrupt:
        self.interrupt = False
        raise RuntimeError("interrupted read")


def _parse_id(response: str) -> Optional[int]:
  """Parse the fixed-width identifier used by test replies."""
  marker = response.find("id")
  if marker == -1:
    return None
  value = response[marker + 2 : marker + 6]
  if not value.isdigit():
    raise ValueError("malformed test identifier")
  return int(value)


def _new_router(
  io: RouterIO,
  *,
  raise_for_error: Callable[[str], None] = lambda response: None,
  packet_read_timeout: float = 0.01,
  read_timeout: float = 1,
) -> ReplyRouter:
  """Build a router using the test protocol callbacks."""
  return ReplyRouter(
    io=io,
    module_id_length=2,
    parse_id=_parse_id,
    raise_for_error=raise_for_error,
    packet_read_timeout=packet_read_timeout,
    read_timeout=read_timeout,
    reader=USBReplyReader(cast(USB, io)),
  )


async def _wait_until(predicate: Callable[[], bool]) -> None:
  """Yield until an in-memory state transition has occurred."""
  with anyio.fail_after(1):
    while not predicate():
      await anyio.lowlevel.checkpoint()


@asynccontextmanager
async def _running_router(router: ReplyRouter) -> AsyncIterator[anyio.abc.TaskGroup]:
  """Borrow a running router while test tasks are joined on exit."""
  async with anyio.create_task_group() as task_group:
    async with router.lifespan():
      try:
        yield task_group
      finally:
        task_group.cancel_scope.cancel()


class TestReplyRouting(AnyioTestBase):
  async def test_first_usb_reply_starts_at_packet_mark(self) -> None:
    io = CursorUSB()
    reader = USBReplyReader(cast(USB, io))
    await io.publish("C0AAfirst")
    assert await reader.read_reply() == b"C0AAfirst"
    await io.publish("C0AAsecond")
    assert await reader.read_reply() == b"C0AAsecond"

  async def test_interrupted_usb_read_cannot_route_partial_reply(self) -> None:
    io = CursorUSB()
    reader = USBReplyReader(cast(USB, io))
    io.interrupt = True
    await io.publish(b"C0AA" + b"x" * 60)
    with pytest.raises(RuntimeError, match="interrupted read"):
      await reader.read_reply()
    await io.publish(b"end")
    with pytest.raises(ValueError, match="Incomplete Hamilton USB reply"):
      await reader.read_reply()

  async def test_cancelled_usb_read_cannot_route_partial_reply(self) -> None:
    io = CursorUSB()
    reader = USBReplyReader(cast(USB, io))
    buffered = anyio.Event()
    io.block_after_packet = buffered
    await io.publish(b"C0AA" + b"x" * 60)
    async with anyio.create_task_group() as group:
      group.start_soon(reader.read_reply)
      await buffered.wait()
      group.cancel_scope.cancel()
    io.block_after_packet = None
    await io.publish(b"end")
    with pytest.raises(ValueError, match="Incomplete Hamilton USB reply"):
      await reader.read_reply()

  async def test_rejects_reply_without_observed_start_boundary(self) -> None:
    io = RouterIO()

    async def unaligned(*, boundary: str, consume_immediate_boundary: bool = True) -> BoundaryRead:
      if boundary == "packet" and not consume_immediate_boundary:
        return BoundaryRead(b"", False, True, False)
      assert boundary == "short_packet_end"
      return BoundaryRead(b"C0QMid0001bad", False, True, False)

    io.read_to_boundary = unaligned  # type: ignore[method-assign]
    router = _new_router(io)
    with pytest.raises(BaseExceptionGroup) as error_info:
      async with router.lifespan():
        await anyio.sleep_forever()
    assert any(isinstance(error, ValueError) for error in error_info.value.exceptions)

  async def test_full_usb_packet_followed_by_short_packet_is_one_reply(self) -> None:
    """Do not route a truncated first packet as a complete firmware reply."""
    io = RouterIO()
    router = _new_router(io)
    response = "C0QMid0001" + "x" * 54 + "ok"
    replies: list[Optional[str]] = []

    async def send() -> None:
      replies.append(await router.send("C0QMid0001", 1))

    async with _running_router(router) as group:
      group.start_soon(send)
      await _wait_until(lambda: 1 in router._waiting_tasks_by_id)
      await io.publish(response[:64])
      await io.publish(response[64:])
      await _wait_until(lambda: bool(replies))

    assert replies == [response]

  async def test_exact_full_reply_terminated_by_zlp_does_not_absorb_next_reply(self) -> None:
    io = RouterIO()
    io.read.side_effect = RuntimeError("byte-stream reads cannot frame replies")
    router = _new_router(io)
    first = "C0QMid0001" + "x" * 54
    second = "C0QMid0002ok"
    responses: dict[int, Optional[str]] = {}

    async def send(id_: int) -> None:
      responses[id_] = await router.send(f"C0QMid{id_:04}", id_)

    async with _running_router(router) as group:
      group.start_soon(send, 1)
      group.start_soon(send, 2)
      await _wait_until(lambda: len(router._waiting_tasks_by_id) == 2)
      await io.publish(first)
      await io.publish(b"")  # USB ZLP ends the exact-full reply.
      await io.publish(second)
      await _wait_until(lambda: len(responses) == 2)

    assert responses == {1: first, 2: second}

  async def test_full_then_short_reply_does_not_absorb_adjacent_reply(self) -> None:
    io = RouterIO()
    io.read.side_effect = RuntimeError("byte-stream reads cannot frame replies")
    router = _new_router(io)
    first = "C0QMid0001" + "x" * 54 + "ok"
    second = "C0QMid0002ok"
    responses: dict[int, Optional[str]] = {}

    async def send(id_: int) -> None:
      responses[id_] = await router.send(f"C0QMid{id_:04}", id_)

    async with _running_router(router) as group:
      group.start_soon(send, 1)
      group.start_soon(send, 2)
      await _wait_until(lambda: len(router._waiting_tasks_by_id) == 2)
      await io.publish(first[:64])
      await io.publish(first[64:])
      await io.publish(second)
      await _wait_until(lambda: len(responses) == 2)

    assert responses == {1: first, 2: second}

  async def test_routes_out_of_order_replies_by_id(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    responses: dict[int, Optional[str]] = {}

    async def send(id_: int) -> None:
      responses[id_] = await router.send(f"C0QMid{id_:04}", id_)

    async with _running_router(router) as task_group:
      task_group.start_soon(send, 1)
      task_group.start_soon(send, 2)
      await _wait_until(lambda: len(router._waiting_tasks_by_id) == 2)
      await io.publish("C0QMid0002ok")
      await io.publish("C0QMid0001ok")
      await _wait_until(lambda: len(responses) == 2)

    assert responses == {1: "C0QMid0001ok", 2: "C0QMid0002ok"}

  async def test_rejects_duplicate_pending_id(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    first_response: list[Optional[str]] = []

    async def send_first() -> None:
      first_response.append(await router.send("C0QMid0007", 7))

    async with _running_router(router) as task_group:
      task_group.start_soon(send_first)
      await _wait_until(lambda: 7 in router._waiting_tasks_by_id)
      with pytest.raises(ValueError, match="already pending"):
        await router.send("C0QMid0007", 7)
      assert len(io.write.calls) == 1
      await io.publish("C0QMid0007ok")
      await _wait_until(lambda: bool(first_response))

    assert first_response == ["C0QMid0007ok"]

  async def test_routes_idless_replies_fifo_per_command_prefix(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    responses: list[tuple[str, Optional[str]]] = []

    async def send(name: str) -> None:
      responses.append((name, await router.send("C0AA", None)))

    async with _running_router(router) as task_group:
      task_group.start_soon(send, "first")
      await _wait_until(lambda: len(router._waiting_tasks_by_command.get("C0AA", ())) == 1)
      task_group.start_soon(send, "second")
      await _wait_until(lambda: len(router._waiting_tasks_by_command.get("C0AA", ())) == 2)
      await io.publish("C0AAfirst")
      await _wait_until(lambda: len(responses) == 1)
      await io.publish("C0AAsecond")
      await _wait_until(lambda: len(responses) == 2)

    assert responses == [("first", "C0AAfirst"), ("second", "C0AAsecond")]

  async def test_does_not_cross_route_different_idless_prefixes(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    responses: dict[str, Optional[str]] = {}

    async def send(command: str) -> None:
      responses[command] = await router.send(command, None)

    async with _running_router(router) as task_group:
      task_group.start_soon(send, "C0AA")
      task_group.start_soon(send, "C0BB")
      await _wait_until(lambda: len(router._waiting_tasks_by_command) == 2)
      await io.publish("C0BBsecond")
      await _wait_until(lambda: "C0BB" in responses)
      assert "C0AA" not in responses
      await io.publish("C0AAfirst")
      await _wait_until(lambda: len(responses) == 2)

    assert responses == {"C0AA": "C0AAfirst", "C0BB": "C0BBsecond"}

  async def test_preserves_matching_firmware_exception_identity(self) -> None:
    io = RouterIO()
    firmware_error = RuntimeError("firmware rejected command")

    def raise_for_error(response: str) -> None:
      del response
      raise firmware_error

    router = _new_router(io, raise_for_error=raise_for_error)
    caught: list[BaseException] = []

    async def send() -> None:
      try:
        await router.send("C0QMid0003", 3)
      except BaseException as error:
        caught.append(error)

    async with _running_router(router) as task_group:
      task_group.start_soon(send)
      await _wait_until(lambda: 3 in router._waiting_tasks_by_id)
      await io.publish("C0QMid0003er99")
      await _wait_until(lambda: bool(caught))

    assert caught == [firmware_error]

  async def test_wait_false_writes_without_registering_waiter(self) -> None:
    io = RouterIO()
    router = _new_router(io)

    async with router.lifespan():
      assert await router.send("C0QMid0004", 4, wait=False) is None

    assert io.write.calls == [((b"C0QMid0004",), {"timeout": None})]
    assert router._waiting_tasks_by_id == {}
    assert router._waiting_tasks_by_command == {}

  async def test_malformed_and_unknown_replies_do_not_corrupt_waiters(self) -> None:
    io = RouterIO()
    parsed: list[str] = []

    def parse_id(response: str) -> Optional[int]:
      parsed.append(response)
      return _parse_id(response)

    router = ReplyRouter(
      io,
      2,
      parse_id,
      lambda response: None,
      packet_read_timeout=0.01,
      reader=USBReplyReader(cast(USB, io)),
    )
    responses: dict[int, Optional[str]] = {}

    async def send(id_: int) -> None:
      responses[id_] = await router.send(f"C0QMid{id_:04}", id_)

    async with _running_router(router) as task_group:
      task_group.start_soon(send, 1)
      task_group.start_soon(send, 2)
      await _wait_until(lambda: len(router._waiting_tasks_by_id) == 2)
      await io.publish("C0QMidxxxx")
      await _wait_until(lambda: "C0QMidxxxx" in parsed)
      await io.publish("C0QMid9999unknown")
      await _wait_until(lambda: "C0QMid9999unknown" in parsed)
      assert set(router._waiting_tasks_by_id) == {1, 2}
      await io.publish("C0QMid0001ok")
      await io.publish("C0QMid0002ok")
      await _wait_until(lambda: len(responses) == 2)

    assert responses == {1: "C0QMid0001ok", 2: "C0QMid0002ok"}


class TestWaiterLifecycle(AnyioTestBase):
  async def test_pre_dispatch_cancelled_idless_waiter_does_not_poison_successor(self) -> None:
    io = RouterIO()
    scope: Optional[anyio.CancelScope] = None

    class CancelOnRegisterRouter(ReplyRouter):
      def _register(self, id_: Optional[int], cmd: str) -> HamiltonTask:
        task = super()._register(id_, cmd)
        if scope is not None:
          scope.cancel()
        return task

    router = CancelOnRegisterRouter(
      io, 2, _parse_id, lambda response: None, reader=USBReplyReader(cast(USB, io))
    )
    async with _running_router(router):
      with anyio.CancelScope() as scope:
        await router.send("C0AA", None)
      scope = None
      assert io.write.calls == []

      async def reply_during_write(data: bytes, *, timeout: Optional[int]) -> None:
        await io._write_command(data, timeout=timeout)
        await io.publish("C0AAfresh")

      io.write.side_effect = reply_during_write
      assert await router.send("C0AA", None) == "C0AAfresh"

  async def test_pre_dispatch_failed_idless_waiter_does_not_poison_successor(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    failure = WriteNotDispatchedError("pre-dispatch failure")

    async def fail_before_dispatch(data: bytes, *, timeout: Optional[int]) -> None:
      del data, timeout
      raise failure

    io.write.side_effect = fail_before_dispatch
    async with _running_router(router):
      with pytest.raises(RuntimeError) as exc:
        await router.send("C0AA", None)
      assert exc.value is failure
      io.write.side_effect = io._write_command

      async def reply_during_write(data: bytes, *, timeout: Optional[int]) -> None:
        await io._write_command(data, timeout=timeout)
        await io.publish("C0AAfresh")

      io.write.side_effect = reply_during_write
      assert await router.send("C0AA", None) == "C0AAfresh"

  async def test_uncertain_idless_write_retains_slot_for_late_reply(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    failure = RuntimeError("write outcome unknown")

    async def uncertain_write(data: bytes, *, timeout: Optional[int]) -> None:
      await io._write_command(data, timeout=timeout)
      raise failure

    io.write.side_effect = uncertain_write
    results: list[Optional[str]] = []

    async def send_second() -> None:
      results.append(await router.send("C0AA", None))

    async with _running_router(router) as group:
      with pytest.raises(RuntimeError) as exc:
        await router.send("C0AA", None)
      assert exc.value is failure
      io.write.side_effect = io._write_command
      group.start_soon(send_second)
      await _wait_until(lambda: len(io.write.calls) == 2)
      await io.publish("C0AAstale")
      await io.publish("C0AAfresh")
      await _wait_until(lambda: bool(results))
    assert results == ["C0AAfresh"]

  async def test_timed_out_idless_reply_is_dropped_before_successor(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    results: list[Optional[str]] = []

    async with _running_router(router) as group:
      with pytest.raises(TimeoutError):
        await router.send("C0AA", None, read_timeout=0)

      async def send_second() -> None:
        results.append(await router.send("C0AA", None))

      group.start_soon(send_second)
      await _wait_until(lambda: len(io.write.calls) == 2)
      await io.publish("C0AAstale")
      await _wait_until(lambda: "read" in io.events)
      await io.publish("C0AAfresh")
      await _wait_until(lambda: bool(results))

    assert results == ["C0AAfresh"]

  async def test_cancelled_idless_reply_is_dropped_before_successor(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    cancelled = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    results: list[Optional[str]] = []

    async def send_first() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await router.send("C0AA", None)
        finally:
          cancelled.set()

    async def send_second() -> None:
      results.append(await router.send("C0AA", None))

    async with _running_router(router) as group:
      group.start_soon(send_first)
      await _wait_until(lambda: len(io.write.calls) == 1)
      scopes[0].cancel()
      await cancelled.wait()
      group.start_soon(send_second)
      await _wait_until(lambda: len(io.write.calls) == 2)
      await io.publish("C0AAstale")
      await io.publish("C0AAfresh")
      await _wait_until(lambda: bool(results))

    assert results == ["C0AAfresh"]

  async def test_shutdown_rejects_waiting_and_fire_and_forget_sends(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    with pytest.raises(RuntimeError, match="active reply router"):
      await router.send("C0AA", None)
    with pytest.raises(RuntimeError, match="active reply router"):
      await router.send("C0AA", None, wait=False)
    async with router.lifespan():
      router._stop(anyio.CancelScope())
      with pytest.raises(RuntimeError, match="active reply router"):
        await router.send("C0AA", None, wait=False)
    assert io.write.calls == []

  async def test_waiter_is_registered_before_fast_reply_can_arrive(self) -> None:
    io = RouterIO()
    router = _new_router(io)

    async def reply_during_write(data: bytes, *, timeout: Optional[int]) -> None:
      del data, timeout
      assert 5 in router._waiting_tasks_by_id
      await io.publish("C0QMid0005fast")

    io.write.side_effect = reply_during_write

    async with _running_router(router):
      response = await router.send("C0QMid0005", 5)

    assert response == "C0QMid0005fast"

  async def test_timeout_removes_waiter_immediately(self) -> None:
    io = RouterIO()
    router = _new_router(io)

    async with router.lifespan():
      with pytest.raises(
        TimeoutError,
        match=r"Timeout while waiting for response to command C0QMid0006\.",
      ):
        await router.send("C0QMid0006", 6, read_timeout=0)

    assert router._waiting_tasks_by_id == {}

  async def test_caller_cancellation_removes_waiter_immediately(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    scopes: list[anyio.CancelScope] = []
    finished = anyio.Event()

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await router.send("C0QMid0008", 8)
        finally:
          finished.set()

    async with _running_router(router) as task_group:
      task_group.start_soon(send)
      await _wait_until(lambda: 8 in router._waiting_tasks_by_id)
      scopes[0].cancel()
      await finished.wait()

    assert router._waiting_tasks_by_id == {}

  async def test_write_failure_removes_pre_registered_waiter(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    write_error = RuntimeError("write failed")

    async def fail_write(data: bytes, *, timeout: Optional[int]) -> None:
      del data, timeout
      assert 9 in router._waiting_tasks_by_id
      raise write_error

    io.write.side_effect = fail_write

    async with router.lifespan():
      with pytest.raises(RuntimeError) as exc_info:
        await router.send("C0QMid0009", 9)

    assert exc_info.value is write_error
    assert router._waiting_tasks_by_id == {}

  async def test_reader_stop_fails_and_clears_all_waiters(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    failures: list[BaseException] = []

    async def send(id_: int) -> None:
      try:
        await router.send(f"C0QMid{id_:04}", id_)
      except BaseException as error:
        failures.append(error)

    async with anyio.create_task_group() as task_group:
      async with router.lifespan():
        task_group.start_soon(send, 10)
        task_group.start_soon(send, 11)
        await _wait_until(lambda: len(router._waiting_tasks_by_id) == 2)
      await _wait_until(lambda: len(failures) == 2)

    assert all(str(error) == "Stopping the reply router." for error in failures)
    assert router._waiting_tasks_by_id == {}
    assert router._waiting_tasks_by_command == {}


class TestReaderOwnership(AnyioTestBase):
  async def test_overlapping_lifespans_are_rejected(self) -> None:
    router = _new_router(RouterIO())

    async with router.lifespan():
      with pytest.raises(RuntimeError, match="active lifespan"):
        async with router.lifespan():
          pytest.fail("a second reader entered the same router")

  async def test_exit_before_reader_enters_its_scope_still_joins(self) -> None:
    io = RouterIO()
    entered = anyio.Event()
    release = anyio.Event()
    leave = anyio.Event()
    exited = anyio.Event()

    class DelayedRouter(ReplyRouter):
      async def _run(self, reader_scope: anyio.CancelScope) -> None:
        """Let lifespan exit cancel the reader scope before it is entered."""
        with anyio.CancelScope(shield=True):
          entered.set()
          await release.wait()
        await super()._run(reader_scope)

    router = DelayedRouter(
      io, 2, _parse_id, lambda response: None, reader=USBReplyReader(cast(USB, io))
    )

    async def use_router() -> None:
      async with router.lifespan():
        await leave.wait()
      exited.set()

    async with anyio.create_task_group() as group:
      group.start_soon(use_router)
      await entered.wait()
      leave.set()
      try:
        with anyio.move_on_after(0.01):
          await exited.wait()
        assert not exited.is_set()
      finally:
        release.set()
      await exited.wait()

  async def test_lifespan_starts_and_joins_its_reader(self) -> None:
    io = RouterIO()
    router = _new_router(io)

    async with router.lifespan():
      await io.read_started.wait()
      reads = io.boundary_reads

    await io.publish("C0QMid0001late")
    await anyio.lowlevel.checkpoint()
    assert io.boundary_reads == reads

  async def test_same_router_runs_in_sequential_lifespans(self) -> None:
    io = RouterIO()
    router = _new_router(io)

    async with router.lifespan():
      await io.read_started.wait()

    first_run_reads = io.boundary_reads
    async with router.lifespan():
      await _wait_until(lambda: io.boundary_reads > first_run_reads)

  async def test_reader_uses_deadline_free_reads(self) -> None:
    io = RouterIO()
    router = _new_router(io, packet_read_timeout=0.02)

    async with _running_router(router):
      await io.read_started.wait()

    assert io.boundary_reads > 0
    assert io.read.calls == []

  async def test_fatal_reader_failure_propagates_and_wakes_every_waiter(self) -> None:
    io = RouterIO()
    router = _new_router(io)
    transport_error = RuntimeError("transport disconnected")
    waiter_failures: list[BaseException] = []

    async def send(id_: int) -> None:
      try:
        await router.send(f"C0QMid{id_:04}", id_)
      except BaseException as error:
        waiter_failures.append(error)

    async with anyio.create_task_group() as task_group:
      with pytest.raises(BaseExceptionGroup) as error_info:
        async with router.lifespan():
          task_group.start_soon(send, 12)
          task_group.start_soon(send, 13)
          await _wait_until(lambda: len(router._waiting_tasks_by_id) == 2)
          await io.fail(transport_error)
          await anyio.sleep_forever()

    assert transport_error in error_info.value.exceptions
    assert waiter_failures == [transport_error, transport_error]
    assert router._waiting_tasks_by_id == {}

  async def test_decode_failure_is_fatal(self) -> None:
    io = RouterIO()
    router = _new_router(io)

    with pytest.raises(BaseExceptionGroup) as error_info:
      async with router.lifespan():
        await io.publish(b"\xff")
        await anyio.sleep_forever()

    assert any(isinstance(error, UnicodeDecodeError) for error in error_info.value.exceptions)

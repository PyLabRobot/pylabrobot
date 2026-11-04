"""Tests for loop-agnostic asynchronous I/O test doubles."""

from typing import Any, Dict, List, Tuple

import anyio
import pytest

from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import CustomReadMock, CustomWriteMock, MockIO

Call = Tuple[Tuple[Any, ...], Dict[str, Any]]


class TestMockIO(AnyioTestBase):
  """Exercise mock I/O behavior on asyncio and Trio."""

  async def test_unconfigured_read_waits_instead_of_returning_empty_data(self) -> None:
    """An idle transport must not look like a successful empty read."""
    read = CustomReadMock()
    with anyio.move_on_after(0.01) as scope:
      await read(1)
    assert scope.cancel_called

  async def test_cancelled_read_retains_queued_reply(self) -> None:
    """A cancelled replay-style read must leave its unreturned effect available."""
    read = CustomReadMock()
    read.side_effect = [b"reply"]
    with anyio.CancelScope() as scope:
      scope.cancel()
      await read(5)
    assert await read(5) == b"reply"

  async def test_cancelled_write_does_not_reserve_a_command(self) -> None:
    """Cancelling before dispatch must leave the queued write effect untouched."""
    write = CustomWriteMock()
    write.side_effect = [RuntimeError("dispatched")]
    with anyio.CancelScope() as scope:
      scope.cancel()
      await write(b"not sent")
    with pytest.raises(RuntimeError, match="dispatched"):
      await write(b"sent")

  async def test_records_read_and_write_calls(self) -> None:
    """Record every positional and keyword argument in invocation order."""
    io = MockIO()
    io.read.side_effect = [b"response"]

    assert await io.read(4, timeout=0.5) == b"response"
    await io.write(b"\x00command\xff", timeout=1)

    assert io.read.calls == [((4,), {"timeout": 0.5})]
    assert io.write.calls == [((b"\x00command\xff",), {"timeout": 1})]

  async def test_returns_queued_reads_in_order(self) -> None:
    """Consume queued byte responses from first to last."""
    read = CustomReadMock()
    read.side_effect = [b"first", b"second"]

    assert await read() == b"first"
    assert await read() == b"second"

  async def test_write_calls_retain_exact_bytes(self) -> None:
    """Retain binary payloads without decoding or normalization."""
    write = CustomWriteMock()
    payloads = [b"\x00\r\n", b"\xff\x80\x00"]

    for payload in payloads:
      await write(payload)

    assert [call[0][0] for call in write.calls] == payloads

  async def test_queued_exception_propagates(self) -> None:
    """Raise an exception stored at the next queue position."""
    read = CustomReadMock()
    error = RuntimeError("transport failed")
    read.side_effect = [error]

    with pytest.raises(RuntimeError, match="transport failed") as exc_info:
      await read()

    assert exc_info.value is error

  async def test_custom_side_effects_are_deterministic(self) -> None:
    """Invoke configured read and write callables once per operation."""
    read = CustomReadMock()
    write = CustomWriteMock()
    written: List[bytes] = []

    def read_effect() -> bytes:
      """Return one deterministic response."""
      return b"custom"

    async def write_effect(data: bytes) -> None:
      """Capture one payload after an asynchronous checkpoint."""
      await anyio.lowlevel.checkpoint()
      written.append(data)

    read.side_effect = read_effect
    write.side_effect = write_effect

    assert await read() == b"custom"
    await write(b"payload")
    assert written == [b"payload"]

  async def test_waiting_read_is_released_by_anyio_event(self) -> None:
    """Allow an asynchronous read effect to wait on an AnyIO event."""
    read = CustomReadMock()
    release = anyio.Event()
    results: List[bytes] = []

    async def wait_for_release() -> bytes:
      """Wait until the test releases the pending read."""
      await release.wait()
      return b"released"

    async def receive() -> None:
      """Store the result of the pending read."""
      results.append(await read())

    read.side_effect = wait_for_release

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(receive)
      await anyio.lowlevel.checkpoint()
      assert results == []
      release.set()

    assert results == [b"released"]

  async def test_read_reserves_its_queued_effect_before_yielding(self) -> None:
    """Give a pending read the effect queued when it was called."""
    read = CustomReadMock()
    read.side_effect = [b"queued"]
    results: List[bytes] = []

    async def receive() -> None:
      """Store the response of the pending read."""
      results.append(await read())

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(receive)
      while not read.calls:
        await anyio.lowlevel.checkpoint()
      read.side_effect = [b"replacement"]

    assert results == [b"queued"]

  async def test_write_reserves_its_queued_effect_before_yielding(self) -> None:
    """Give a pending write the effect queued when it was called."""
    write = CustomWriteMock()
    write.side_effect = [b"queued"]
    results: List[bytes] = []

    async def send() -> None:
      """Store the result of the pending write."""
      results.append(await write(b"payload"))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      while not write.calls:
        await anyio.lowlevel.checkpoint()
      write.side_effect = [b"replacement"]

    assert results == [b"queued"]

  async def test_concurrent_reads_receive_queued_effects_in_call_order(self) -> None:
    """Give every concurrent read the effect queued at its recorded position."""
    read = CustomReadMock()
    queued = [bytes([index]) for index in range(8)]
    read.side_effect = list(queued)
    results: Dict[int, bytes] = {}

    async def receive(index: int) -> None:
      """Store the response one concurrent reader receives."""
      results[index] = await read(index)

    async with anyio.create_task_group() as task_group:
      for index in range(len(queued)):
        task_group.start_soon(receive, index)

    call_order = [call[0][0] for call in read.calls]
    assert results == dict(zip(call_order, queued))

  async def test_concurrent_writes_receive_queued_effects_in_call_order(self) -> None:
    """Give every concurrent write the effect queued at its recorded position."""
    write = CustomWriteMock()
    queued = list(range(8))
    write.side_effect = list(queued)
    results: Dict[bytes, int] = {}

    async def send(payload: bytes) -> None:
      """Store the result one concurrent writer receives."""
      results[payload] = await write(payload)

    async with anyio.create_task_group() as task_group:
      for index in range(len(queued)):
        task_group.start_soon(send, bytes([index]))

    call_order = [call[0][0] for call in write.calls]
    assert results == dict(zip(call_order, queued))

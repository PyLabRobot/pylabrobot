"""Socket connection binding, write cancellation, and timeout tests without network I/O."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pylabrobot.io.socket import Socket


class TestSocketWrite(unittest.IsolatedAsyncioTestCase):
  """Exercise production write handling with a controlled StreamWriter."""

  def setUp(self) -> None:
    """Provide an in-memory writer."""
    self.io = Socket("test", "memory-only", 0)
    self.writer = Mock(spec=asyncio.StreamWriter)
    self.io._writer = self.writer

  async def test_cancel_after_drain_completes_still_propagates(self) -> None:
    """Cancellation must win even when drain finished before its waiter resumed."""
    cancellations: list[bool] = []

    def cancel_write(completed: asyncio.Task[None]) -> None:
      """Cancel after drain completes, before its waiter resumes."""
      cancellations.append(task.cancel())

    async def drain() -> None:
      """Complete immediately, as an unbuffered real socket commonly does."""
      drain_task = asyncio.current_task()
      assert drain_task is not None
      drain_task.add_done_callback(cancel_write)

    self.writer.drain.side_effect = drain
    task = asyncio.create_task(self.io.write(b"query"))
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertEqual(cancellations, [True])
    self.writer.write.assert_called_once_with(b"query")

  async def test_timeout_cancels_and_joins_drain(self) -> None:
    """A write deadline leaves no drain task running and does not retransmit."""
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def drain() -> None:
      """Hold drain until timeout cancels it, recording cleanup."""
      entered.set()
      try:
        await asyncio.Event().wait()
      finally:
        finished.set()

    self.writer.drain.side_effect = drain
    with self.assertRaises(TimeoutError):
      await self.io.write(b"query", timeout=0.01)
    self.assertTrue(entered.is_set())
    self.assertTrue(finished.is_set())
    self.writer.write.assert_called_once_with(b"query")

  async def test_cancel_pending_drain_joins_it(self) -> None:
    """Caller cancellation also cleans up a drain that has not completed."""
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def drain() -> None:
      """Hold the writer until caller cancellation."""
      entered.set()
      try:
        await asyncio.Event().wait()
      finally:
        finished.set()

    self.writer.drain.side_effect = drain
    task = asyncio.create_task(self.io.write(b"query"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertTrue(finished.is_set())
    self.writer.write.assert_called_once_with(b"query")

  async def test_drain_failure_is_preserved(self) -> None:
    """A connection error retains its identity without retrying the write."""
    error = ConnectionResetError("peer reset")
    self.writer.drain.side_effect = error
    with self.assertRaises(ConnectionResetError) as caught:
      await self.io.write(b"query")
    self.assertIs(caught.exception, error)
    self.writer.write.assert_called_once_with(b"query")

  async def test_cleanup_error_does_not_replace_timeout(self) -> None:
    """An error raised while cancelling drain must not hide the write deadline."""
    finished = asyncio.Event()

    async def drain() -> None:
      """Fail during timeout cleanup."""
      try:
        await asyncio.Event().wait()
      finally:
        finished.set()
        raise ConnectionResetError("reset during cleanup")

    self.writer.drain.side_effect = drain
    with self.assertRaises(TimeoutError):
      await self.io.write(b"query", timeout=0.01)
    self.assertTrue(finished.is_set())

  async def test_cancellation_during_timeout_cleanup_joins_drain(self) -> None:
    """A caller cancelling during cleanup still waits for drain to stop."""
    cleaning = asyncio.Event()
    finished = asyncio.Event()

    async def drain() -> None:
      """Pause in cancellation cleanup until the caller cancels again."""
      try:
        await asyncio.Event().wait()
      finally:
        cleaning.set()
        try:
          await asyncio.Event().wait()
        finally:
          finished.set()

    self.writer.drain.side_effect = drain
    task = asyncio.create_task(self.io.write(b"query", timeout=0.01))
    await asyncio.wait_for(cleaning.wait(), timeout=1)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertTrue(finished.is_set())

  async def test_success_writes_once_and_finishes_drain(self) -> None:
    """Successful writes finish normally and await their drain."""
    await self.io.write(b"query")
    self.writer.write.assert_called_once_with(b"query")
    self.writer.drain.assert_awaited_once()


class SocketSourceIPTests(unittest.IsolatedAsyncioTestCase):
  async def test_setup_binds_to_source_ip(self):
    socket = Socket(
      human_readable_device_name="test",
      host="192.0.2.10",
      port=7612,
      source_ip="192.0.2.20",
    )

    with patch(
      "pylabrobot.io.socket.asyncio.open_connection",
      new_callable=AsyncMock,
    ) as open_connection:
      open_connection.return_value = (object(), object())
      await socket.setup()

    open_connection.assert_awaited_once_with(
      host="192.0.2.10",
      port=7612,
      ssl=None,
      server_hostname=None,
      local_addr=("192.0.2.20", 0),
    )

  async def test_setup_without_source_ip_uses_default_binding(self):
    socket = Socket(
      human_readable_device_name="test",
      host="192.0.2.10",
      port=7612,
    )

    with patch(
      "pylabrobot.io.socket.asyncio.open_connection",
      new_callable=AsyncMock,
    ) as open_connection:
      open_connection.return_value = (object(), object())
      await socket.setup()

    open_connection.assert_awaited_once_with(
      host="192.0.2.10",
      port=7612,
      ssl=None,
      server_hostname=None,
      local_addr=None,
    )


if __name__ == "__main__":
  unittest.main()

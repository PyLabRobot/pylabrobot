import asyncio
import logging
import queue
import time
import unittest
from typing import Callable, List, Optional, Union

from pylabrobot.hamilton.protocol.text.framing import read_id
from pylabrobot.hamilton.protocol.text.router import ReplyRouter

ROUTER_LOGGER = "pylabrobot.hamilton.protocol.text.router"


class FakeTransport:
  """A link whose replies are queued by the test, and whose writes can be made to fail."""

  def __init__(self, fail_writes: bool = False):
    self.replies: "queue.Queue[bytes]" = queue.Queue()
    self.written: List[bytes] = []
    self.fail_writes = fail_writes

  async def write(self, data: bytes, timeout: Optional[int] = None) -> None:
    if self.fail_writes:
      raise ConnectionError("the write did not go out")
    self.written.append(data)

  async def read(self) -> bytes:
    try:
      return self.replies.get(timeout=0.01)
    except queue.Empty:
      raise TimeoutError from None


class LoopBoundTransport:
  """A link whose reads belong to the loop it was created in, as a socket's streams do.

  Awaiting its `read` from another loop fails. It never times out: an idle link just waits. A
  reply that is an exception is raised by `read` instead of returned, as a closed link would.
  """

  def __init__(self):
    self.replies: "asyncio.Queue[Union[bytes, BaseException]]" = asyncio.Queue()
    self.written: List[bytes] = []
    self.reads_started = 0
    self.read_cancelled = False

  async def write(self, data: bytes, timeout: Optional[int] = None) -> None:
    self.written.append(data)

  async def read(self) -> bytes:
    self.reads_started += 1
    try:
      reply = await self.replies.get()
    except asyncio.CancelledError:
      self.read_cancelled = True
      raise
    if isinstance(reply, BaseException):
      raise reply
    return reply


def is_spontaneous(line: str) -> bool:
  return line[2:4] == "__"


def router(
  transport: Union[FakeTransport, LoopBoundTransport],
  read_timeout: int = 5,
  spontaneous: Optional[Callable[[str], bool]] = None,
  read_on_owner_loop: bool = False,
) -> ReplyRouter:
  def raise_for_error(reply: str) -> None:
    if "er99" in reply:
      raise RuntimeError(reply)

  return ReplyRouter(
    io=transport,  # type: ignore[arg-type]
    module_id_length=2,
    parse_id=read_id,
    raise_for_error=raise_for_error,
    read_timeout=read_timeout,
    is_spontaneous=spontaneous,
    read_on_owner_loop=read_on_owner_loop,
  )


class TestReplyRouter(unittest.IsolatedAsyncioTestCase):
  """Each reply reaches the command waiting for it, and nothing is left waiting that cannot be."""

  async def test_a_reply_reaches_the_command_with_its_id(self):
    transport = FakeTransport()
    r = router(transport)
    r.start()
    try:
      first = asyncio.ensure_future(r.send("C0RTid0001", id_=1))
      second = asyncio.ensure_future(r.send("C0RWid0002", id_=2))
      await asyncio.sleep(0.05)
      transport.replies.put(b"C0RWid0002er00/00rw000")
      transport.replies.put(b"C0RTid0001er00/00rt0")
      self.assertEqual(await asyncio.wait_for(first, 2), "C0RTid0001er00/00rt0")
      self.assertEqual(await asyncio.wait_for(second, 2), "C0RWid0002er00/00rw000")
      self.assertEqual(r._waiting_tasks, [])
    finally:
      r.stop()

  async def test_an_error_reply_raises_on_its_command(self):
    transport = FakeTransport()
    r = router(transport)
    r.start()
    try:
      sent = asyncio.ensure_future(r.send("C0ZAid0003", id_=3))
      await asyncio.sleep(0.05)
      transport.replies.put(b"C0ZAid0003er99/00")
      with self.assertRaises(RuntimeError):
        await asyncio.wait_for(sent, 2)
    finally:
      r.stop()

  async def test_an_unanswered_command_times_out_and_is_taken_off(self):
    r = router(FakeTransport(), read_timeout=0)
    r.start()
    try:
      with self.assertRaises(TimeoutError):
        await asyncio.wait_for(r.send("C0QWid0004", id_=4), 2)
      self.assertEqual(r._waiting_tasks, [])
    finally:
      r.stop()

  async def test_a_failed_write_leaves_nothing_waiting(self):
    r = router(FakeTransport(fail_writes=True))
    r.start()
    try:
      with self.assertRaises(ConnectionError):
        await r.send("C0QWid0005", id_=5)
      self.assertEqual(r._waiting_tasks, [])
    finally:
      r.stop()

  async def test_stop_fails_every_command_still_waiting(self):
    r = router(FakeTransport())
    r.start()
    sent = asyncio.ensure_future(r.send("C0QWid0006", id_=6))
    await asyncio.sleep(0.05)
    r.stop()
    with self.assertRaises(RuntimeError):
      await asyncio.wait_for(sent, 2)
    self.assertEqual(r._waiting_tasks, [])


class TestSpontaneousMessages(unittest.IsolatedAsyncioTestCase):
  """A line the machine sent unasked is discarded, and never answers a command."""

  SPONTANEOUS = b'C0__id0sm"Spontan Message"'

  async def test_a_spontaneous_line_does_not_answer_the_waiting_command(self):
    transport = FakeTransport()
    r = router(transport, spontaneous=is_spontaneous)
    r.start()
    try:
      with self.assertNoLogs(ROUTER_LOGGER, level=logging.WARNING):
        sent = asyncio.ensure_future(r.send("C0QWid0007", id_=7))
        await asyncio.sleep(0.05)
        transport.replies.put(self.SPONTANEOUS)
        await asyncio.sleep(0.1)
        self.assertFalse(sent.done())
        self.assertEqual(len(r._waiting_tasks), 1)

        transport.replies.put(b"C0QWid0007er00/00qw1")
        self.assertEqual(await asyncio.wait_for(sent, 2), "C0QWid0007er00/00qw1")
    finally:
      r.stop()

  async def test_a_spontaneous_line_is_discarded_before_its_id_is_read(self):
    transport = FakeTransport()
    r = router(transport, spontaneous=is_spontaneous)
    r.start()
    try:
      # `id0s` is not an id `read_id` can read, which would be logged if it were tried.
      with self.assertLogs(ROUTER_LOGGER, level=logging.DEBUG) as logs:
        sent = asyncio.ensure_future(r.send("C0QWid0008", id_=8))
        await asyncio.sleep(0.05)
        transport.replies.put(self.SPONTANEOUS)
        await asyncio.sleep(0.1)
        transport.replies.put(b"C0QWid0008er00/00qw1")
        await asyncio.wait_for(sent, 2)
      self.assertEqual([record.levelno for record in logs.records], [logging.DEBUG])
      self.assertIn("discarded spontaneous message", logs.output[0])
    finally:
      r.stop()

  async def test_without_a_filter_such_a_line_is_not_treated_specially(self):
    transport = FakeTransport()
    r = router(transport)
    r.start()
    try:
      with self.assertLogs(ROUTER_LOGGER, level=logging.WARNING) as logs:
        sent = asyncio.ensure_future(r.send("C0QWid0009", id_=9))
        await asyncio.sleep(0.05)
        transport.replies.put(self.SPONTANEOUS)
        await asyncio.sleep(0.1)
        transport.replies.put(b"C0QWid0009er00/00qw1")
        await asyncio.wait_for(sent, 2)
      self.assertIn("Could not parse response", logs.output[0])
    finally:
      r.stop()


class TestOwnerLoopReading(unittest.IsolatedAsyncioTestCase):
  """With `read_on_owner_loop` the replies are read on the loop that owns the transport, always."""

  SPONTANEOUS = b'C0__id0sm"Spontan Message"'

  async def asyncSetUp(self):
    self.transport = LoopBoundTransport()
    self.r = router(
      self.transport, spontaneous=is_spontaneous, read_on_owner_loop=True, read_timeout=5
    )
    self.r.start()

  async def asyncTearDown(self):
    self.r.stop()

  async def test_a_reply_reaches_the_command_with_its_id(self):
    first = asyncio.ensure_future(self.r.send("C0RTid0001", id_=1))
    second = asyncio.ensure_future(self.r.send("C0RWid0002", id_=2))
    await asyncio.sleep(0.05)
    self.transport.replies.put_nowait(b"C0RWid0002er00/00rw000")
    self.transport.replies.put_nowait(b"C0RTid0001er00/00rt0")
    self.assertEqual(await asyncio.wait_for(first, 2), "C0RTid0001er00/00rt0")
    self.assertEqual(await asyncio.wait_for(second, 2), "C0RWid0002er00/00rw000")
    self.assertEqual(self.r._waiting_tasks, [])

  async def test_an_error_reply_raises_on_its_command(self):
    sent = asyncio.ensure_future(self.r.send("C0ZAid0003", id_=3))
    await asyncio.sleep(0.05)
    self.transport.replies.put_nowait(b"C0ZAid0003er99/00")
    with self.assertRaises(RuntimeError):
      await asyncio.wait_for(sent, 2)

  async def test_a_spontaneous_line_between_a_command_and_its_reply_is_discarded(self):
    with self.assertNoLogs(ROUTER_LOGGER, level=logging.WARNING):
      sent = asyncio.ensure_future(self.r.send("C0QWid0007", id_=7))
      await asyncio.sleep(0.05)
      self.transport.replies.put_nowait(self.SPONTANEOUS)
      await asyncio.sleep(0.15)
      self.assertFalse(sent.done())
      self.transport.replies.put_nowait(b"C0QWid0007er00/00qw1")
      self.assertEqual(await asyncio.wait_for(sent, 2), "C0QWid0007er00/00qw1")

  async def test_a_line_arriving_with_no_command_waiting_is_read_and_discarded(self):
    with self.assertNoLogs(ROUTER_LOGGER, level=logging.WARNING):
      self.transport.replies.put_nowait(self.SPONTANEOUS)
      self.transport.replies.put_nowait(self.SPONTANEOUS)
      await asyncio.sleep(0.15)
    self.assertTrue(self.transport.replies.empty())

    sent = asyncio.ensure_future(self.r.send("C0QWid0008", id_=8))
    await asyncio.sleep(0.05)
    self.transport.replies.put_nowait(b"C0QWid0008er00/00qw1")
    self.assertEqual(await asyncio.wait_for(sent, 2), "C0QWid0008er00/00qw1")

  async def test_a_reply_nothing_waits_for_is_read_and_warned_about(self):
    with self.assertLogs(ROUTER_LOGGER, level=logging.WARNING) as logs:
      self.transport.replies.put_nowait(b"C0QWid0099er00/00qw1")
      await asyncio.sleep(0.15)
    self.assertIn("nothing was waiting for this reply", logs.output[0])
    self.assertTrue(self.transport.replies.empty())

  async def test_an_idle_link_logs_nothing_and_keeps_one_read_outstanding(self):
    with self.assertNoLogs(ROUTER_LOGGER, level=logging.WARNING):
      await asyncio.sleep(0.45)
    self.assertEqual(self.transport.reads_started, 1)

  async def test_an_unanswered_command_times_out_and_the_reading_goes_on(self):
    with self.assertRaises(TimeoutError):
      await asyncio.wait_for(self.r.send("C0QWid0004", id_=4, read_timeout=0), 2)
    self.assertEqual(self.r._waiting_tasks, [])

    sent = asyncio.ensure_future(self.r.send("C0QWid0005", id_=5))
    await asyncio.sleep(0.05)
    self.transport.replies.put_nowait(b"C0QWid0005er00/00qw1")
    self.assertEqual(await asyncio.wait_for(sent, 2), "C0QWid0005er00/00qw1")

  async def test_a_closed_link_fails_the_waiting_commands_and_ends_the_reading(self):
    first = asyncio.ensure_future(self.r.send("C0QWid0010", id_=10))
    second = asyncio.ensure_future(self.r.send("C0RTid0011", id_=11))
    await asyncio.sleep(0.05)
    self.transport.replies.put_nowait(ConnectionError("the instrument closed the connection"))
    for sent in (first, second):
      with self.assertRaises(ConnectionError):
        await asyncio.wait_for(sent, 2)
    self.assertEqual(self.r._waiting_tasks, [])

    await asyncio.sleep(0.3)
    self.assertEqual(self.transport.reads_started, 1)
    with self.assertRaises(ConnectionError):
      await self.r.send("C0QWid0012", id_=12)
    self.assertEqual(self.r._waiting_tasks, [])

  async def test_a_line_that_is_not_utf8_is_skipped(self):
    with self.assertLogs(ROUTER_LOGGER, level=logging.WARNING) as logs:
      self.transport.replies.put_nowait(b"\xff\xfe")
      await asyncio.sleep(0.15)
    self.assertIn("Could not decode", logs.output[0])

    sent = asyncio.ensure_future(self.r.send("C0QWid0013", id_=13))
    await asyncio.sleep(0.05)
    self.transport.replies.put_nowait(b"C0QWid0013er00/00qw1")
    self.assertEqual(await asyncio.wait_for(sent, 2), "C0QWid0013er00/00qw1")

  async def test_stop_returns_at_once_cancels_the_read_and_fails_waiting_commands(self):
    sent = asyncio.ensure_future(self.r.send("C0QWid0014", id_=14))
    await asyncio.sleep(0.05)

    started = time.monotonic()
    self.r.stop()
    self.assertLess(time.monotonic() - started, 0.5)

    with self.assertRaises(RuntimeError):
      await asyncio.wait_for(sent, 2)
    # A few turns of the loop, far less than a poll interval: the read is cancelled by `stop`
    # itself, not found out about at the next wake-up.
    for _ in range(20):
      await asyncio.sleep(0)
    self.assertTrue(self.transport.read_cancelled)
    self.assertEqual(self.r._waiting_tasks, [])

  async def test_the_router_can_be_started_again_after_it_was_stopped(self):
    self.r.stop()
    await asyncio.sleep(0.05)
    self.r.start()
    sent = asyncio.ensure_future(self.r.send("C0QWid0015", id_=15))
    await asyncio.sleep(0.05)
    self.transport.replies.put_nowait(b"C0QWid0015er00/00qw1")
    self.assertEqual(await asyncio.wait_for(sent, 2), "C0QWid0015er00/00qw1")

  async def test_a_command_is_refused_when_the_router_is_not_started(self):
    self.r.stop()
    await asyncio.sleep(0.05)
    with self.assertRaises(RuntimeError):
      await self.r.send("C0QWid0016", id_=16)
    self.assertEqual(self.r._waiting_tasks, [])


class TestOwnerLoopStart(unittest.TestCase):
  def test_start_needs_a_running_event_loop(self):
    r = router(LoopBoundTransport(), read_on_owner_loop=True)
    with self.assertRaises(RuntimeError):
      r.start()
    r.stop()


class TestNextId(unittest.TestCase):
  """Command ids run from 1 to 9999 and never take the id a machine's own messages carry."""

  def test_the_first_id_is_1(self):
    self.assertEqual(router(FakeTransport()).next_id(), 1)

  def test_ids_wrap_from_9999_to_1_and_skip_0(self):
    r = router(FakeTransport())
    r.id_ = 9998
    self.assertEqual([r.next_id() for _ in range(3)], [9999, 1, 2])

  def test_a_full_cycle_never_yields_0(self):
    r = router(FakeTransport())
    ids = [r.next_id() for _ in range(20000)]
    self.assertNotIn(0, ids)
    self.assertEqual(set(ids), set(range(1, 10000)))


if __name__ == "__main__":
  unittest.main()

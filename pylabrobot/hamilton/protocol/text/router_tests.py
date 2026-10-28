import asyncio
import logging
import queue
import unittest
from typing import Callable, List, Optional

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


def is_spontaneous(line: str) -> bool:
  return line[2:4] == "__"


def router(
  transport: FakeTransport,
  read_timeout: int = 5,
  spontaneous: Optional[Callable[[str], bool]] = None,
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

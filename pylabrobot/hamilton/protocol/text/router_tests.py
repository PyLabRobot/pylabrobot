import asyncio
import queue
import unittest
from typing import List, Optional

from pylabrobot.hamilton.protocol.text.framing import read_id
from pylabrobot.hamilton.protocol.text.router import ReplyRouter


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


def router(transport: FakeTransport, read_timeout: int = 5) -> ReplyRouter:
  def raise_for_error(reply: str) -> None:
    if "er99" in reply:
      raise RuntimeError(reply)

  return ReplyRouter(
    io=transport,  # type: ignore[arg-type]
    module_id_length=2,
    parse_id=read_id,
    raise_for_error=raise_for_error,
    read_timeout=read_timeout,
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


if __name__ == "__main__":
  unittest.main()

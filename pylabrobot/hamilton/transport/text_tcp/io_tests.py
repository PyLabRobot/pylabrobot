import asyncio
import logging
import re
import unittest
from typing import Optional
from unittest.mock import AsyncMock, Mock

from pylabrobot.hamilton.protocol.text.router import ReplyRouter
from pylabrobot.hamilton.transport.text_tcp.io import DEFAULT_PORT, HamiltonTextTCP

ROUTER_LOGGER = "pylabrobot.hamilton.protocol.text.router"

VERSION_REPLY = b'A1AMRFid1er0rf"D.0.06.00.0065 2025-04-25 (MAG Arm)"'
ERROR_REPLY = b'A1PMQAid3er30et"Command: Unknown command"'
SPONTANEOUS = b'A1AM__id0sm"Spontan Message"'


class TestFraming(unittest.IsolatedAsyncioTestCase):
  """Lines go out with a CRLF added and come back whole, without it, whatever the TCP segments."""

  async def asyncSetUp(self):
    self.io = HamiltonTextTCP("memory-only", 0)
    self.reader = asyncio.StreamReader()
    self.writer = Mock(spec=asyncio.StreamWriter)
    self.writer.drain = AsyncMock()
    self.io._socket._reader = self.reader
    self.io._socket._writer = self.writer

  async def test_the_default_port_is_24652(self):
    self.assertEqual(DEFAULT_PORT, 24652)
    self.assertEqual(HamiltonTextTCP("memory-only").serialize()["port"], 24652)

  async def test_write_adds_crlf(self):
    await self.io.write(b"A1AMRFid0001")
    self.writer.write.assert_called_once_with(b"A1AMRFid0001\r\n")

  async def test_read_returns_the_line_without_its_terminator(self):
    self.reader.feed_data(VERSION_REPLY + b"\r\n")
    self.assertEqual(await self.io.read(), VERSION_REPLY)

  async def test_a_reply_with_quotes_and_an_error_text_is_returned_intact(self):
    self.reader.feed_data(ERROR_REPLY + b"\r\n")
    self.assertEqual(await self.io.read(), ERROR_REPLY)

  async def test_a_reply_split_across_two_segments_is_returned_once_complete(self):
    self.reader.feed_data(b"A1AMQWid2er0")
    pending = asyncio.ensure_future(self.io.read())
    await asyncio.sleep(0.02)
    self.assertFalse(pending.done())
    self.reader.feed_data(b"qw0\r")
    await asyncio.sleep(0.02)
    self.assertFalse(pending.done())
    self.reader.feed_data(b"\n")
    self.assertEqual(await asyncio.wait_for(pending, 2), b"A1AMQWid2er0qw0")

  async def test_two_replies_in_one_segment_are_returned_one_by_one(self):
    self.reader.feed_data(b"A1AMQWid2er0qw0\r\n" + SPONTANEOUS + b"\r\n")
    self.assertEqual(await self.io.read(), b"A1AMQWid2er0qw0")
    self.assertEqual(await self.io.read(), SPONTANEOUS)

  async def test_a_connection_closed_between_lines_raises_connection_error(self):
    self.reader.feed_eof()
    with self.assertRaises(ConnectionError):
      await self.io.read()

  async def test_a_connection_closed_in_the_middle_of_a_line_raises_connection_error(self):
    self.reader.feed_data(b"A1AMQWid2er")
    self.reader.feed_eof()
    with self.assertRaises(ConnectionError):
      await self.io.read()

  async def test_read_times_out_when_no_line_arrives(self):
    with self.assertRaises(TimeoutError):
      await self.io.read(timeout=0.05)

  async def test_serialize_holds_the_arguments(self):
    io = HamiltonTextTCP("192.0.2.123", 1234, write_timeout=5, read_timeout=7)
    self.assertEqual(
      io.serialize(),
      {
        "type": "HamiltonTextTCP",
        "host": "192.0.2.123",
        "port": 1234,
        "write_timeout": 5,
        "read_timeout": 7,
      },
    )


class LineServer:
  """A local server standing in for a machine: it records the lines it receives, and sends what a
  test tells it to."""

  def __init__(self):
    self.lines: "asyncio.Queue[bytes]" = asyncio.Queue()
    self.port = 0
    self._server: Optional[asyncio.AbstractServer] = None
    self._writer: Optional[asyncio.StreamWriter] = None
    self._connected = asyncio.Event()

  async def start(self) -> None:
    self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
    self.port = self._server.sockets[0].getsockname()[1]

  async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    self._writer = writer
    self._connected.set()
    try:
      while True:
        self.lines.put_nowait((await reader.readuntil(b"\r\n"))[:-2])
    except (asyncio.IncompleteReadError, ConnectionError):
      pass
    finally:
      writer.close()

  async def connected(self) -> None:
    await asyncio.wait_for(self._connected.wait(), 2)

  async def send(self, data: bytes) -> None:
    assert self._writer is not None
    self._writer.write(data)
    await self._writer.drain()

  async def hang_up(self) -> None:
    assert self._writer is not None
    self._writer.close()
    await self._writer.wait_closed()

  async def stop(self) -> None:
    assert self._server is not None
    self._server.close()
    await self._server.wait_closed()


class TestOverLoopback(unittest.IsolatedAsyncioTestCase):
  """The io against a real socket."""

  async def asyncSetUp(self):
    self.server = LineServer()
    await self.server.start()
    self.io = HamiltonTextTCP("127.0.0.1", self.server.port)
    await self.io.setup()
    await self.server.connected()

  async def asyncTearDown(self):
    await self.io.stop()
    await self.server.stop()

  async def test_a_written_line_arrives_with_a_crlf(self):
    await self.io.write(b"A1AMRFid0001")
    self.assertEqual(await asyncio.wait_for(self.server.lines.get(), 2), b"A1AMRFid0001")

  async def test_a_line_from_the_server_is_read(self):
    await self.server.send(VERSION_REPLY + b"\r\n")
    self.assertEqual(await asyncio.wait_for(self.io.read(), 2), VERSION_REPLY)

  async def test_a_reply_sent_in_two_pieces_is_read_whole(self):
    await self.server.send(b"A1AMQWid2er0")
    pending = asyncio.ensure_future(self.io.read())
    await asyncio.sleep(0.05)
    self.assertFalse(pending.done())
    await self.server.send(b"qw0\r\n")
    self.assertEqual(await asyncio.wait_for(pending, 2), b"A1AMQWid2er0qw0")

  async def test_a_server_that_hangs_up_raises_connection_error(self):
    await self.server.hang_up()
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(self.io.read(), 2)


def read_id(reply: str) -> int:
  """The id of a reply, however many digits the machine echoes it with."""
  found = re.search(r"id(\d+)", reply)
  if found is None:
    raise ValueError(f"no id in {reply!r}")
  return int(found.group(1))


class TestWithTheReplyRouter(unittest.IsolatedAsyncioTestCase):
  """The io read by the router on the loop that owns the socket, as the Vantage driver does."""

  async def asyncSetUp(self):
    self.server = LineServer()
    await self.server.start()
    self.io = HamiltonTextTCP("127.0.0.1", self.server.port)
    await self.io.setup()
    await self.server.connected()
    self.router = ReplyRouter(
      io=self.io,
      module_id_length=4,
      parse_id=read_id,
      raise_for_error=lambda reply: None,
      read_timeout=5,
      is_spontaneous=lambda line: re.match(r"^[A-Z0-9_.]+__[a-z]", line) is not None,
      read_on_owner_loop=True,
    )
    self.router.start()

  async def asyncTearDown(self):
    self.router.stop()
    await self.io.stop()
    await self.server.stop()

  async def test_a_command_is_answered_when_the_echoed_id_has_no_padding(self):
    sent = asyncio.ensure_future(self.router.send("A1AMRFid0001", id_=1))
    self.assertEqual(await asyncio.wait_for(self.server.lines.get(), 2), b"A1AMRFid0001")
    await self.server.send(VERSION_REPLY + b"\r\n")
    self.assertEqual(await asyncio.wait_for(sent, 2), VERSION_REPLY.decode())

  async def test_a_spontaneous_line_between_a_command_and_its_reply_is_discarded(self):
    with self.assertNoLogs(ROUTER_LOGGER, level=logging.WARNING):
      sent = asyncio.ensure_future(self.router.send("A1AMRFid0001", id_=1))
      await asyncio.wait_for(self.server.lines.get(), 2)
      await self.server.send(SPONTANEOUS + b"\r\n")
      await asyncio.sleep(0.15)
      self.assertFalse(sent.done())
      await self.server.send(VERSION_REPLY + b"\r\n")
      self.assertEqual(await asyncio.wait_for(sent, 2), VERSION_REPLY.decode())

  async def test_a_spontaneous_line_on_an_idle_link_is_discarded_and_the_next_command_works(self):
    with self.assertNoLogs(ROUTER_LOGGER, level=logging.WARNING):
      await self.server.send(SPONTANEOUS + b"\r\n")
      await asyncio.sleep(0.15)

      sent = asyncio.ensure_future(self.router.send("A1AMRFid0001", id_=1))
      await asyncio.wait_for(self.server.lines.get(), 2)
      await self.server.send(VERSION_REPLY + b"\r\n")
      self.assertEqual(await asyncio.wait_for(sent, 2), VERSION_REPLY.decode())

  async def test_a_server_that_hangs_up_fails_the_waiting_command(self):
    sent = asyncio.ensure_future(self.router.send("A1AMRFid0001", id_=1))
    await asyncio.wait_for(self.server.lines.get(), 2)
    await self.server.hang_up()
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(sent, 2)
    with self.assertRaises(ConnectionError):
      await self.router.send("A1AMRFid0002", id_=2)


if __name__ == "__main__":
  unittest.main()

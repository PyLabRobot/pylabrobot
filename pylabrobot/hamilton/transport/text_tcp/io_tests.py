import logging
import re
import sys
from typing import Any, List, Optional
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.hamilton.protocol.text.router import ReplyReader, ReplyRouter
from pylabrobot.hamilton.transport.text_tcp.io import DEFAULT_PORT, HamiltonTextTCP
from pylabrobot.io.io import ReconnectableConnectionIO
from pylabrobot.testing.concurrency import AnyioTestBase

if sys.version_info < (3, 11):
  from exceptiongroup import BaseExceptionGroup

ROUTER_LOGGER = "pylabrobot.hamilton.protocol.text.router"

VERSION_REPLY = b'A1AMRFid1er0rf"D.0.06.00.0065 2025-04-25 (MAG Arm)"'
ERROR_REPLY = b'A1PMQAid3er30et"Command: Unknown command"'
SPONTANEOUS = b'A1AM__id0sm"Spontan Message"'


class _MemoryByteStream(anyio.abc.ByteStream):
  """Loop-neutral in-memory ByteStream for framing tests."""

  def __init__(self) -> None:
    self._send, self._receive = anyio.create_memory_object_stream[Optional[bytes]](float("inf"))
    self.written: List[bytes] = []
    self.closed = False

  def feed_data(self, chunk: bytes) -> None:
    self._send.send_nowait(chunk)

  def feed_eof(self) -> None:
    self._send.send_nowait(None)

  async def receive(self, max_bytes: int = 65536) -> bytes:
    del max_bytes
    item = await self._receive.receive()
    if item is None:
      raise anyio.EndOfStream
    return item

  async def send(self, item: bytes) -> None:
    if self.closed:
      raise anyio.ClosedResourceError
    self.written.append(bytes(item))

  async def send_eof(self) -> None:
    pass

  async def aclose(self) -> None:
    self.closed = True
    await self._send.aclose()
    await self._receive.aclose()


class TestFraming(AnyioTestBase):
  """Lines go out with a CRLF added and come back whole, without it, whatever the TCP segments."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.stream = _MemoryByteStream()

    async def fake_connect_tcp(*args: Any, **kw: Any) -> _MemoryByteStream:
      del args, kw
      return self.stream

    stack.enter_context(
      patch("pylabrobot.io.socket.anyio.connect_tcp", side_effect=fake_connect_tcp)
    )
    self.io = HamiltonTextTCP("memory-only", 24652)
    await stack.enter_async_context(self.io.lifespan())

  async def test_contracts_and_default_port(self) -> None:
    assert DEFAULT_PORT == 24652
    inactive = HamiltonTextTCP("memory-only")
    assert isinstance(inactive, ReconnectableConnectionIO)
    assert isinstance(inactive, ReplyReader)
    assert inactive.serialize()["port"] == 24652
    with pytest.raises(RuntimeError, match="lifespan"):
      await inactive.read()
    with pytest.raises(RuntimeError, match="lifespan"):
      await inactive.write(b"A1AMRFid0001")
    with pytest.raises(RuntimeError, match="lifespan"):
      await inactive.reconnect()

  async def test_write_adds_crlf(self) -> None:
    await self.io.write(b"A1AMRFid0001")
    assert self.stream.written == [b"A1AMRFid0001\r\n"]

  async def test_read_returns_the_line_without_its_terminator(self) -> None:
    self.stream.feed_data(VERSION_REPLY + b"\r\n")
    assert await self.io.read() == VERSION_REPLY

  async def test_a_reply_with_quotes_and_an_error_text_is_returned_intact(self) -> None:
    self.stream.feed_data(ERROR_REPLY + b"\r\n")
    assert await self.io.read() == ERROR_REPLY

  async def test_a_reply_split_across_two_segments_is_returned_once_complete(self) -> None:
    self.stream.feed_data(b"A1AMQWid2er0")
    results: List[bytes] = []

    async def reader() -> None:
      results.append(await self.io.read())

    async with anyio.create_task_group() as tg:
      tg.start_soon(reader)
      await anyio.sleep(0.02)
      assert results == []
      self.stream.feed_data(b"qw0\r")
      await anyio.sleep(0.02)
      assert results == []
      self.stream.feed_data(b"\n")
    assert results == [b"A1AMQWid2er0qw0"]

  async def test_two_replies_in_one_segment_are_returned_one_by_one(self) -> None:
    self.stream.feed_data(b"A1AMQWid2er0qw0\r\n" + SPONTANEOUS + b"\r\n")
    assert await self.io.read() == b"A1AMQWid2er0qw0"
    assert await self.io.read() == SPONTANEOUS

  async def test_a_connection_closed_between_lines_raises_connection_error(self) -> None:
    self.stream.feed_eof()
    with pytest.raises(ConnectionError):
      await self.io.read()

  async def test_a_connection_closed_in_the_middle_of_a_line_raises_connection_error(self) -> None:
    self.stream.feed_data(b"A1AMQWid2er")
    self.stream.feed_eof()
    with pytest.raises(ConnectionError):
      await self.io.read()

  async def test_read_deadline_retains_partial_bytes_for_next_read(self) -> None:
    self.stream.feed_data(b"A1AMQWid2er0")
    with pytest.raises(TimeoutError):
      with anyio.fail_after(0.05):
        await self.io.read()
    self.stream.feed_data(b"qw0\r\n")
    assert await self.io.read() == b"A1AMQWid2er0qw0"

  async def test_serialize_holds_the_arguments(self) -> None:
    io = HamiltonTextTCP("192.0.2.123", 1234, write_timeout=5)
    assert io.serialize() == {
      "type": "HamiltonTextTCP",
      "host": "192.0.2.123",
      "port": 1234,
      "write_timeout": 5,
    }


class LineServer(HasLifespan):
  """A local server standing in for a machine: it records the lines it receives, and sends what a
  test tells it to."""

  def __init__(self) -> None:
    super().__init__()
    self.port = 0
    self._lines_send, self._lines_receive = anyio.create_memory_object_stream[bytes](float("inf"))
    self._stream: Optional[anyio.abc.SocketStream] = None
    self._connected = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
    await stack.enter_async_context(listener)
    self.port = int(listener.extra(anyio.abc.SocketAttribute.local_address)[1])
    tg = await stack.enter_task_group()
    stack.callback(tg.cancel_scope.cancel)
    tg.start_soon(listener.serve, self._serve)
    stack.push_shielded_async_callback(self._close_client)

  async def _close_client(self) -> None:
    if self._stream is not None:
      await self._stream.aclose()
      self._stream = None

  async def _serve(self, stream: anyio.abc.SocketStream) -> None:
    self._stream = stream
    self._connected.set()
    buf = bytearray()
    try:
      while True:
        chunk = await stream.receive(4096)
        buf.extend(chunk)
        while b"\r\n" in buf:
          idx = buf.index(b"\r\n")
          line = bytes(buf[:idx])
          del buf[: idx + 2]
          await self._lines_send.send(line)
    except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError, OSError):
      pass
    finally:
      await stream.aclose()

  async def connected(self) -> None:
    with anyio.fail_after(2):
      await self._connected.wait()

  async def next_line(self) -> bytes:
    with anyio.fail_after(2):
      return await self._lines_receive.receive()

  async def send(self, data: bytes) -> None:
    assert self._stream is not None
    await self._stream.send(data)

  async def hang_up(self) -> None:
    assert self._stream is not None
    await self._stream.aclose()


class TestOverLoopback(AnyioTestBase):
  """The io against a real socket."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.server = LineServer()
    await stack.enter_async_context(self.server.lifespan())
    self.io = HamiltonTextTCP("127.0.0.1", self.server.port)
    await stack.enter_async_context(self.io.lifespan())
    await self.server.connected()

  async def test_a_written_line_arrives_with_a_crlf(self) -> None:
    await self.io.write(b"A1AMRFid0001")
    assert await self.server.next_line() == b"A1AMRFid0001"

  async def test_a_line_from_the_server_is_read(self) -> None:
    await self.server.send(VERSION_REPLY + b"\r\n")
    with anyio.fail_after(2):
      assert await self.io.read() == VERSION_REPLY

  async def test_a_reply_sent_in_two_pieces_is_read_whole(self) -> None:
    await self.server.send(b"A1AMQWid2er0")
    results: List[bytes] = []

    async def reader() -> None:
      with anyio.fail_after(2):
        results.append(await self.io.read())

    async with anyio.create_task_group() as tg:
      tg.start_soon(reader)
      await anyio.sleep(0.05)
      assert results == []
      await self.server.send(b"qw0\r\n")
    assert results == [b"A1AMQWid2er0qw0"]

  async def test_a_server_that_hangs_up_raises_connection_error(self) -> None:
    await self.server.hang_up()
    with pytest.raises(ConnectionError):
      with anyio.fail_after(2):
        await self.io.read()


def read_id(reply: str) -> int:
  """The id of a reply, however many digits the machine echoes it with."""
  found = re.search(r"id(\d+)", reply)
  if found is None:
    raise ValueError(f"no id in {reply!r}")
  return int(found.group(1))


class TestWithTheReplyRouter(AnyioTestBase):
  """The io read by the router inside its lifespan, as TCP drivers do."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.server = LineServer()
    await stack.enter_async_context(self.server.lifespan())
    self.io = HamiltonTextTCP("127.0.0.1", self.server.port)
    await stack.enter_async_context(self.io.lifespan())
    await self.server.connected()
    self.router = ReplyRouter(
      io=self.io,
      module_id_length=4,
      parse_id=read_id,
      raise_for_error=lambda reply: None,
      read_timeout=5,
      is_spontaneous=lambda line: re.match(r"^[A-Z0-9_.]+__[a-z]", line) is not None,
    )

  async def test_a_command_is_answered_when_the_echoed_id_has_no_padding(self) -> None:
    results: List[Optional[str]] = []

    async def send() -> None:
      results.append(await self.router.send("A1AMRFid0001", id_=1))

    async with self.router.lifespan():
      async with anyio.create_task_group() as tg:
        tg.start_soon(send)
        assert await self.server.next_line() == b"A1AMRFid0001"
        await self.server.send(VERSION_REPLY + b"\r\n")
    assert results == [VERSION_REPLY.decode()]

  async def test_a_spontaneous_line_between_a_command_and_its_reply_is_discarded(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    results: List[Optional[str]] = []

    async def send() -> None:
      results.append(await self.router.send("A1AMRFid0001", id_=1))

    with caplog.at_level(logging.WARNING, logger=ROUTER_LOGGER):
      async with self.router.lifespan():
        async with anyio.create_task_group() as tg:
          tg.start_soon(send)
          await self.server.next_line()
          await self.server.send(SPONTANEOUS + b"\r\n")
          await anyio.sleep(0.05)
          assert results == []
          await self.server.send(VERSION_REPLY + b"\r\n")
    assert results == [VERSION_REPLY.decode()]
    assert caplog.records == []

  async def test_a_spontaneous_line_on_an_idle_link_is_discarded_and_the_next_command_works(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    results: List[Optional[str]] = []

    async def send() -> None:
      results.append(await self.router.send("A1AMRFid0001", id_=1))

    with caplog.at_level(logging.WARNING, logger=ROUTER_LOGGER):
      async with self.router.lifespan():
        await self.server.send(SPONTANEOUS + b"\r\n")
        await anyio.sleep(0.05)
        async with anyio.create_task_group() as tg:
          tg.start_soon(send)
          await self.server.next_line()
          await self.server.send(VERSION_REPLY + b"\r\n")
    assert results == [VERSION_REPLY.decode()]
    assert caplog.records == []

  async def test_a_server_that_hangs_up_fails_the_waiting_command(self) -> None:
    waiter_errors: List[BaseException] = []

    async def send() -> None:
      try:
        await self.router.send("A1AMRFid0001", id_=1)
      except BaseException as err:
        waiter_errors.append(err)

    with pytest.raises(BaseExceptionGroup) as exc_info:
      async with self.router.lifespan():
        async with anyio.create_task_group() as tg:
          tg.start_soon(send)
          await self.server.next_line()
          await self.server.hang_up()

    assert len(waiter_errors) == 1
    assert isinstance(waiter_errors[0], ConnectionError)
    assert any(isinstance(e, ConnectionError) for e in exc_info.value.exceptions)
    with pytest.raises(ConnectionError):
      await self.io.write(b"A1AMRFid0002")
    with pytest.raises(RuntimeError, match="No active reply router"):
      await self.router.send("A1AMRFid0002", id_=2)

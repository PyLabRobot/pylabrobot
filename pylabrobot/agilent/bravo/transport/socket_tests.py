import asyncio
import time
import unittest
from typing import Optional
from unittest.mock import patch

from pylabrobot.agilent.bravo.transport._bridge import AsyncTransportBase
from pylabrobot.agilent.bravo.transport.base_tests import TransportContractTests
from pylabrobot.agilent.bravo.transport.socket import _RECEIVE_BUFFER_SIZE, SocketTransport


class _LoopbackServer:
  """A local TCP server on an ephemeral port, for exercising the transport.

  Subclasses supply only :meth:`handle`.
  """

  def __init__(self):
    self._server: Optional[asyncio.AbstractServer] = None
    self.port = 0

  async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    raise NotImplementedError

  async def start(self) -> None:
    self._server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
    self.port = self._server.sockets[0].getsockname()[1]

  async def stop(self) -> None:
    assert self._server is not None
    self._server.close()
    await self._server.wait_closed()


class EchoServer(_LoopbackServer):
  """Echoes every write straight back."""

  async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
      data = await reader.read(1024)
      if not data:
        break
      writer.write(data)
      await writer.drain()
    writer.close()


class DribbleServer(_LoopbackServer):
  """Echoes each write back in delayed, undersized chunks.

  Unlike ``EchoServer``, a single write from the client is guaranteed to arrive
  as several separate reads: each chunk is followed by a delay before the next
  one is written, so the client's first read returns before later chunks
  exist. This exercises the multi-read assembly path in ``receive_exact``.
  """

  def __init__(self, chunk_size: int = 2, delay: float = 0.05):
    super().__init__()
    self._chunk_size = chunk_size
    self._delay = delay

  async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
      data = await reader.read(1024)
      if not data:
        break
      for i in range(0, len(data), self._chunk_size):
        writer.write(data[i : i + self._chunk_size])
        await writer.drain()
        await asyncio.sleep(self._delay)
    writer.close()


class SocketTransportTests(TransportContractTests, unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.server = await self._started(EchoServer())
    self.transport = await self._connected("test bravo", self.server.port)

  async def _started(self, server):
    await server.start()
    self.addAsyncCleanup(server.stop)
    return server

  async def _connected(self, name: str, port: int) -> SocketTransport:
    transport = SocketTransport(
      human_readable_device_name=name,
      host="127.0.0.1",
      port=port,
    )
    await transport.setup()
    self.addAsyncCleanup(transport.stop)
    return transport

  async def connected_transport(self) -> AsyncTransportBase:
    return self.transport

  async def chunked_transport(self) -> AsyncTransportBase:
    dribbler = await self._started(DribbleServer(chunk_size=2, delay=0.05))
    return await self._connected("dribble bravo", dribbler.port)

  def unconnected_transport(self) -> AsyncTransportBase:
    return SocketTransport(
      human_readable_device_name="unconnected",
      host="127.0.0.1",
      port=self.server.port,
    )

  async def test_receive_caps_at_buffer_size(self):
    # A response larger than _RECEIVE_BUFFER_SIZE is truncated to that many
    # bytes on a single receive() call; this pins that behavior so it can't
    # regress into a silent, undocumented change. Rather than sleeping a fixed
    # interval and hoping the whole payload has landed, poll the reader's
    # buffered byte count -- without consuming it -- until it has, so this
    # can't race the loopback echo under load.
    payload = b"x" * (_RECEIVE_BUFFER_SIZE + 200)
    await asyncio.to_thread(self.transport.send, payload)

    # _buffer is not part of StreamReader's typed public surface; this is
    # test-only introspection to poll for arrival without consuming.
    reader = self.transport._io._reader
    assert reader is not None
    deadline = time.monotonic() + 2.0
    while len(reader._buffer) < len(payload):  # type: ignore[attr-defined]
      if time.monotonic() > deadline:
        buffered = len(reader._buffer)  # type: ignore[attr-defined]
        self.fail(f"only {buffered} of {len(payload)} bytes had arrived")
      await asyncio.sleep(0.005)

    result = await asyncio.to_thread(self.transport.receive)
    self.assertEqual(len(result), _RECEIVE_BUFFER_SIZE)
    self.assertEqual(result, payload[:_RECEIVE_BUFFER_SIZE])

  async def test_receive_exact_spans_several_underlying_reads(self):
    # The contract suite pins that the eight bytes come back assembled. This
    # pins that assembling them really did take more than one read of the
    # socket, which is the part Socket.read_exact is being trusted with.
    transport = await self.chunked_transport()

    def blocking_roundtrip() -> bytes:
      transport.send(b"12345678")
      return transport.receive_exact(8)

    start = time.monotonic()
    result = await asyncio.to_thread(blocking_roundtrip)
    elapsed = time.monotonic() - start

    self.assertEqual(result, b"12345678")
    # 8 bytes arrive as four 2-byte chunks, 50ms apart: a single underlying
    # read could not have produced this result in under ~150ms.
    self.assertGreater(elapsed, 0.15)

  async def test_send_reports_a_write_timeout_as_timeout_error(self):
    # Transport.send promises TimeoutError. Socket.write already raises one when
    # a drain times out, so what needs pinning is that send hands it on rather
    # than swallowing it or letting the outer future-level bound stand in for it.
    # Provoking a real drain timeout would mean pushing megabytes at a peer that
    # never reads, whose teardown then blocks on that unread data; the exception
    # is injected instead, and still travels the whole way out through _run.
    injected = TimeoutError("Timeout while writing to socket after 0.2 seconds")

    async def failing_write(data, timeout=None):
      raise injected

    def blocking_call() -> BaseException:
      with patch.object(self.transport._io, "write", failing_write):
        try:
          self.transport.send(b"ping")
        except BaseException as exc:  # noqa: BLE001 - the exception is the assertion
          return exc
      raise AssertionError("send did not raise")

    raised = await asyncio.to_thread(blocking_call)

    # Asserted on the message rather than on identity, for the reason
    # AsyncTransportBase._run documents: from Python 3.11 on, an exception of
    # exactly class TimeoutError does not survive the crossing intact.
    self.assertIsInstance(raised, TimeoutError)
    self.assertIn("Timeout while writing to socket", str(raised))
    self.assertNotIn("did not complete within", str(raised))


if __name__ == "__main__":
  unittest.main()

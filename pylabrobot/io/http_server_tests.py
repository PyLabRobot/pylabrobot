"""Exercise callback framing and lifecycle against a loopback HTTP listener."""

import base64
import json
import tempfile
from pathlib import Path
from typing import Any

import anyio
import pytest
from anyio.streams.buffered import BufferedByteReceiveStream

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.capture import start_capture, stop_capture
from pylabrobot.io.http import HTTP
from pylabrobot.io.http_server import HTTPServer
from pylabrobot.testing.concurrency import AnyioTestBase


class TestHTTPServer(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.received: list[bytes] = []

    async def handle(body: bytes) -> bytes:
      self.received.append(body)
      return b"<ack/>"

    self.server = HTTPServer(handle, "127.0.0.1", max_body_size=1024)
    await stack.enter_async_context(self.server.lifespan())
    self.client = HTTP("callback test", self.server.url)
    await stack.enter_async_context(self.client.lifespan())

  def test_inherits_managed_lifespan_methods(self) -> None:
    assert isinstance(self.server, HasLifespan)
    assert HTTPServer.setup is HasLifespan.setup
    assert HTTPServer.stop is HasLifespan.stop
    assert HTTPServer.__aenter__ is HasLifespan.__aenter__
    assert HTTPServer.__aexit__ is HasLifespan.__aexit__

  async def test_raw_callback_roundtrip_and_overlapping_entry_rejection(self) -> None:
    port = self.server.bound_port
    with pytest.raises(RuntimeError, match="already has an active lifespan"):
      async with self.server:
        pass
    assert self.server.bound_port == port
    response = await self.client.request_raw("POST", "/", b"<event/>")
    assert (response.status, response.body) == (200, b"<ack/>")
    assert self.received == [b"<event/>"]

  async def test_body_limit_prevents_dispatch(self) -> None:
    response = await self.client.request_raw("POST", "/", b"x" * 1025)
    assert response.status == 413
    assert self.received == []

  async def test_expect_continue_and_partial_body(self) -> None:
    stream = await anyio.connect_tcp("127.0.0.1", self.server.bound_port)
    async with stream:
      buffered = BufferedByteReceiveStream(stream)
      await stream.send(b"POST / HTTP/1.1\r\nContent-Length: 8\r\nExpect: 100-continue\r\n\r\n")
      assert await buffered.receive_until(b"\r\n\r\n", 1024) == b"HTTP/1.1 100 Continue"
      await stream.send(b"<eve")
      await anyio.sleep(0)
      await stream.send(b"nt/>")
      chunks = bytearray()
      while True:
        try:
          chunks.extend(await buffered.receive(4096))
        except anyio.EndOfStream:
          break
      assert b"<ack/>" in chunks

  async def test_lifespan_exit_cancels_incomplete_request_and_allows_reentry(self) -> None:
    async def handle(body: bytes) -> bytes:
      return b"<ack/>"

    server = HTTPServer(handle, "127.0.0.1", max_body_size=1024)
    async with server:
      stream = await anyio.connect_tcp("127.0.0.1", server.bound_port)
      async with stream:
        await stream.send(b"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\nx")
        with anyio.fail_after(2):
          while server._active_clients == 0:
            await anyio.sleep(0)
      # Exiting the lifespan with an incomplete request closes the connection cleanly.
    assert server._active_clients == 0
    with pytest.raises(RuntimeError, match="not active"):
      _ = server.bound_port

    async with server:
      assert server.bound_port > 0

      stream = await anyio.connect_tcp("127.0.0.1", server.bound_port)
      try:
        await stream.send(b"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\nx")
        with anyio.fail_after(2):
          while server._active_clients == 0:
            await anyio.sleep(0)
      finally:
        pass
    with anyio.fail_after(1):
      with pytest.raises(anyio.EndOfStream):
        await stream.receive(1024)
    await stream.aclose()
    assert server._active_clients == 0

  async def test_capture_records_callback_request_and_response(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "callback.json"
      start_capture(path)
      try:
        await self.client.request_raw("POST", "/", b"<event/>")
      finally:
        stop_capture()
      commands = json.loads(path.read_text())["commands"]
      callback = next(command for command in commands if command["module"] == "http_server")
      assert base64.b64decode(callback["request_base64"]) == b"<event/>"
      assert base64.b64decode(callback["response_base64"]) == b"<ack/>"

  async def test_outer_cancellation_keeps_listener_alive_for_shielded_exit_callback(self) -> None:
    received: list[bytes] = []

    async def handle(body: bytes) -> bytes:
      received.append(body)
      return b"<ack/>"

    server = HTTPServer(handle, "127.0.0.1", max_body_size=1024)

    async def send_callback_during_teardown(url: str) -> None:
      client = HTTP("teardown callback", url)
      async with client:
        response = await client.request_raw("POST", "/", b"<stop-ack/>")
        assert response.status == 200

    with anyio.fail_after(5):
      with anyio.CancelScope() as outer:
        async with AsyncExitStackWithShielding() as stack:
          await stack.enter_async_context(server.lifespan())
          stack.push_shielded_async_callback(
            send_callback_during_teardown,
            server.url,
            shield_timeout=2.0,
          )
          outer.cancel()
          await anyio.sleep_forever()
      assert outer.cancelled_caught
    assert received == [b"<stop-ack/>"]

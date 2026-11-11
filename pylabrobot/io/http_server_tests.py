"""Exercise callback framing and lifecycle against a loopback HTTP listener."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from pylabrobot.io.http import HTTP
from pylabrobot.io.http_server import HTTPServer


class HTTPServerTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.handler = AsyncMock(return_value=b"<ack/>")
    self.server = HTTPServer(self.handler, "127.0.0.1", max_body_size=1024)
    await self.server.setup()
    self.client = HTTP("callback test", self.server.url)
    await self.client.setup()

  async def asyncTearDown(self):
    await self.client.stop()
    await self.server.stop()

  async def test_raw_callback_roundtrip_and_idempotent_setup(self):
    port = self.server.bound_port
    await self.server.setup()
    self.assertEqual(self.server.bound_port, port)
    response = await self.client.request_raw("POST", "/", b"<event/>")
    self.assertEqual((response.status, response.body), (200, b"<ack/>"))
    self.handler.assert_awaited_once_with(b"<event/>")

  async def test_body_limit_prevents_dispatch(self):
    response = await self.client.request_raw("POST", "/", b"x" * 1025)
    self.assertEqual(response.status, 413)
    self.handler.assert_not_awaited()

  async def test_expect_continue_and_partial_body(self):
    reader, writer = await asyncio.open_connection("127.0.0.1", self.server.bound_port)
    writer.write(b"POST / HTTP/1.1\r\nContent-Length: 8\r\nExpect: 100-continue\r\n\r\n")
    await writer.drain()
    self.assertEqual(await reader.readuntil(b"\r\n\r\n"), b"HTTP/1.1 100 Continue\r\n\r\n")
    writer.write(b"<eve")
    await writer.drain()
    await asyncio.sleep(0)
    writer.write(b"nt/>")
    await writer.drain()
    self.assertIn(b"<ack/>", await reader.read())
    writer.close()
    await writer.wait_closed()

  async def test_stop_cancels_incomplete_request_and_allows_restart(self):
    reader, writer = await asyncio.open_connection("127.0.0.1", self.server.bound_port)
    writer.write(b"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\nx")
    await writer.drain()
    await asyncio.sleep(0)
    await self.server.stop()
    self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
    self.assertFalse(self.server._clients)
    writer.close()
    await writer.wait_closed()
    await self.server.setup()
    self.assertGreater(self.server.bound_port, 0)

  async def test_capture_records_callback_request_and_response(self):
    import base64
    import json
    import tempfile
    from pathlib import Path

    from pylabrobot.io.capture import start_capture, stop_capture

    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "callback.json"
      start_capture(path)
      try:
        await self.client.request_raw("POST", "/", b"<event/>")
      finally:
        stop_capture()
      commands = json.loads(path.read_text())["commands"]
      callback = next(command for command in commands if command["module"] == "http_server")
      self.assertEqual(base64.b64decode(callback["request_base64"]), b"<event/>")
      self.assertEqual(base64.b64decode(callback["response_base64"]), b"<ack/>")

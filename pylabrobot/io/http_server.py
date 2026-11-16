"""Receive HTTP callbacks from instruments without blocking the asyncio loop."""

import asyncio
import base64
import logging
import socket
from collections.abc import Awaitable, Callable

from pylabrobot.io.capture import capturer
from pylabrobot.io.http import HTTPRawCommand

logger = logging.getLogger(__name__)


async def local_address_for(host: str, port: int) -> str:
  """Find the IPv4 interface used to reach a device, without sending a packet."""

  def resolve() -> str:
    """Ask the routing table for the outgoing interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
      connection.connect((host, port))
      return str(connection.getsockname()[0])

  return await asyncio.to_thread(resolve)


class HTTPServer:
  """A POST callback endpoint whose handler receives and returns raw bytes.

  The server accepts Content-Length framed requests and closes each connection after its
  response. Requests, including Expect: 100-continue, are handled on the owning event loop.
  Incoming exchanges are recorded alongside outgoing HTTP traffic during capture.
  """

  def __init__(
    self,
    handler: Callable[[bytes], Awaitable[bytes]],
    host: str,
    port: int = 0,
    *,
    max_body_size: int = 8 * 1024 * 1024,
    timeout: float = 30,
  ) -> None:
    if max_body_size <= 0 or timeout <= 0:
      raise ValueError("max_body_size and timeout must be positive")
    self.handler = handler
    self.host = host
    self.port = port
    self.max_body_size = max_body_size
    self.timeout = timeout
    self._server: asyncio.Server | None = None
    self._clients: set[asyncio.Task] = set()

  @property
  def bound_port(self) -> int:
    """Return the listening port after setup."""
    if self._server is None:
      raise RuntimeError("HTTP callback server is not set up")
    return int(self._server.sockets[0].getsockname()[1])

  @property
  def url(self) -> str:
    """Return the callback URL advertised to the device."""
    return f"http://{self.host}:{self.bound_port}/"

  async def _respond(self, writer: asyncio.StreamWriter, status: str, body: bytes) -> None:
    """Send one response and request connection closure."""
    writer.write(
      f"HTTP/1.1 {status}\r\nContent-Type: text/xml; charset=utf-8\r\n"
      f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
      + body
    )
    await writer.drain()

  async def _exchange(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Read a framed request, invoke the handler, and record its response."""
    header = (await reader.readuntil(b"\r\n\r\n")).decode("iso-8859-1")
    lines = header.split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
      if line:
        key, value = line.split(":", 1)
        key = key.lower().strip()
        if key in headers:
          raise ValueError("Duplicate HTTP header")
        headers[key] = value.strip()
    if method != "POST":
      await self._respond(writer, "405 Method Not Allowed", b"POST required")
      return
    if "transfer-encoding" in headers or "content-length" not in headers:
      await self._respond(writer, "411 Length Required", b"Content-Length required")
      return
    length = int(headers["content-length"])
    if not 0 <= length <= self.max_body_size:
      await self._respond(writer, "413 Content Too Large", b"Callback too large")
      return
    if headers.get("expect", "").lower() == "100-continue":
      writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
      await writer.drain()
    body = await reader.readexactly(length)
    response = await self.handler(body)
    if capturer.capture_active:
      capturer.record(
        HTTPRawCommand(
          module="http_server",
          device_id=self.url,
          action=method,
          path=path,
          request_base64=base64.b64encode(body).decode("ascii"),
          status=200,
          response_base64=base64.b64encode(response).decode("ascii"),
        )
      )
    await self._respond(writer, "200 OK", response)

  async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Bound the lifetime of a connection and always release its socket."""
    task = asyncio.current_task()
    assert task is not None
    self._clients.add(task)
    try:
      await asyncio.wait_for(self._exchange(reader, writer), self.timeout)
    except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
      await self._respond(writer, "400 Bad Request", b"Malformed callback")
    except (ConnectionError, asyncio.TimeoutError):
      logger.debug("HTTP callback disconnected or timed out")
    except Exception:
      logger.exception("HTTP callback handler failed")
      await self._respond(writer, "500 Internal Server Error", b"Callback handler failed")
    finally:
      writer.close()
      try:
        await writer.wait_closed()
      except ConnectionError:
        pass
      self._clients.discard(task)

  async def stop(self) -> None:
    """Close the listener and cancel unfinished callbacks."""
    if self._server is not None:
      self._server.close()
      await self._server.wait_closed()
      self._server = None
    clients = list(self._clients)
    for task in clients:
      task.cancel()
    await asyncio.gather(*clients, return_exceptions=True)

  async def setup(self) -> None:
    """Start listening; repeated setup leaves the existing endpoint intact."""
    if self._server is None:
      self._server = await asyncio.start_server(self._handle, self.host, self.port)
      if not self._server.sockets:
        await self.stop()
        raise OSError("HTTP callback server could not bind a listening socket")

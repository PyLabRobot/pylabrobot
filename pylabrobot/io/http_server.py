"""Receive HTTP callbacks from instruments within a structured-concurrency lifespan."""

import base64
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Optional

import anyio
from anyio.abc import ByteStream, SocketAttribute
from anyio.streams.buffered import BufferedByteReceiveStream

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.capture import capturer
from pylabrobot.io.http import HTTPRawCommand

logger = logging.getLogger(__name__)

_MAX_HEADER_BYTES = 65536


async def local_address_for(host: str, port: int) -> str:
  """Find the IPv4 interface used to reach a device, without sending a packet."""

  def resolve() -> str:
    """Ask the routing table for the outgoing interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
      connection.connect((host, port))
      return str(connection.getsockname()[0])

  return await anyio.to_thread.run_sync(resolve)


class HTTPServer(HasLifespan):
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
    super().__init__()
    if max_body_size <= 0 or timeout <= 0:
      raise ValueError("max_body_size and timeout must be positive")
    self.handler = handler
    self.host = host
    self.port = port
    self.max_body_size = max_body_size
    self.timeout = timeout
    self._bound_port: Optional[int] = None
    self._active_clients = 0
    self._client_scopes: set[anyio.CancelScope] = set()

  @property
  def bound_port(self) -> int:
    """Return the listening port while the server lifespan is active."""
    if self._bound_port is None:
      raise RuntimeError("HTTP callback server is not active; enter its lifespan first")
    return self._bound_port

  @property
  def url(self) -> str:
    """Return the callback URL advertised to the device."""
    return f"http://{self.host}:{self.bound_port}/"

  def _clear_listener_state(self) -> None:
    """Clear bound-port state when leaving the server lifespan."""
    self._bound_port = None

  def _cancel_clients(self) -> None:
    """Cancel in-flight callback handlers when the server lifespan exits."""
    for scope in tuple(self._client_scopes):
      scope.cancel()

  async def _serve(
    self, listener: anyio.abc.Listener[ByteStream], scope: anyio.CancelScope
  ) -> None:
    """Accept incoming callbacks until the server's own lifespan unwinds."""
    with scope:
      await listener.serve(self._handle)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Bind the callback listener and serve connections within this lifespan."""
    await super()._enter_lifespan(stack)
    stack.callback(self._clear_listener_state)
    listener = await anyio.create_tcp_listener(local_host=self.host, local_port=self.port)
    await stack.enter_async_context(listener)
    self._bound_port = int(listener.extra(SocketAttribute.local_port))
    task_group = await stack.enter_task_group()
    serve_scope = anyio.CancelScope(shield=True)
    stack.callback(task_group.cancel_scope.cancel)
    stack.callback(self._cancel_clients)
    stack.callback(serve_scope.cancel)
    task_group.start_soon(self._serve, listener, serve_scope)

  async def _respond(self, stream: ByteStream, status: str, body: bytes) -> None:
    """Send one response and request connection closure."""
    await stream.send(
      f"HTTP/1.1 {status}\r\nContent-Type: text/xml; charset=utf-8\r\n"
      f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
      + body
    )

  async def _exchange(self, stream: ByteStream) -> None:
    """Read a framed request, invoke the handler, and record its response."""
    buffered = BufferedByteReceiveStream(stream)
    header = (await buffered.receive_until(b"\r\n\r\n", _MAX_HEADER_BYTES)).decode("iso-8859-1")
    lines = header.split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
      if line:
        key, value = line.split(":", 1)
        key = key.lower().strip()
        if key in headers:
          raise ValueError("Duplicate HTTP header")
        headers[key] = value.strip()
    if method != "POST":
      await self._respond(stream, "405 Method Not Allowed", b"POST required")
      return
    if "transfer-encoding" in headers or "content-length" not in headers:
      await self._respond(stream, "411 Length Required", b"Content-Length required")
      return
    length = int(headers["content-length"])
    if not 0 <= length <= self.max_body_size:
      await self._respond(stream, "413 Content Too Large", b"Callback too large")
      return
    if headers.get("expect", "").lower() == "100-continue":
      await stream.send(b"HTTP/1.1 100 Continue\r\n\r\n")
    body = await buffered.receive_exactly(length)
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
    await self._respond(stream, "200 OK", response)

  async def _handle(self, stream: ByteStream) -> None:
    """Bound the lifetime of a connection and always release its socket."""
    self._active_clients += 1
    client_scope = anyio.CancelScope(shield=True)
    self._client_scopes.add(client_scope)
    try:
      with client_scope:
        try:
          with anyio.fail_after(self.timeout):
            await self._exchange(stream)
        except (ValueError, anyio.IncompleteRead, anyio.DelimiterNotFound):
          with anyio.move_on_after(self.timeout):
            try:
              await self._respond(stream, "400 Bad Request", b"Malformed callback")
            except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
              pass
        except (
          OSError,
          TimeoutError,
          anyio.BrokenResourceError,
          anyio.ClosedResourceError,
          anyio.EndOfStream,
        ):
          logger.debug("HTTP callback disconnected or timed out")
        except Exception:
          logger.exception("HTTP callback handler failed")
          with anyio.move_on_after(self.timeout):
            try:
              await self._respond(stream, "500 Internal Server Error", b"Callback handler failed")
            except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
              pass
    finally:
      self._client_scopes.discard(client_scope)
      self._active_clients -= 1
      with anyio.CancelScope(shield=True):
        with anyio.move_on_after(1.0):
          try:
            await stream.aclose()
          except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass

# Adapted from pyqwest's `pyqwest.testing.ASGITransport` (pyqwest 0.11.0,
# https://github.com/curioswitch/pyqwest), under the MIT License:
#
# Copyright (c) CurioSwitch (oss@curioswitch.org)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
# associated documentation files (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge, publish, distribute,
# sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or
# substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
# NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
# OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""A pyqwest transport that hands each request to an ASGI application in this process.

`ResourceServer.local()` serves its store through it: the client speaks Connect exactly as it
would to a remote store, with no socket in between. Only what serving a store needs is kept from
pyqwest's version: HTTP requests (no lifespan), an ASGI 3 application, and gzip or no response
encoding.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import zlib
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

from pyqwest import Headers, HTTPVersion, ReadError, Request, Response, WriteError

# An ASGI 3 application: called with a scope, `receive` and `send`.
ASGIApp = Callable[
  [
    Dict[str, Any],
    Callable[[], Awaitable[Dict[str, Any]]],
    Callable[[Dict[str, Any]], Awaitable[None]],
  ],
  Awaitable[None],
]

_ASGI = {"version": "3.0", "spec_version": "2.5"}
_EXTENSIONS: Dict[str, Any] = {"http.response.trailers": {}}


def _http_version(version: HTTPVersion) -> str:
  """An HTTP version as an ASGI scope gives it."""
  if version == HTTPVersion.HTTP2:
    return "2"
  if version == HTTPVersion.HTTP3:
    return "3"
  return "1.1"


class _Cancelled(Exception):
  """Put on a response's queue when its body is no longer read."""


class _Gzip:
  def __init__(self) -> None:
    self._decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)

  def feed(self, data: bytes, end: bool) -> bytes:
    out = self._decompressor.decompress(data)
    if end:
      out += self._decompressor.flush()
    return out


class _Identity:
  def feed(self, data: bytes, end: bool) -> bytes:
    return data


def _decompressor(encoding: Optional[str]) -> Union[_Gzip, _Identity]:
  if encoding is None or encoding == "identity":
    return _Identity()
  if encoding == "gzip":
    return _Gzip()
  raise ReadError(f"unsupported response encoding: {encoding}")


class ASGITransport:
  """A pyqwest `Transport` that runs each request through an ASGI application, in this process.

  Args:
    app: the ASGI 3 application.
    http_version: the HTTP version requests claim to be.
    client: the (host, port) the scope gives as the client's address.
  """

  def __init__(
    self,
    app: ASGIApp,
    http_version: HTTPVersion = HTTPVersion.HTTP2,
    client: Tuple[str, int] = ("127.0.0.1", 111),
  ) -> None:
    self._app = app
    self._http_version = http_version
    self._client = client
    self._state: Dict[str, Any] = {}

  async def execute(self, request: Request) -> Response:
    """Run `request` through the application: the response starts once the application does."""
    parsed = urlparse(request.url)
    raw_path = parsed.path or "/"
    headers = Headers(request.headers.items())
    if "host" not in headers:
      headers["host"] = parsed.netloc
    scope: Dict[str, Any] = {
      "type": "http",
      "asgi": _ASGI,
      "http_version": _http_version(self._http_version),
      "method": request.method,
      "scheme": parsed.scheme,
      "path": unquote(raw_path),
      "raw_path": raw_path.encode(),
      "query_string": parsed.query.encode(),
      "headers": [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()],
      "server": (parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)),
      "client": self._client,
      "extensions": _EXTENSIONS,
      "state": self._state,
      "root_path": "",
    }

    received: "asyncio.Queue[Union[bytes, Exception, None]]" = asyncio.Queue(1)
    receiving = asyncio.Event()

    async def read_request() -> None:
      content = request.content
      try:
        await receiving.wait()
        if isinstance(content, bytes):
          await received.put(content)
        else:
          async for chunk in content:
            if not isinstance(chunk, bytes):
              raise WriteError("Request not bytes object")
            await received.put(chunk)
        await received.put(None)
      except Exception as error:
        await received.put(error)
      finally:
        if inspect.isasyncgen(content):
          await content.aclose()

    # Its own task, so the request body stops being read once the response is closed.
    request_task = asyncio.create_task(read_request())

    async def receive() -> Dict[str, Any]:
      receiving.set()
      chunk = await received.get()
      if chunk is None:
        return {"type": "http.request", "body": b"", "more_body": False}
      if isinstance(chunk, Exception):
        raise WriteError(f"Request failed: {chunk}") from chunk
      return {"type": "http.request", "body": chunk, "more_body": True}

    sent: "asyncio.Queue[Union[Dict[str, Any], Exception]]" = asyncio.Queue()

    async def send(message: Dict[str, Any]) -> None:
      await sent.put(message)

    async def run_app() -> None:
      try:
        await self._app(scope, receive, send)
      except asyncio.TimeoutError as error:
        sent.put_nowait(TimeoutError(str(error)))
      except Exception as error:
        sent.put_nowait(error)

    app_task = asyncio.create_task(run_app())
    message = await sent.get()
    if isinstance(message, Exception):
      request_task.cancel()
      with contextlib.suppress(BaseException):
        await app_task
        await request_task
      if isinstance(message, (ConnectionError, TimeoutError)):
        raise message
      return Response(
        status=500,
        http_version=self._http_version,
        headers=Headers((("content-type", "text/plain"),)),
        content=str(message).encode(),
      )

    assert message["type"] == "http.response.start"
    response_headers = Headers(
      (k.decode("utf-8"), v.decode("utf-8")) for k, v in message.get("headers", [])
    )
    trailers = (
      Headers()
      if self._http_version == HTTPVersion.HTTP2 and request.headers.get("te") == "trailers"
      else None
    )
    content = _ResponseContent(
      sent,
      request_task,
      app_task,
      trailers,
      _decompressor(response_headers.get("content-encoding")),
      read_trailers=message.get("trailers", False),
    )
    return Response(
      status=message["status"],
      http_version=self._http_version,
      headers=response_headers,
      content=content,
      trailers=trailers,
    )


class _ResponseContent(AsyncIterator[bytes]):
  """A response body as the application sends it, and its trailers once the body is read."""

  def __init__(
    self,
    sent: "asyncio.Queue[Union[Dict[str, Any], Exception]]",
    request_task: "asyncio.Task[None]",
    app_task: "asyncio.Task[None]",
    trailers: Optional[Headers],
    decompressor: Union[_Gzip, _Identity],
    read_trailers: bool,
  ) -> None:
    self._sent = sent
    self._request_task = request_task
    self._app_task = app_task
    self._trailers = trailers
    self._decompressor = decompressor
    self._read_trailers = read_trailers
    self._closed = False

  def __aiter__(self) -> AsyncIterator[bytes]:
    return self

  async def __anext__(self) -> bytes:
    if self._closed:
      raise StopAsyncIteration
    error: Optional[BaseException] = None
    while True:
      message = await self._sent.get()
      if isinstance(message, _Cancelled):
        error = StopAsyncIteration()
        break
      if isinstance(message, (WriteError, TimeoutError)):
        error = message
        break
      if isinstance(message, ReadError):
        raise message
      if isinstance(message, Exception):
        raise ReadError("Error reading response body") from message
      if message["type"] == "http.response.body":
        more_body = message.get("more_body", False)
        if not more_body and not self._read_trailers:
          await self._cleanup()
        body: bytes = message.get("body", b"")
        if body or self._closed:
          return self._decompressor.feed(body, end=not more_body)
      elif message["type"] == "http.response.trailers":
        if self._trailers is not None:
          pairs: List[Tuple[bytes, bytes]] = message.get("headers", [])
          for k, v in pairs:
            self._trailers.add(k.decode("utf-8"), v.decode("utf-8"))
        if not message.get("more_trailers", False):
          break
    await self._cleanup()
    if error is not None:
      raise error
    raise StopAsyncIteration

  async def aclose(self) -> None:
    if self._closed:
      return
    self._closed = True
    self._sent.put_nowait(ReadError("Response body read cancelled"))
    await self._cleanup()

  async def _cleanup(self) -> None:
    self._closed = True
    self._request_task.cancel()
    with contextlib.suppress(BaseException):
      await self._request_task
    self._app_task.cancel()
    with contextlib.suppress(BaseException):
      await self._app_task

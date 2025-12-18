from __future__ import annotations

import asyncio
import http.server
import random
import socketserver
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, urlsplit


class SCILABackend:
  """
  One in-flight "command" at a time (raise on concurrent calls).
  The "background worker" is an HTTP server:
    - Your async methods "send" (optional hook) then await a response.
    - An incoming HTTP request resolves the currently-pending Future.
  """

  # ---- request model (kept inside the class, per your ask) ----
  @dataclass(frozen=True)
  class Request:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes

  # Optional hook: called when a method starts a command (e.g., send to hardware)
  SendFn = Callable[[str, Any], Awaitable[None]]

  def __init__(
    self,
    host: str = "127.0.0.1",
    on_send: Optional[SendFn] = None,
  ) -> None:
    self.host = host
    self._on_send = on_send

    # single "in-flight token"
    self._in_flight = asyncio.Lock()

    # pending response plumbing
    self.pending: Optional[asyncio.Future[Any]] = None
    self._matcher: Optional[Callable[[SCILABackend.Request], Optional[Any]]] = None

    # server plumbing
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._httpd: Optional[socketserver.TCPServer] = None
    self._server_task: Optional[asyncio.Task[None]] = None
    self._closed = False

  # ---------------- server lifecycle ----------------

  @property
  def bound_port(self) -> int:
    if self._httpd is None:
      raise RuntimeError("Server not started yet")
    return self._httpd.server_address[1]

  async def start(self) -> None:
    if self._httpd is not None:
      return
    if self._closed:
      raise RuntimeError("Bridge is closed")

    self._loop = asyncio.get_running_loop()
    outer = self

    class _Handler(http.server.BaseHTTPRequestHandler):
      server_version = "OneInFlightHTTPBridge/0.1"

      def log_message(self, fmt: str, *args) -> None:
        return  # silence

      def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

      def _do(self) -> None:
        assert outer._loop is not None

        parsed = urlsplit(self.path)
        req = SCILABackend.Request(
          method=self.command,
          path=parsed.path,
          query=parsed.query,
          headers={k.lower(): v for k, v in self.headers.items()},
          body=self._read_body(),
        )

        fut = asyncio.run_coroutine_threadsafe(outer._on_http(req), outer._loop)
        try:
          resp_body = fut.result()
          status = 200
        except Exception as e:
          resp_body = f"Internal Server Error: {type(e).__name__}: {e}\n".encode()
          status = 500

        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

      def do_GET(self) -> None:
        self._do()

      def do_POST(self) -> None:
        self._do()

      def do_PUT(self) -> None:
        self._do()

      def do_DELETE(self) -> None:
        self._do()

    # allow multiple clients; your in-flight lock still enforces serialization
    self._httpd = http.server.ThreadingHTTPServer((self.host, 0), _Handler)

    async def run_server() -> None:
      assert self._httpd is not None
      await asyncio.to_thread(self._httpd.serve_forever)

    self._server_task = asyncio.create_task(run_server(), name="http-server")

  async def close(self) -> None:
    self._closed = True
    if self._httpd is None:
      return

    self._httpd.shutdown()
    self._httpd.server_close()

    if self._server_task is not None:
      await self._server_task

    self._httpd = None
    self._server_task = None

  async def _on_http(self, req: Request) -> bytes:
    """
    Called on the asyncio loop for every incoming HTTP request.
    If there's a pending command, try to match and resolve it.
    """
    fut = self.pending
    matcher = self._matcher

    if fut is None or fut.done() or matcher is None:
      return b"no pending command\n"

    try:
      payload = matcher(req)
    except Exception as e:
      # matcher bug; fail the pending request
      if not fut.done():
        fut.set_exception(e)
      return b"pending command failed (matcher exception)\n"

    if payload is None:
      return b"ignored (does not match pending command)\n"

    if not fut.done():
      fut.set_result(payload)
    return b"delivered\n"

  # ---------------- one-in-flight call helper ----------------

  async def _call(
    self,
    kind: str,
    send_payload: Any,
    matcher: Callable[[Request], Optional[Any]],
  ) -> Any:
    if self._closed:
      raise RuntimeError("Bridge is closed")

    await self.start()

    # raise (don’t wait) if concurrent
    if self._in_flight.locked():
      raise RuntimeError("can't send multiple commands at the same time")

    await self._in_flight.acquire()
    fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    self.pending = fut
    self._matcher = matcher

    try:
      # "send ASAP" hook (optional)
      if self._on_send is not None:
        await self._on_send(kind, send_payload)

      # wait for HTTP to resolve it
      return await fut
    finally:
      self._matcher = None
      self.pending = None
      self._in_flight.release()

  # ---------------- example command API ----------------
  # These are just examples of how you can define matchers.

  async def fetch_status(self) -> str:
    def match(req: SCILABackend.Request) -> Optional[str]:
      # accept: GET /status  (or anything with path "/status")
      if req.path != "/status":
        return None
      return req.body.decode("utf-8", "replace").strip() or "OK"

    return await self._call("fetch_status", None, match)

  async def compute(self, x: int) -> int:
    def match(req: SCILABackend.Request) -> Optional[int]:
      if req.path != "/compute":
        return None
      qs = parse_qs(req.query)
      if "result" in qs and qs["result"]:
        return int(qs["result"][0])
      # or body contains an int
      b = req.body.decode("utf-8", "replace").strip()
      return int(b) if b else None

    # sending payload is just metadata for your on_send hook
    return await self._call("compute", {"x": x}, match)

  async def echo(self, msg: str) -> str:
    token = f"{random.random():.16f}"  # example correlation token if you want

    def match(req: SCILABackend.Request) -> Optional[str]:
      if req.path != "/echo":
        return None
      qs = parse_qs(req.query)
      if qs.get("token", [None])[0] != token:
        return None
      return req.body.decode("utf-8", "replace")

    # on_send could tell the remote side to POST back with this token
    return await self._call("echo", {"msg": msg, "token": token}, match)


# ---- demo ----
# Start the server and then (from another terminal) resolve a call by hitting it:

#   curl -X POST http://127.0.0.1:8080/status -d "OK"

# or:
#   curl -X POST "http://127.0.0.1:8080/compute?result=42"
#   curl -X POST "http://127.0.0.1:8080/echo?token=<printed_token>" -d "hello"


async def main():
  b = SCILABackend()
  await b.start()
  print(f"Serving on http://127.0.0.1:{b.bound_port}")

  print("awaiting /status ...")
  print(await b.fetch_status())

  print("awaiting /compute ...")
  print(await b.compute(123))

  print("awaiting /echo ...")
  print(await b.echo("hello world"))

  await b.close()


if __name__ == "__main__":
  asyncio.run(main())

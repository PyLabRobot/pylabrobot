"""Inheco flavor of sila"""

from __future__ import annotations

import datetime
import http.server
import logging
import random
import socket
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan

_CALLBACK_REQUEST_TIMEOUT_S = 10.0
_MACHINE_REQUEST_TIMEOUT_S = 10.0


class _CallbackHTTPServer(http.server.ThreadingHTTPServer):
  """Track accepted callback sockets so shutdown can unblock every handler."""

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    self._active_requests: set[socket.socket] = set()
    self._active_requests_lock = threading.Lock()
    super().__init__(*args, **kwargs)

  def process_request(self, request: Any, client_address: Any) -> None:
    """Track an accepted socket before starting its request thread."""
    with self._active_requests_lock:
      self._active_requests.add(request)
    try:
      super().process_request(request, client_address)
    except BaseException:
      with self._active_requests_lock:
        self._active_requests.discard(request)
      raise

  def shutdown_request(self, request: Any) -> None:
    """Stop tracking a request when its handler releases the socket."""
    with self._active_requests_lock:
      self._active_requests.discard(request)
    super().shutdown_request(request)

  def close_active_requests(self) -> None:
    """Close accepted sockets so blocked request handlers can finish."""
    with self._active_requests_lock:
      requests = tuple(self._active_requests)
      self._active_requests.clear()
    for request in requests:
      try:
        request.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      request.close()


SOAP_RESPONSE_ResponseEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <ResponseEventResponse xmlns="http://sila.coop">
      <ResponseEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0.0006262S</duration>
        <deviceClass>0</deviceClass>
      </ResponseEventResult>
    </ResponseEventResponse>
  </s:Body>
</s:Envelope>"""


SOAP_RESPONSE_StatusEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <StatusEventResponse xmlns="http://sila.coop">
      <StatusEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0.0005967S</duration>
        <deviceClass>0</deviceClass>
      </StatusEventResult>
    </StatusEventResponse>
  </s:Body>
</s:Envelope>"""


def _get_local_ip(machine_ip: str) -> str:
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    # Doesn't actually connect, just determines the route
    s.connect((machine_ip, 1))
    local_ip: str = s.getsockname()[0]  # type: ignore
    if local_ip is None or local_ip.startswith("127."):
      raise RuntimeError("Could not determine local IP address.")
  finally:
    s.close()
  return local_ip


class SiLAError(RuntimeError):
  def __init__(self, code: int, message: str, command: str, details: Optional[dict] = None):
    self.code = code
    self.message = message
    self.command = command
    self.details = details or {}


class InhecoSiLAInterface(HasLifespan):
  @dataclass(frozen=True)
  class _HTTPRequest:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes

  @dataclass
  class _SiLACommand:
    name: str
    request_id: int
    completed: anyio.Event
    result: Optional[ET.Element] = None
    error: Optional[Exception] = None

  def __init__(
    self,
    machine_ip: str,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
  ) -> None:
    super().__init__()
    self._client_ip = client_ip or _get_local_ip(machine_ip)
    self._machine_ip = machine_ip
    self._logger = logger or logging.getLogger(__name__)

    # single "in-flight token"
    self._making_request: Optional[anyio.Lock] = None

    # pending command information
    self._pending: Optional[InhecoSiLAInterface._SiLACommand] = None

    # server plumbing
    self._portal: Optional[anyio.from_thread.BlockingPortal] = None
    self._httpd: Optional[_CallbackHTTPServer] = None
    self._callback_server_started: Optional[anyio.Event] = None
    self._callback_server_stopped: Optional[anyio.Event] = None
    self._callback_server_startup_resolved: Optional[anyio.Event] = None
    self._callback_server_worker_requested = False

  @property
  def client_ip(self) -> str:
    return self._client_ip

  @property
  def machine_ip(self) -> str:
    return self._machine_ip

  @property
  def bound_port(self) -> int:
    if self._httpd is None:
      raise RuntimeError("Server not started yet")
    return self._httpd.server_address[1]

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Bind and own the HTTP callback server and AnyIO thread bridge."""
    await super()._enter_lifespan(stack)

    task_group = await stack.enter_task_group()
    stack.callback(self._clear_run_state)
    self._making_request = anyio.Lock()
    self._pending = None
    self._portal = await stack.enter_task_group_context(anyio.from_thread.BlockingPortal())
    self._callback_server_started = anyio.Event()
    self._callback_server_stopped = anyio.Event()
    self._callback_server_startup_resolved = anyio.Event()
    self._callback_server_worker_requested = False
    outer = self

    class _Handler(http.server.BaseHTTPRequestHandler):
      server_version = "OneInFlightHTTPBridge/0.1"

      def setup(self) -> None:
        """Initialize streams with a finite socket-operation timeout."""
        super().setup()
        self.connection.settimeout(_CALLBACK_REQUEST_TIMEOUT_S)

      def log_message(self, fmt: str, *args) -> None:
        return  # silence

      def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

      def _do(self) -> None:
        portal = outer._portal
        assert portal is not None

        parsed = urllib.parse.urlsplit(self.path)
        req = InhecoSiLAInterface._HTTPRequest(
          method=self.command,
          path=parsed.path,
          query=parsed.query,
          headers={k.lower(): v for k, v in self.headers.items()},
          body=self._read_body(),
        )

        try:
          resp_body = portal.call(outer._on_http, req)
          status = 200
        except Exception as e:
          resp_body = f"Internal Server Error: {type(e).__name__}: {e}\n".encode()
          status = 500

        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
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

    # port 0: choose random free port
    server = await anyio.to_thread.run_sync(
      lambda: _CallbackHTTPServer((self._client_ip, 0), _Handler),
      abandon_on_cancel=False,
    )
    self._httpd = server
    server.daemon_threads = False
    stack.push_shielded_async_callback(self._close_callback_server)
    stack.callback(task_group.cancel_scope.cancel)
    task_group.start_soon(self._serve_callbacks)
    await self._callback_server_started.wait()

  def _clear_run_state(self) -> None:
    """Clear state published for one lifespan, including partial entry."""
    self._pending = None
    self._httpd = None
    self._callback_server_started = None
    self._callback_server_stopped = None
    self._callback_server_startup_resolved = None
    self._callback_server_worker_requested = False
    self._portal = None
    self._making_request = None

  async def _serve_callbacks(self) -> None:
    """Run the blocking callback server in lifespan-owned worker work."""
    server = self._httpd
    started = self._callback_server_started
    stopped = self._callback_server_stopped
    startup_resolved = self._callback_server_startup_resolved
    if server is None or started is None or stopped is None or startup_resolved is None:
      raise RuntimeError("Callback server is not initialized")
    self._callback_server_worker_requested = True

    def serve() -> None:
      def mark_started() -> None:
        started.set()
        startup_resolved.set()

      anyio.from_thread.run_sync(mark_started)
      server.serve_forever()

    try:
      await anyio.to_thread.run_sync(serve, abandon_on_cancel=False)
    finally:
      stopped.set()
      startup_resolved.set()

  async def _close_callback_server(self) -> None:
    """Stop callback acceptance, await its worker, and release its socket."""
    pending = self._pending
    self._pending = None
    if pending is not None and not pending.completed.is_set():
      pending.error = RuntimeError("SiLA callback server stopped before command completion")
      pending.completed.set()

    server = self._httpd
    started = self._callback_server_started
    stopped = self._callback_server_stopped
    startup_resolved = self._callback_server_startup_resolved
    if server is not None:
      try:
        if self._callback_server_worker_requested:
          if startup_resolved is None:
            raise RuntimeError("Callback server startup state is unavailable")
          await startup_resolved.wait()
        if started is not None and started.is_set():
          try:
            await anyio.to_thread.run_sync(server.shutdown, abandon_on_cancel=False)
            if stopped is not None:
              await stopped.wait()
          finally:
            try:
              await anyio.to_thread.run_sync(server.close_active_requests, abandon_on_cancel=False)
            finally:
              await anyio.to_thread.run_sync(server.server_close, abandon_on_cancel=False)
        else:
          await anyio.to_thread.run_sync(server.server_close, abandon_on_cancel=False)
      finally:
        self._httpd = None

  async def _on_http(self, req: _HTTPRequest) -> bytes:
    """
    Called on the owning AnyIO event loop for every incoming HTTP request.
    If there's a pending command, try to match and resolve it.
    """

    cmd = self._pending

    if cmd is not None and not cmd.completed.is_set():
      from pylabrobot.inheco.scila.soap import soap_decode

      response_event = soap_decode(req.body.decode("utf-8"))
      if "ResponseEvent" in response_event:
        request_id = response_event["ResponseEvent"].get("requestId")
        if request_id != cmd.request_id:
          self._logger.warning("Request ID does not match pending command.")
        else:
          return_value = response_event["ResponseEvent"].get("returnValue", {})
          return_code = return_value.get("returnCode")
          if return_code != 3:  # error
            err_msg = return_value.get("message", "Unknown error").replace("\n", " ")
            cmd.error = RuntimeError(
              f"Command {cmd.name} failed with code {return_code}: '{err_msg}'"
            )
          else:
            response_data = response_event["ResponseEvent"].get("responseData", "")
            root = ET.fromstring(response_data)
            cmd.result = root
          cmd.completed.set()
          if self._pending is cmd:
            self._pending = None
    else:
      self._logger.warning("No pending command to match response to.")

    if "ResponseEvent" in req.body.decode("utf-8"):
      return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")
    if "StatusEvent" in req.body.decode("utf-8"):
      return SOAP_RESPONSE_StatusEventResponse.encode("utf-8")
    self._logger.warning("Unknown event type received.")
    return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

  def _get_return_code_and_message(self, command_name: str, response: Any) -> Tuple[int, str]:
    resp_level = response.get(f"{command_name}Response", {})  # first level
    result_level = resp_level.get(f"{command_name}Result", {})  # second level
    return_code = result_level.get("returnCode")
    if return_code is None:
      raise ValueError(f"returnCode not found in response for {command_name}")
    return return_code, result_level.get("message", "")

  def _make_request_id(self):
    return random.randint(1, 2**31 - 1)

  async def send_command(
    self,
    command: str,
    **kwargs,
  ) -> Any:
    from pylabrobot.inheco.scila.soap import XSI, soap_decode, soap_encode

    making_request = self._making_request
    if not self.is_active or making_request is None:
      raise RuntimeError("Inheco SiLA interface is not active")

    request_id = self._make_request_id()
    cmd_xml = soap_encode(
      command,
      {"requestId": request_id, **kwargs},
      method_ns="http://sila.coop",
      extra_method_xmlns={"i": XSI},
    )

    # make POST request to machine
    url = f"http://{self._machine_ip}:8080/"
    req = urllib.request.Request(
      url=url,
      data=cmd_xml.encode("utf-8"),
      method="POST",
      headers={
        "Content-Type": "text/xml; charset=utf-8",
        "Content-Length": str(len(cmd_xml)),
        "SOAPAction": f"http://sila.coop/{command}",
        "Expect": "100-continue",
        "Accept-Encoding": "gzip, deflate",
      },
    )

    if making_request.locked() or self._pending is not None:
      raise RuntimeError("can't send multiple commands at the same time")

    async with making_request:
      pending: Optional[InhecoSiLAInterface._SiLACommand] = None
      try:

        def _do_request() -> bytes:
          with urllib.request.urlopen(req, timeout=_MACHINE_REQUEST_TIMEOUT_S) as resp:
            return resp.read()  # type: ignore

        body = await anyio.to_thread.run_sync(_do_request, abandon_on_cancel=False)
        response = soap_decode(body.decode("utf-8"))
        return_code, message = self._get_return_code_and_message(command, response)
        if return_code == 1:  # success
          return response
        elif return_code == 2:  # concurrent command
          result = response.get(f"{command}Response", {}).get(f"{command}Result", {})
          duration = result.get("duration")
          if not isinstance(duration, datetime.timedelta):
            raise ValueError(f"duration not found or invalid in accepted response for {command}")
          pending = InhecoSiLAInterface._SiLACommand(
            name=command, request_id=request_id, completed=anyio.Event()
          )
          self._pending = pending
          callback_timeout: TimeoutError | None = None
          with anyio.CancelScope(shield=True):
            try:
              with anyio.fail_after(duration.total_seconds() + _CALLBACK_REQUEST_TIMEOUT_S):
                await pending.completed.wait()
            except TimeoutError as error:
              callback_timeout = error
          if callback_timeout is not None:
            raise callback_timeout
          if pending.error is not None:
            raise pending.error
          if pending.result is None:
            raise RuntimeError("SiLA callback completed without a result")
          await anyio.lowlevel.checkpoint()
          return pending.result
        else:
          raise RuntimeError(f"command {command} failed: {return_code} {message}")
      finally:
        if pending is not None and pending.completed.is_set() and self._pending is pending:
          self._pending = None


def _parse_scalar(text: Optional[str], tag: str) -> object:
  if text is None:
    return None
  s = text.strip()
  t = tag.lower()
  if t in ("float64", "float32", "double", "single"):
    return float(s)
  if t in ("int32", "int64", "uint32", "uint64", "integer"):
    return int(s)
  if t in ("boolean", "bool"):
    return s.lower() == "true"
  return s


def _get_param(root: ET.Element, name: str):
  p = root.find(f".//Parameter[@name='{name}']")
  if p is None or len(p) == 0:
    raise RuntimeError(f"Response missing parameter '{name}'")
  child = next(iter(p))
  return _parse_scalar(child.text, child.tag)


def get_params(root: ET.Element, names: list[str]) -> dict[str, object]:
  return {n: _get_param(root, n) for n in names}

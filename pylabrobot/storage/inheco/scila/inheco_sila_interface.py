from __future__ import annotations

import asyncio
import http.server
import logging
import random
import socket
import socketserver
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from pylabrobot.storage.inheco.scila.soap import XSI, soap_decode, soap_encode

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


class InhecoSiLAInterface:
  @dataclass(frozen=True)
  class _HTTPRequest:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes

  @dataclass(frozen=True)
  class _SiLACommand:
    name: str
    request_id: int
    fut: asyncio.Future[Any]

  def __init__(
    self,
    machine_ip: str,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
  ) -> None:
    self._client_ip = client_ip or _get_local_ip(machine_ip)
    self._machine_ip = machine_ip
    self._logger = logger or logging.getLogger(__name__)

    # single "in-flight token"
    self._making_request = asyncio.Lock()

    # pending command information
    self._pending: Optional[InhecoSiLAInterface._SiLACommand] = None

    # server plumbing
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._httpd: Optional[socketserver.TCPServer] = None
    self._server_task: Optional[asyncio.Task[None]] = None
    self._closed = False

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

        parsed = urllib.parse.urlsplit(self.path)
        req = InhecoSiLAInterface._HTTPRequest(
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
    self._httpd = http.server.ThreadingHTTPServer((self._client_ip, 0), _Handler)

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

  async def _on_http(self, req: _HTTPRequest) -> bytes:
    """
    Called on the asyncio loop for every incoming HTTP request.
    If there's a pending command, try to match and resolve it.
    """

    cmd = self._pending

    if cmd is not None and not cmd.fut.done():
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
            cmd.fut.set_exception(
              RuntimeError(f"Command {cmd.name} failed with code {return_code}: '{err_msg}'")
            )
          else:
            response_data = response_event["ResponseEvent"].get("responseData", "")
            root = ET.fromstring(response_data)
            cmd.fut.set_result(root)
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

  async def setup(self) -> None:
    await self.start()

  def _make_request_id(self):
    return random.randint(1, 2**31 - 1)

  async def send_command(
    self,
    command: str,
    **kwargs,
  ) -> Any:
    if self._closed:
      raise RuntimeError("Bridge is closed")

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

    if self._making_request.locked():
      raise RuntimeError("can't send multiple commands at the same time")

    async with self._making_request:
      try:

        def _do_request() -> bytes:
          with urllib.request.urlopen(req) as resp:
            return resp.read()  # type: ignore

        body = await asyncio.to_thread(_do_request)
        return_code, message = self._get_return_code_and_message(
          command, soap_decode(body.decode("utf-8"))
        )
        if return_code == 1:  # success
          return soap_decode(body.decode("utf-8"))
        elif return_code == 2:  # concurrent command
          fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
          self._pending = InhecoSiLAInterface._SiLACommand(
            name=command, request_id=request_id, fut=fut
          )
          return await fut  # wait for response to be handled in _on_http
        else:
          raise RuntimeError(f"command {command} failed: {return_code} {message}")
      finally:
        self._pending = None

from __future__ import annotations

import datetime
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

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, AsyncResource
from pylabrobot.storage.inheco.scila.soap import (
  XSI,
  _localname,
  soap_body_payload,
  soap_decode,
  soap_encode,
)

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


SOAP_RESPONSE_DataEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <DataEventResponse xmlns="http://sila.coop">
      <DataEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0S</duration>
        <deviceClass>0</deviceClass>
      </DataEventResult>
    </DataEventResponse>
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
    super().__init__(f"Command {command} failed with code {code}: '{message}'")


class InhecoSiLAInterface(AsyncResource):
  @dataclass(frozen=True)
  class _HTTPRequest:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes

  @dataclass
  class _CommandState:
    result: Any = None
    error: Optional[Exception] = None

  @dataclass(frozen=True)
  class _SiLACommand:
    name: str
    request_id: int
    event: anyio.Event
    state: InhecoSiLAInterface._CommandState

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
    self._making_request = anyio.Lock()

    # pending command information
    self._pending: Optional[InhecoSiLAInterface._SiLACommand] = None

    # server plumbing
    self._httpd: Optional[socketserver.TCPServer] = None

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
    outer = self

    class _Handler(http.server.BaseHTTPRequestHandler):
      server_version = "OneInFlightHTTPBridge/0.1"

      def log_message(self, fmt: str, *args) -> None:
        return  # silence

      def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

      def _do(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        req = InhecoSiLAInterface._HTTPRequest(
          method=self.command,
          path=parsed.path,
          query=parsed.query,
          headers={k.lower(): v for k, v in self.headers.items()},
          body=self._read_body(),
        )

        try:
          resp_body = anyio.from_thread.run(outer._on_http, req)
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

      def _serve():
        assert self._httpd is not None
        with self._httpd:
          self._httpd.serve_forever()

      await anyio.to_thread.run_sync(_serve)

    async def cleanup():
      assert self._httpd is not None
      await anyio.to_thread.run_sync(self._httpd.shutdown)
      self._httpd = None

    tg = await stack.enter_async_context(anyio.create_task_group())
    stack.push_shielded_async_callback(cleanup)
    tg.start_soon(run_server)

  async def _on_http(self, req: _HTTPRequest) -> bytes:
    """
    Called on the asyncio loop for every incoming HTTP request.
    If there's a pending command, try to match and resolve it.
    """

    cmd = self._pending

    try:
      xml_str = req.body.decode("utf-8")
      payload = soap_body_payload(xml_str)
      tag_local = _localname(payload.tag)

      if cmd is not None and not cmd.event.is_set() and tag_local == "ResponseEvent":
        response_event = soap_decode(xml_str)
        if response_event["ResponseEvent"].get("requestId") == cmd.request_id:
          ret = response_event["ResponseEvent"].get("returnValue", {})
          rc = ret.get("returnCode")
          if rc != 3:  # 3=Success
            cmd.state.error = SiLAError(
              rc, ret.get("message", "").replace(chr(10), " "), cmd.name, details=ret
            )
          else:
            cmd.state.result = (
              ET.fromstring(d)
              if (d := response_event["ResponseEvent"].get("responseData"))
              else ET.Element("EmptyResponse")
            )
          cmd.event.set()

      if tag_local == "DataEvent":
        try:
          raw = next(e.text for e in payload.iter() if _localname(e.tag) == "dataValue")
          any_data_elem = ET.fromstring(raw).find(".//AnyData")  # type: ignore[arg-type]
          assert any_data_elem is not None and any_data_elem.text is not None
          series = ET.fromstring(any_data_elem.text).findall(".//dataSeries")
          data = {}
          for s in series:
            val = s.findall(".//integerValue")[-1].text
            unit = s.get("unit")
            data[s.get("nameId")] = f"{val} {unit}" if unit else val
          print(f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] [SiLA DataEvent] {data}")
        except Exception:
          pass
        return SOAP_RESPONSE_DataEventResponse.encode("utf-8")

      if tag_local == "StatusEvent":
        return SOAP_RESPONSE_StatusEventResponse.encode("utf-8")
      return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

    except Exception as e:
      self._logger.error(f"Error handling event: {e}")
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
    if self._httpd is None:
      raise RuntimeError("Server not started")

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

        body = await anyio.to_thread.run_sync(_do_request)
        return_code, message = self._get_return_code_and_message(
          command, soap_decode(body.decode("utf-8"))
        )
        if return_code == 1:  # success
          return soap_decode(body.decode("utf-8"))
        elif return_code == 2:  # concurrent command
          event = anyio.Event()
          state = InhecoSiLAInterface._CommandState()
          self._pending = InhecoSiLAInterface._SiLACommand(
            name=command, request_id=request_id, event=event, state=state
          )
          await event.wait()
          if self._pending.state.error is not None:
            raise self._pending.state.error
          return self._pending.state.result
        else:
          raise RuntimeError(f"command {command} failed: {return_code} {message}")
      finally:
        self._pending = None

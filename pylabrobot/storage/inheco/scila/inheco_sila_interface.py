from __future__ import annotations

import asyncio
import http.server
import logging
import random
import socket
import socketserver
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from pylabrobot.storage.inheco.scila.soap import (
  XSI,
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


SOAP_RESPONSE_ErrorEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <ErrorEventResponse xmlns="http://sila.coop">
      <ErrorEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0.0005967S</duration>
        <deviceClass>0</deviceClass>
      </ErrorEventResult>
    </ErrorEventResponse>
  </s:Body>
</s:Envelope>"""


def _get_local_ip(machine_ip: str) -> str:
  from pylabrobot.io.sila.discovery import _get_link_local_interfaces

  # Link-local (169.254.x.x): the UDP routing trick picks the wrong interface
  # on multi-homed hosts. Enumerate local link-local addresses instead.
  if machine_ip.startswith("169.254."):
    interfaces = _get_link_local_interfaces()
    if interfaces:
      return interfaces[0]
    raise RuntimeError(f"No link-local interface found for device at {machine_ip}")

  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    s.connect((machine_ip, 1))
    local_ip: str = s.getsockname()[0]  # type: ignore
    if local_ip is None or local_ip.startswith("127."):
      raise RuntimeError("Could not determine local IP address.")
  finally:
    s.close()
  return local_ip


class SiLAState(str, Enum):
  """SiLA device states per specification."""

  STARTUP = "startup"
  STANDBY = "standby"
  INITIALIZING = "initializing"
  IDLE = "idle"
  BUSY = "busy"
  PAUSED = "paused"
  ERRORHANDLING = "errorHandling"
  INERROR = "inError"


class SiLAError(RuntimeError):
  def __init__(self, code: int, message: str, command: str, details: Optional[dict] = None):
    self.code = code
    self.message = message
    self.command = command
    self.details = details or {}
    super().__init__(f"Command {command} failed with code {code}: '{message}'")


class SiLATimeoutError(SiLAError):
  """Command timed out: lifetime_of_execution exceeded or ResponseEvent not received."""

  def __init__(self, message: str, command: str = ""):
    super().__init__(code=0, message=message, command=command)


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

    # pending commands by request_id (supports multiple in-flight)
    self._pending_by_id: Dict[int, InhecoSiLAInterface._SiLACommand] = {}

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
        except Exception:
          resp_body = SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

        self.send_response(200)
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

  def _complete_pending(
    self,
    request_id: int,
    result: Any = None,
    exception: Optional[BaseException] = None,
  ) -> None:
    """Pop pending command by request_id and resolve its future."""
    pending = self._pending_by_id.pop(request_id, None)
    if pending is None or pending.fut.done():
      return
    if exception is not None:
      pending.fut.set_exception(exception)
    else:
      pending.fut.set_result(result)

  async def _on_http(self, req: _HTTPRequest) -> bytes:
    """Dispatch incoming device events to handler methods."""
    try:
      decoded = soap_decode(req.body.decode("utf-8"))
      for event_type, handler, response in (
        ("ResponseEvent", self._on_response_event, SOAP_RESPONSE_ResponseEventResponse),
        ("StatusEvent", self._on_status_event, SOAP_RESPONSE_StatusEventResponse),
        ("DataEvent", self._on_data_event, SOAP_RESPONSE_DataEventResponse),
        ("ErrorEvent", self._on_error_event, SOAP_RESPONSE_ErrorEventResponse),
      ):
        if event_type in decoded:
          handler(decoded[event_type])
          return response.encode("utf-8")

      self._logger.warning("Unknown event type received")
      return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

    except Exception as e:
      self._logger.error(f"Error handling event: {e}\nRaw body: {req.body[:500]}")
      return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

  def _on_response_event(self, response_event: dict) -> None:
    request_id = response_event.get("requestId")
    if request_id is None:
      self._logger.warning("ResponseEvent missing requestId")
      return

    pending = self._pending_by_id.get(request_id)
    if pending is None:
      self._logger.warning(f"ResponseEvent for unknown requestId: {request_id}")
      return
    if pending.fut.done():
      self._logger.warning(f"ResponseEvent for already-completed requestId: {request_id}")
      return

    return_value = response_event.get("returnValue", {})
    return_code = return_value.get("returnCode")

    if return_code == 3:
      response_data = response_event.get("responseData", "")
      if response_data and response_data.strip():
        try:
          self._complete_pending(request_id, result=ET.fromstring(response_data))
        except ET.ParseError as e:
          self._logger.error(f"Failed to parse ResponseEvent responseData: {e}")
          self._complete_pending(
            request_id, exception=RuntimeError(f"Failed to parse response data: {e}")
          )
      else:
        self._complete_pending(request_id, result=None)
    else:
      message = return_value.get("message", "")
      err_msg = message.replace("\n", " ") if message else f"Unknown error (code {return_code})"
      self._complete_pending(
        request_id,
        exception=SiLAError(return_code, err_msg, pending.name),
      )

  def _on_status_event(self, status_event: dict) -> None:
    event_description = status_event.get("eventDescription", {})
    if isinstance(event_description, dict):
      device_state = event_description.get("DeviceState")
    elif isinstance(event_description, str) and "<DeviceState>" in event_description:
      root = ET.fromstring(event_description)
      device_state = root.text if root.tag == "DeviceState" else root.findtext("DeviceState")
    else:
      self._logger.warning(f"StatusEvent with unparsable eventDescription: {event_description!r}")
      return
    if device_state:
      self._logger.debug(f"StatusEvent device state: {device_state}")

  def _on_data_event(self, data_event: dict) -> None:
    """Override in subclasses to store/process DataEvents."""

  def _on_error_event(self, error_event: dict) -> None:
    req_id = error_event.get("requestId")
    return_value = error_event.get("returnValue", {})
    return_code = return_value.get("returnCode")
    message = return_value.get("message", "")

    self._logger.error(f"ErrorEvent for requestId {req_id}: code {return_code}, message: {message}")

    err_msg = message.replace("\n", " ") if message else f"Error (code {return_code})"
    if req_id is not None:
      pending = self._pending_by_id.get(req_id)
      if pending and not pending.fut.done():
        self._complete_pending(
          req_id, exception=RuntimeError(f"Command {pending.name} error: '{err_msg}'")
        )

  def _get_return_code_and_message(self, command_name: str, response: Any) -> Tuple[int, str]:
    resp_level = response.get(f"{command_name}Response", {})  # first level
    result_level = resp_level.get(f"{command_name}Result", {})  # second level
    return_code = result_level.get("returnCode")
    if return_code is None:
      raise ValueError(f"returnCode not found in response for {command_name}")
    return return_code, result_level.get("message", "")

  async def request_status(self) -> SiLAState:
    """Query the device for its current state via GetStatus."""
    decoded = await self.send_command("GetStatus")
    state_str = decoded.get("GetStatusResponse", {}).get("state", "")
    try:
      return SiLAState(state_str)
    except ValueError:
      for s in SiLAState:
        if s.value.lower() == state_str.lower():
          return s
      raise ValueError(f"Unknown device state: {state_str!r}")

  async def _handle_return_code(
    self, return_code: int, message: str, command_name: str, request_id: int
  ) -> None:
    """Handle SiLA return codes. Override _handle_device_return_code for device-specific codes (1000+)."""
    if return_code in (1, 2, 3):
      return
    if return_code == 4:
      raise SiLAError(4, "Device is busy", command_name)
    if return_code == 5:
      raise SiLAError(5, "LockId mismatch", command_name)
    if return_code == 6:
      raise SiLAError(6, "Invalid or duplicate requestId", command_name)
    if return_code == 9:
      try:
        state = await self.request_status()
      except Exception:
        state = None
      msg = f"{message} (state: {state.value})" if state else message
      if state == SiLAState.INERROR:
        msg += ". Device requires a power cycle to recover."
      raise SiLAError(9, msg, command_name)
    if return_code == 11:
      raise SiLAError(11, f"Invalid parameter: {message}", command_name)
    if return_code == 12:
      self._logger.warning(f"Command {command_name} finished with warning: {message}")
      return
    if return_code >= 1000:
      self._handle_device_return_code(return_code, message, command_name)
      return
    raise SiLAError(return_code, message, command_name)

  def _handle_device_return_code(self, return_code: int, message: str, command_name: str) -> None:
    """Handle device-specific return codes (1000+). Override in subclasses."""
    raise SiLAError(return_code, f"Device error: {message}", command_name)

  async def setup(self) -> None:
    await self.start()

  def _make_request_id(self):
    return random.randint(1, 2**31 - 1)

  @property
  def event_receiver_uri(self) -> str:
    return f"http://{self._client_ip}:{self.bound_port}/"

  async def _post_command(self, command: str, request_id: int, **kwargs: Any) -> Tuple[Any, int]:
    """POST a SOAP command to the device. Returns (decoded_response, return_code)."""
    cmd_xml = soap_encode(
      command,
      {"requestId": request_id, **kwargs},
      method_ns="http://sila.coop",
      extra_method_xmlns={"i": XSI},
    )

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

    def _do_request() -> bytes:
      with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read()  # type: ignore

    body = await asyncio.to_thread(_do_request)
    decoded = soap_decode(body.decode("utf-8"))
    return_code, message = self._get_return_code_and_message(command, decoded)
    await self._handle_return_code(return_code, message, command, request_id)
    return decoded, return_code

  async def send_command(
    self,
    command: str,
    **kwargs,
  ) -> Any:
    if self._closed:
      raise RuntimeError("Bridge is closed")

    request_id = self._make_request_id()
    decoded, return_code = await self._post_command(command, request_id, **kwargs)
    if return_code == 1:
      return decoded
    if return_code == 2:
      fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
      self._pending_by_id[request_id] = InhecoSiLAInterface._SiLACommand(
        name=command, request_id=request_id, fut=fut
      )
      return await fut
    raise RuntimeError(f"command {command} failed: {return_code}")

  async def start_command(
    self,
    command: str,
    **kwargs,
  ) -> Tuple[asyncio.Future[Any], int, float]:
    """Start an async command and return (future, request_id, started_at) without awaiting."""
    if self._closed:
      raise RuntimeError("Bridge is closed")

    request_id = self._make_request_id()
    decoded, return_code = await self._post_command(command, request_id, **kwargs)
    if return_code == 2:
      fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
      started_at = time.time()
      self._pending_by_id[request_id] = InhecoSiLAInterface._SiLACommand(
        name=command, request_id=request_id, fut=fut
      )
      return fut, request_id, started_at
    if return_code == 1:
      raise ValueError(
        "start_command is for async commands only; device returned sync response (return_code 1)"
      )
    raise RuntimeError(f"command {command} failed: {return_code}")

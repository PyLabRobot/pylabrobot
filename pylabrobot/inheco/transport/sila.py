"""Inheco SiLA 1 SOAP sessions with correlated asynchronous callbacks."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pylabrobot.inheco.soap import (
  _parse_duration_iso8601,
  required_text,
  soap_body_payload,
  soap_decode,
  soap_encode,
)
from pylabrobot.io.http import HTTP
from pylabrobot.io.http_server import HTTPServer, local_address_for

logger = logging.getLogger(__name__)


def response_data(root: ET.Element) -> ET.Element | None:
  """Decode an escaped asynchronous ResponseData document."""
  child = root.find("{*}responseData")
  if child is None or not child.text or not child.text.strip():
    return None
  return ET.fromstring(child.text)


class SiLAError(RuntimeError):
  """A device refusal, retaining its command and original return code."""

  def __init__(self, code: int, message: str, command: str) -> None:
    self.code = code
    self.message = message
    self.command = command
    super().__init__(f"{command}: {message} (SiLA code {code})")


@dataclass
class SiLACommand:
  """An accepted command whose completion remains available after its callback."""

  name: str
  request_id: int
  future: asyncio.Future[Any]
  duration: float | None = None

  async def wait(self, timeout: float | None = None) -> Any:
    """Wait without canceling the instrument command when the caller stops waiting."""
    if timeout is None:
      fallback = 10800.0 if self.name == "ExecuteMethod" else 60.0
      timeout = max(60.0, self.duration * 1.5) if self.duration else fallback
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be positive")
    return await asyncio.wait_for(asyncio.shield(self.future), timeout)


def _observe_completion(future: asyncio.Future[Any]) -> None:
  """Observe failures even if a waiter was canceled; future.result still raises them."""
  if not future.cancelled():
    future.exception()


class InhecoSiLAInterface:
  """One HTTP connection and callback endpoint, shared by Inheco devices.

  Only the short HTTP exchanges are serialized by HTTP. Multiple asynchronous commands
  may remain pending; the instrument decides which combinations it permits.
  """

  def __init__(
    self,
    machine_ip: str,
    client_ip: str | None = None,
    *,
    machine_port: int = 8080,
    callback_port: int = 0,
    on_data: Callable[[int, str], None] | None = None,
    on_status: Callable[[ET.Element], None] | None = None,
  ) -> None:
    self._machine_ip = machine_ip
    self.machine_port = machine_port
    self._client_ip = client_ip
    self._callback_port = callback_port
    self.io = HTTP("Inheco SiLA 1", f"http://{machine_ip}:{machine_port}")
    self._server: HTTPServer | None = None
    self._pending: dict[int, SiLACommand] = {}
    self._next_id = random.randint(1, 2**30)
    self._on_data = on_data
    self._on_status = on_status
    self._lock_id: str | None = None

  @property
  def machine_ip(self) -> str:
    """Return the instrument address."""
    return self._machine_ip

  @property
  def client_ip(self) -> str:
    """Return the selected callback interface after setup."""
    if self._client_ip is None:
      raise RuntimeError("SiLA session is not set up")
    return self._client_ip

  @property
  def bound_port(self) -> int:
    """Return the callback port after setup."""
    if self._server is None:
      raise RuntimeError("SiLA session is not set up")
    return self._server.bound_port

  @property
  def event_receiver_uri(self) -> str:
    """Return the address that the instrument must be able to reach."""
    return f"http://{self.client_ip}:{self.bound_port}/"

  def pending_command_name(self, request_id: int) -> str | None:
    """Identify a callback's active request without retaining an unbounded event history."""
    command = self._pending.get(request_id)
    return command.name if command is not None else None

  def fail_pending(self, error: Exception) -> None:
    """Fail commands when the device reports a session-wide fault."""
    for command in self._pending.values():
      if not command.future.done():
        command.future.set_exception(error)
    self._pending.clear()

  def _finish(self, request_id: int, event: ET.Element, *, error_event: bool = False) -> None:
    """Complete one registered command from its response or error event."""
    command = self._pending.get(request_id)
    if command is None or command.future.done():
      logger.debug("Ignoring late SiLA event for request %d", request_id)
      return
    value = event.find("{*}returnValue")
    if value is None:
      raise ValueError("SiLA event is missing returnValue")
    code = int(required_text(value, "returnCode"))
    message = required_text(value, "message", allow_empty=True)
    if error_event or code not in (1, 3, 12):
      command.future.set_exception(SiLAError(code, message, command.name))
    else:
      result = response_data(event)
      if code == 12:
        logger.warning("%s completed with warning: %s", command.name, message)
      command.future.set_result(result)
    self._pending.pop(request_id, None)

  async def _handle_event(self, body: bytes) -> bytes:
    """Dispatch a SOAP callback and acknowledge the exact event type."""
    event = soap_body_payload(body.decode("utf-8"))
    name = event.tag.rsplit("}", 1)[-1]
    if name not in {"ResponseEvent", "StatusEvent", "DataEvent", "ErrorEvent"}:
      raise ValueError(f"Unsupported SiLA event {name}")
    request_id: int | None = None
    try:
      if name == "StatusEvent":
        if self._on_status is not None:
          self._on_status(event)
      else:
        request_id = int(required_text(event, "requestId"))
        if name == "DataEvent":
          if self._on_data is not None:
            self._on_data(request_id, required_text(event, "dataValue"))
        else:
          self._finish(request_id, event, error_event=name == "ErrorEvent")
    except (ValueError, ET.ParseError) as error:
      logger.exception("Malformed %s", name)
      command = self._pending.pop(request_id, None) if request_id is not None else None
      if command is not None and not command.future.done():
        command.future.set_exception(error)
      raise
    return soap_encode(
      f"{name}Response",
      {
        f"{name}Result": {
          "returnCode": 1,
          "message": "Success",
          "duration": "PT0S",
          "deviceClass": 0,
        }
      },
      method_ns="http://sila.coop",
    ).encode()

  async def setup(self) -> None:
    """Open HTTP and callback transports without resetting the instrument."""
    if self._server is not None:
      return
    if self._client_ip is None:
      self._client_ip = await local_address_for(self.machine_ip, self.machine_port)
    await self.io.setup()
    server = HTTPServer(self._handle_event, self.client_ip, self._callback_port)
    try:
      await server.setup()
    except BaseException:
      await server.stop()
      await self.io.stop()
      raise
    self._server = server

  async def close(self) -> None:
    """Close transports and notify pending waiters of the lost connection."""
    self.fail_pending(ConnectionError("SiLA connection closed"))
    try:
      if self._server is not None:
        await self._server.stop()
    finally:
      self._server = None
      self._lock_id = None
      await self.io.stop()

  async def start_command(self, command: str, **kwargs: Any) -> SiLACommand:
    """Register before sending so an early callback cannot be lost."""
    if self._server is None:
      raise RuntimeError("SiLA session is not set up")
    self._next_id = self._next_id % (2**31 - 1) + 1
    request_id = self._next_id
    pending = SiLACommand(command, request_id, asyncio.get_running_loop().create_future())
    pending.future.add_done_callback(_observe_completion)
    pending.future.add_done_callback(lambda _: self._pending.pop(request_id, None))
    self._pending[request_id] = pending
    if self._lock_id is not None:
      kwargs.setdefault("lockId", self._lock_id)
    body = soap_encode(command, {"requestId": request_id, **kwargs}, method_ns="http://sila.coop")
    try:
      response = await self.io.request_raw(
        "POST",
        "/",
        body.encode(),
        headers={
          "Content-Type": "text/xml; charset=utf-8",
          "SOAPAction": f"http://sila.coop/{command}",
        },
        allow_redirects=False,
      )
      if response.status != 200:
        raise ConnectionError(f"{command}: HTTP {response.status}: {response.text()}")
      root = soap_body_payload(response.text())
      if root.tag.rsplit("}", 1)[-1] != f"{command}Response":
        raise ValueError(f"Unexpected response to {command}: {root.tag}")
      result = root.find(f"{{*}}{command}Result")
      if result is None:
        raise ValueError(f"Missing {command}Result")
      code = int(required_text(result, "returnCode"))
      message = required_text(result, "message", allow_empty=True)
      duration = result.find("{*}duration")
      if duration is not None and duration.text:
        parsed = _parse_duration_iso8601(duration.text)
        if parsed is None:
          raise ValueError(f"Invalid SiLA duration: {duration.text}")
        pending.duration = parsed.total_seconds()
      if code == 2:
        return pending
      if code not in (1, 3, 12):
        raise SiLAError(code, message, command)
      if code == 12:
        logger.warning("%s completed with warning: %s", command, message)
      if not pending.future.done():
        pending.future.set_result(soap_decode(response.text()))
      self._pending.pop(request_id, None)
      return pending
    except BaseException:
      self._pending.pop(request_id, None)
      if not pending.future.done():
        pending.future.cancel()
      raise

  async def send_command(self, command: str, *, timeout: float | None = None, **kwargs: Any) -> Any:
    """Send a command and await its synchronous response or completion callback."""
    pending = await self.start_command(command, **kwargs)
    return await pending.wait(timeout)


def _parse_scalar(text: str | None, tag: str) -> object:
  """Convert a typed SiLA Parameter value."""
  if text is None:
    raise ValueError(f"Missing {tag} parameter value")
  tag = tag.rsplit("}", 1)[-1].lower()
  if tag in ("float64", "float32", "double", "single"):
    return float(text)
  if tag in ("int32", "int64", "uint32", "uint64", "integer"):
    return int(text)
  if tag in ("boolean", "bool"):
    if text.lower() not in ("true", "false"):
      raise ValueError(f"Invalid boolean {text!r}")
    return text.lower() == "true"
  return text


def get_params(root: ET.Element, names: list[str]) -> dict[str, object]:
  """Read named parameters from a SiLA ResponseData document."""
  parameters = {p.attrib["name"]: p for p in root.findall(".//{*}Parameter")}
  result = {}
  for name in names:
    parameter = parameters[name]
    if len(parameter) != 1:
      raise ValueError(f"Parameter {name} must contain one typed value")
    result[name] = _parse_scalar(parameter[0].text, parameter[0].tag)
  return result

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

from pylabrobot.capabilities.plate_access import PlateAccess, PlateAccessBackend, PlateAccessState
from pylabrobot.device import Device, Driver, need_setup_finished

logger = logging.getLogger(__name__)

HTTP_HEADER_END = b"\r\n\r\n"
DEFAULT_SLOT_A = 15588
DEFAULT_SLOT_B = 8240
DEFAULT_RPC_PORT = 8000
DEFAULT_TIMEOUT = 10.0


class EchoError(Exception):
  """Base error for Echo interactions."""


class EchoProtocolError(EchoError):
  """Raised when the Echo returns malformed data."""


class EchoCommandError(EchoError):
  """Raised when the Echo rejects a command or required state is missing."""

  def __init__(self, method: str, status: Optional[str] = None):
    message = f"{method} failed"
    if status:
      message = f"{message}: {status}"
    super().__init__(message)
    self.method = method
    self.status = status


@dataclass
class EchoInstrumentInfo:
  """Identity information returned by ``GetInstrumentInfo``."""

  serial_number: str
  instrument_name: str
  ip_address: str
  software_version: str
  boot_time: str
  instrument_status: str
  model: str
  raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _HttpMessage:
  start_line: str
  headers: Dict[str, str]
  body: bytes

  def decoded_body(self) -> str:
    payload = self.body
    if payload[:2] == b"\x1f\x8b":
      payload = gzip.decompress(payload)
    return payload.decode("utf-8", errors="replace")


@dataclass
class _RpcResult:
  method: str
  values: Dict[str, Any]
  succeeded: Optional[bool]
  status: Optional[str]


def _local_name(tag: str) -> str:
  if "}" in tag:
    return tag.rsplit("}", 1)[1]
  return tag


def _parse_scalar(value: str) -> Any:
  normalized = value.strip()
  lowered = normalized.lower()
  if lowered == "true":
    return True
  if lowered == "false":
    return False
  try:
    return int(normalized)
  except ValueError:
    pass
  try:
    return float(normalized)
  except ValueError:
    return normalized


def _coerce_bool(value: Any) -> Optional[bool]:
  return value if isinstance(value, bool) else None


def _coerce_int(value: Any) -> Optional[int]:
  return value if isinstance(value, int) and not isinstance(value, bool) else None


class EchoDriver(Driver):
  """Driver for Labcyte Echo Medman access-control RPCs."""

  def __init__(
    self,
    host: str,
    rpc_port: int = DEFAULT_RPC_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    app_name: str = "PyLabRobot Echo",
    owner: Optional[str] = None,
    token: Optional[str] = None,
    token_slot_a: int = DEFAULT_SLOT_A,
    token_slot_b: int = DEFAULT_SLOT_B,
    client_version: str = "3.1.0",
    protocol_version: str = "3.1",
  ):
    super().__init__()
    self.host = host
    self.rpc_port = rpc_port
    self.timeout = timeout
    self.app_name = app_name
    self.owner = owner
    self._token = token
    self.token_slot_a = token_slot_a
    self.token_slot_b = token_slot_b
    self.client_version = client_version
    self.protocol_version = protocol_version
    self._rpc_lock = asyncio.Lock()
    self._lock_held = False

  @property
  def token(self) -> str:
    if self._token is None:
      raise RuntimeError("Echo driver is not set up; call setup() first")
    return self._token

  @staticmethod
  def build_token(
    instrument_host: str,
    slot_a: int = DEFAULT_SLOT_A,
    slot_b: int = DEFAULT_SLOT_B,
    epoch: Optional[int] = None,
    pid: Optional[int] = None,
  ) -> str:
    resolved_host = instrument_host
    try:
      resolved_host = socket.gethostbyname(instrument_host)
    except OSError:
      pass

    if epoch is None:
      epoch = int(time.time())
    if pid is None:
      pid = os.getpid()
    return f"{resolved_host}:{slot_a}:{slot_b}:{epoch}:{pid}"

  async def setup(self):
    if self._token is None:
      self._token = self.build_token(
        self.host,
        slot_a=self.token_slot_a,
        slot_b=self.token_slot_b,
      )

  async def stop(self):
    if self._lock_held:
      try:
        await self.unlock()
      except Exception as exc:  # pragma: no cover - best-effort cleanup
        logger.warning("Failed to unlock Echo during stop: %s", exc)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.host,
      "rpc_port": self.rpc_port,
      "timeout": self.timeout,
      "app_name": self.app_name,
      "owner": self.owner,
      "token": self._token,
      "token_slot_a": self.token_slot_a,
      "token_slot_b": self.token_slot_b,
      "client_version": self.client_version,
      "protocol_version": self.protocol_version,
    }

  async def get_instrument_info(self) -> EchoInstrumentInfo:
    result = await self._rpc("GetInstrumentInfo")
    self._ensure_success("GetInstrumentInfo", result)
    return EchoInstrumentInfo(
      serial_number=str(result.values.get("SerialNumber", "")),
      instrument_name=str(result.values.get("InstrumentName", "")),
      ip_address=str(result.values.get("IPAddress", "")),
      software_version=str(result.values.get("SoftwareVersion", "")),
      boot_time=str(result.values.get("BootTime", "")),
      instrument_status=str(result.values.get("InstrumentStatus", "")),
      model=str(result.values.get("Model", "")),
      raw=result.values,
    )

  async def get_access_state(self) -> PlateAccessState:
    result = await self._rpc("GetDIOEx2")
    raw = result.values
    return PlateAccessState(
      source_access_open=_coerce_bool(raw.get("LSO")),
      source_access_closed=_coerce_bool(raw.get("LSI")),
      door_open=_coerce_bool(raw.get("DFO")),
      door_closed=_coerce_bool(raw.get("DFC")),
      source_plate_position=_coerce_int(raw.get("SPP")),
      destination_plate_position=_coerce_int(raw.get("DPP")),
      raw=raw,
    )

  async def lock(self, app: Optional[str] = None, owner: Optional[str] = None) -> None:
    result = await self._rpc(
      "LockInstrument",
      (
        ("App", "string", app or self.app_name),
        ("Owner", "string", owner or self.owner or f"{self.host}\\PyLabRobot"),
        ("LockID", "string", self.token),
      ),
    )
    self._ensure_success("LockInstrument", result)
    self._lock_held = True

  async def unlock(self) -> None:
    if not self._lock_held:
      return

    result = await self._rpc(
      "UnlockInstrument",
      (("LockID", "string", self.token),),
    )
    self._ensure_success("UnlockInstrument", result)
    self._lock_held = False

  async def open_source_plate(self) -> None:
    self._require_lock("PresentSrcPlateGripper")
    result = await self._rpc("PresentSrcPlateGripper")
    self._ensure_success("PresentSrcPlateGripper", result)

  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    self._require_lock("RetractSrcPlateGripper")
    result = await self._rpc(
      "RetractSrcPlateGripper",
      self._make_retract_params(plate_type, barcode_location, barcode),
    )
    self._ensure_success("RetractSrcPlateGripper", result)

  async def open_destination_plate(self) -> None:
    self._require_lock("PresentDstPlateGripper")
    result = await self._rpc("PresentDstPlateGripper")
    self._ensure_success("PresentDstPlateGripper", result)

  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    self._require_lock("RetractDstPlateGripper")
    result = await self._rpc(
      "RetractDstPlateGripper",
      self._make_retract_params(plate_type, barcode_location, barcode),
    )
    self._ensure_success("RetractDstPlateGripper", result)

  async def close_door(self) -> None:
    self._require_lock("CloseDoor")
    result = await self._rpc("CloseDoor")
    self._ensure_success("CloseDoor", result)

  def _make_retract_params(
    self,
    plate_type: Optional[str],
    barcode_location: Optional[str],
    barcode: str,
  ) -> Tuple[Tuple[str, str, str], ...]:
    return (
      ("PlateType", "string", plate_type or "None"),
      ("BarCodeLocation", "string", barcode_location or "None"),
      ("BarCode", "string", barcode),
    )

  def _require_lock(self, method: str) -> None:
    if not self._lock_held:
      raise EchoCommandError(method, "An active lock is required for motion commands.")

  def _ensure_success(self, method: str, result: _RpcResult) -> None:
    if result.succeeded is False:
      raise EchoCommandError(method, result.status)

  async def _rpc(
    self,
    method: str,
    params: Iterable[Tuple[str, str, str]] = (),
  ) -> _RpcResult:
    envelope = self._make_soap_envelope(method, params)
    message = await self._send_request(
      port=self.rpc_port,
      host_header=self.token,
      body_text=envelope,
    )
    return self._parse_rpc_result(method, message)

  async def _send_request(self, port: int, host_header: str, body_text: str) -> _HttpMessage:
    body_bytes = gzip.compress(body_text.encode("utf-8"))
    request = (
      "POST /Medman HTTP/1.1\n"
      f"Host: {host_header}\n"
      f"Client: {self.client_version}\n"
      f"Protocol: {self.protocol_version}\n"
      'Content-Type: text/xml; charset="utf-8"\n'
      f"Content-Length: {len(body_bytes)}\n"
      'SOAPAction: "Some-URI"\r\n'
      "\r\n"
    ).encode("ascii") + body_bytes

    async with self._rpc_lock:
      reader, writer = await asyncio.open_connection(self.host, port)
      try:
        writer.write(request)
        await asyncio.wait_for(writer.drain(), timeout=self.timeout)
        return await self._read_http_message(reader)
      finally:
        writer.close()
        await writer.wait_closed()

  async def _read_http_message(self, reader: asyncio.StreamReader) -> _HttpMessage:
    data = bytearray()
    while HTTP_HEADER_END not in data:
      chunk = await asyncio.wait_for(reader.read(4096), timeout=self.timeout)
      if not chunk:
        raise EchoProtocolError("Connection closed before headers arrived.")
      data.extend(chunk)

    header_blob, rest = bytes(data).split(HTTP_HEADER_END, 1)
    header_lines = header_blob.decode("iso-8859-1").split("\r\n")
    start_line = header_lines[0]
    headers: Dict[str, str] = {}
    for line in header_lines[1:]:
      if not line or ":" not in line:
        continue
      key, value = line.split(":", 1)
      headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    body = await self._read_exact(reader, content_length, initial=rest)
    return _HttpMessage(start_line=start_line, headers=headers, body=body)

  async def _read_exact(
    self,
    reader: asyncio.StreamReader,
    want: int,
    initial: bytes = b"",
  ) -> bytes:
    data = bytearray(initial)
    while len(data) < want:
      chunk = await asyncio.wait_for(reader.read(want - len(data)), timeout=self.timeout)
      if not chunk:
        raise EchoProtocolError("Connection closed before full body arrived.")
      data.extend(chunk)
    return bytes(data)

  def _parse_rpc_result(self, method: str, message: _HttpMessage) -> _RpcResult:
    body_text = message.decoded_body()
    try:
      root = ET.fromstring(body_text)
    except ET.ParseError as exc:
      raise EchoProtocolError(f"Malformed XML in {method} response.") from exc

    payload = self._extract_payload_element(root)
    values: Dict[str, Any] = {}
    for child in payload:
      text = child.text or ""
      values[_local_name(child.tag)] = _parse_scalar(text) if text.strip() else ""

    succeeded_value = values.get("SUCCEEDED")
    return _RpcResult(
      method=method,
      values=values,
      succeeded=succeeded_value if isinstance(succeeded_value, bool) else None,
      status=str(values["Status"]) if "Status" in values else None,
    )

  def _extract_payload_element(self, root: ET.Element) -> ET.Element:
    body = next((node for node in root if _local_name(node.tag) == "Body"), None)
    if body is None or len(body) == 0:
      raise EchoProtocolError("SOAP body missing from response.")
    outer = body[0]
    if len(outer) == 0:
      return outer
    return outer[0]

  def _make_soap_envelope(
    self,
    method: str,
    params: Iterable[Tuple[str, str, str]],
  ) -> str:
    envelope = ET.Element(
      "SOAP-ENV:Envelope",
      {
        "SOAP-ENV:encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/",
        "xmlns:SOAPSDK1": "http://www.w3.org/2001/XMLSchema",
        "xmlns:SOAPSDK2": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:SOAPSDK3": "http://schemas.xmlsoap.org/soap/encoding/",
        "xmlns:SOAP-ENV": "http://schemas.xmlsoap.org/soap/envelope/",
      },
    )
    body = ET.SubElement(
      envelope,
      "SOAP-ENV:Body",
      {"SOAP-ENV:encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/"},
    )
    method_el = ET.SubElement(
      body,
      method,
      {"SOAP-ENV:encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/"},
    )
    for name, value_type, value in params:
      param = ET.SubElement(
        method_el,
        name,
        {
          "SOAP-ENV:encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/",
          "type": f"xsd:{value_type}",
        },
      )
      param.text = value

    return '<?xml version="1.0" encoding="UTF-8" standalone="no"?>' + ET.tostring(
      envelope,
      encoding="unicode",
      short_empty_elements=True,
    )


class EchoPlateAccessBackend(PlateAccessBackend):
  """Plate-access backend backed by the Echo Medman protocol."""

  def __init__(self, driver: EchoDriver):
    self.driver = driver

  async def lock(self, app: Optional[str] = None, owner: Optional[str] = None) -> None:
    await self.driver.lock(app=app, owner=owner)

  async def unlock(self) -> None:
    await self.driver.unlock()

  async def get_access_state(self) -> PlateAccessState:
    return await self.driver.get_access_state()

  async def open_source_plate(self) -> None:
    await self.driver.open_source_plate()

  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    await self.driver.close_source_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
    )

  async def open_destination_plate(self) -> None:
    await self.driver.open_destination_plate()

  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    await self.driver.close_destination_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
    )

  async def close_door(self) -> None:
    await self.driver.close_door()


class Echo(Device):
  """Labcyte Echo access-control device frontend."""

  def __init__(
    self,
    host: str,
    rpc_port: int = DEFAULT_RPC_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    app_name: str = "PyLabRobot Echo",
    owner: Optional[str] = None,
    token: Optional[str] = None,
  ):
    driver = EchoDriver(
      host=host,
      rpc_port=rpc_port,
      timeout=timeout,
      app_name=app_name,
      owner=owner,
      token=token,
    )
    super().__init__(driver=driver)
    self.driver: EchoDriver = driver
    self.plate_access = PlateAccess(backend=EchoPlateAccessBackend(driver))
    self._capabilities = [self.plate_access]

  @need_setup_finished
  async def get_instrument_info(self) -> EchoInstrumentInfo:
    """Return instrument identity and status information."""
    return await self.driver.get_instrument_info()

  @need_setup_finished
  async def get_access_state(self) -> PlateAccessState:
    """Return the current access state."""
    return await self.plate_access.get_access_state()

  @need_setup_finished
  async def lock(self, app: Optional[str] = None, owner: Optional[str] = None) -> None:
    """Lock the Echo for exclusive control."""
    await self.plate_access.lock(app=app, owner=owner)

  @need_setup_finished
  async def unlock(self) -> None:
    """Release the Echo lock held by this client."""
    await self.plate_access.unlock()

  @need_setup_finished
  async def open_source_plate(self) -> None:
    """Present the source-side access path."""
    await self.plate_access.open_source_plate()

  @need_setup_finished
  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    """Retract the source-side access path."""
    await self.plate_access.close_source_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
    )

  @need_setup_finished
  async def open_destination_plate(self) -> None:
    """Present the destination-side access path."""
    await self.plate_access.open_destination_plate()

  @need_setup_finished
  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
  ) -> None:
    """Retract the destination-side access path."""
    await self.plate_access.close_destination_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
    )

  @need_setup_finished
  async def close_door(self) -> None:
    """Close the Echo door."""
    await self.plate_access.close_door()

from __future__ import annotations

import asyncio
import enum
import gzip
import html
import logging
import os
import socket
import time
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_access import PlateAccess, PlateAccessBackend, PlateAccessState
from pylabrobot.device import Device, Driver, need_setup_finished
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import label_to_row_index, split_identifier

logger = logging.getLogger(__name__)

HTTP_HEADER_END = b"\r\n\r\n"
DEFAULT_SLOT_A = 15588
DEFAULT_SLOT_B = 8240
DEFAULT_RPC_PORT = 8000
DEFAULT_EVENT_PORT = 8010
DEFAULT_TIMEOUT = 10.0
DEFAULT_LOADED_RETRACT_TIMEOUT = 30.0
DEFAULT_SURVEY_TIMEOUT = 120.0
DEFAULT_DRY_TIMEOUT = 30.0


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


@dataclass(frozen=True)
class EchoPlateMap:
  """Echo plate-map payload built from canonical PLR well identifiers."""

  plate_type: str
  well_identifiers: Tuple[str, ...]

  @classmethod
  def from_plate(
    cls,
    plate: Plate,
    *,
    plate_type: str,
    wells: Optional[Sequence[str]] = None,
  ) -> "EchoPlateMap":
    if wells is None:
      identifiers = tuple(well.get_identifier() for well in plate.get_all_items())
    else:
      identifiers = tuple(plate.get_well(identifier).get_identifier() for identifier in wells)
    return cls(plate_type=plate_type, well_identifiers=identifiers)

  def to_xml(self) -> str:
    plate_map = ET.Element("PlateMap", {"p": self.plate_type})
    wells = ET.SubElement(plate_map, "Wells")
    for identifier in self.well_identifiers:
      row_label, column_label = split_identifier(identifier)
      ET.SubElement(
        wells,
        "Well",
        {
          "n": identifier,
          "r": str(label_to_row_index(row_label)),
          "c": str(int(column_label) - 1),
          "wc": "",
          "sid": "",
        },
      )
    return ET.tostring(plate_map, encoding="unicode", short_empty_elements=True)


class EchoDryPlateMode(enum.Enum):
  """Observed DryPlate modes."""

  TWO_PASS = "TWO_PASS"


@dataclass
class EchoSurveyParams(BackendParams):
  """Parameters for Echo ``PlateSurvey``."""

  plate_type: str
  num_rows: int
  num_cols: int
  start_row: int = 0
  start_col: int = 0
  save: bool = True
  check_source: bool = False
  timeout: Optional[float] = None


@dataclass
class EchoDryPlateParams(BackendParams):
  """Parameters for Echo ``DryPlate``."""

  mode: EchoDryPlateMode = EchoDryPlateMode.TWO_PASS
  timeout: Optional[float] = None


@dataclass
class EchoSurveyWell:
  """Single well entry parsed from Echo survey XML."""

  identifier: str
  row: int
  column: int
  raw_attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class EchoSurveyData:
  """Parsed survey dataset plus the original XML payload."""

  plate_type: Optional[str]
  wells: list[EchoSurveyWell]
  raw_xml: str

  @classmethod
  def from_xml(cls, xml_text: str) -> "EchoSurveyData":
    normalized_xml = html.unescape(xml_text).strip()
    try:
      root = ET.fromstring(normalized_xml)
    except ET.ParseError as exc:
      raise EchoProtocolError("Malformed survey XML.") from exc

    plate_type = (
      root.attrib.get("p")
      or root.attrib.get("PlateType")
      or root.attrib.get("plate_type")
      or root.attrib.get("plateType")
    )
    wells: list[EchoSurveyWell] = []
    seen: set[tuple[str, int, int]] = set()
    for element in root.iter():
      attributes = {str(key): str(value) for key, value in element.attrib.items()}
      identifier = attributes.get("n") or attributes.get("name")
      if identifier is None:
        continue

      row_value = attributes.get("r")
      column_value = attributes.get("c")
      row: Optional[int] = None
      column: Optional[int] = None
      if row_value is not None and column_value is not None:
        try:
          row = int(row_value)
          column = int(column_value)
        except ValueError:
          row = None
          column = None
      if row is None or column is None:
        try:
          row_label, column_label = split_identifier(identifier)
        except ValueError:
          continue
        row = label_to_row_index(row_label)
        column = int(column_label) - 1

      key = (identifier, row, column)
      if key in seen:
        continue
      seen.add(key)
      wells.append(
        EchoSurveyWell(
          identifier=identifier,
          row=row,
          column=column,
          raw_attributes=attributes,
        )
      )

    return cls(plate_type=plate_type, wells=wells, raw_xml=normalized_xml)


@dataclass
class EchoSurveyRunResult:
  """Combined result from the high-level survey helper."""

  response_data: Optional[EchoSurveyData] = None
  saved_data: Optional[EchoSurveyData] = None
  dry_mode: Optional[EchoDryPlateMode] = None


@dataclass
class EchoTransferPrintOptions(BackendParams):
  """Options passed to ``DoWellTransfer``."""

  do_plate_survey: bool = False
  monitor_power: bool = False
  homogeneous_plate: bool = False
  save_survey: bool = False
  save_print: bool = False
  source_plate_sensor: bool = False
  destination_plate_sensor: bool = False
  source_plate_sensor_override: bool = False
  destination_plate_sensor_override: bool = False
  plate_map: bool = False

  def to_params(self) -> Tuple[Tuple[str, str, str], ...]:
    return (
      ("DoPlateSurvey", "boolean", _format_bool(self.do_plate_survey)),
      ("MonitorPower", "boolean", _format_bool(self.monitor_power)),
      ("HomogeneousPlate", "boolean", _format_bool(self.homogeneous_plate)),
      ("SaveSurvey", "boolean", _format_bool(self.save_survey)),
      ("SavePrint", "boolean", _format_bool(self.save_print)),
      ("SrcPlateSensor", "boolean", _format_bool(self.source_plate_sensor)),
      ("DstPlateSensor", "boolean", _format_bool(self.destination_plate_sensor)),
      ("SrcPlateSensorOverride", "boolean", _format_bool(self.source_plate_sensor_override)),
      ("DstPlateSensorOverride", "boolean", _format_bool(self.destination_plate_sensor_override)),
      ("PlateMap", "boolean", _format_bool(self.plate_map)),
    )


@dataclass
class EchoTransferResult:
  """Result returned by ``DoWellTransfer``."""

  report_xml: Optional[str]
  raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EchoEvent:
  """Single callback event emitted on the Echo event channel."""

  event_id: Optional[str]
  source: str
  payload: str
  timestamp: Optional[str]
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


def _element_value(element: ET.Element) -> Any:
  if len(element) == 0:
    text = element.text or ""
    return _parse_scalar(text) if text.strip() else ""
  return "".join(
    ET.tostring(child, encoding="unicode", short_empty_elements=True) for child in element
  )


def _coerce_bool(value: Any) -> Optional[bool]:
  if isinstance(value, bool):
    return value
  if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
    return bool(value)
  return None


def _coerce_int(value: Any) -> Optional[int]:
  return value if isinstance(value, int) and not isinstance(value, bool) else None


def _infer_access_open(value: Any, position: Optional[int]) -> Optional[bool]:
  explicit = _coerce_bool(value)
  if explicit is not None:
    return explicit
  if position == -1:
    return True
  if position == 0:
    return False
  return None


def _infer_access_closed(value: Any, position: Optional[int]) -> Optional[bool]:
  explicit = _coerce_bool(value)
  if explicit is not None:
    return explicit
  if position == 0:
    return True
  if position == -1:
    return False
  return None


def _infer_door_open(
  value: Any,
  source_access_open: Optional[bool],
  destination_access_open: Optional[bool],
) -> Optional[bool]:
  explicit = _coerce_bool(value)
  if explicit is not None:
    return explicit
  if source_access_open is True or destination_access_open is True:
    return True
  if source_access_open is False and destination_access_open is False:
    return False
  return None


def _infer_door_closed(
  value: Any,
  source_access_closed: Optional[bool],
  destination_access_closed: Optional[bool],
) -> Optional[bool]:
  explicit = _coerce_bool(value)
  if explicit is not None:
    return explicit
  if source_access_closed is True and destination_access_closed is True:
    return True
  if source_access_closed is False or destination_access_closed is False:
    return False
  return None


def _resolve_timeout(timeout: Optional[float], default_timeout: float) -> float:
  return default_timeout if timeout is None else timeout


def _format_bool(value: bool) -> str:
  return "True" if value else "False"


def _param_type_and_value(value: Any) -> Tuple[str, str]:
  if isinstance(value, bool):
    return "boolean", _format_bool(value)
  if isinstance(value, int) and not isinstance(value, bool):
    return "int", str(value)
  if isinstance(value, float):
    return "double", str(value)
  return "string", str(value)


def _unlock_already_released(status: Optional[str]) -> bool:
  normalized = (status or "").strip().lower()
  return "does not own the lock" in normalized or "not locked" in normalized


def _survey_xml_from_values(values: Dict[str, Any]) -> Optional[str]:
  for value in values.values():
    if not isinstance(value, str):
      continue
    normalized = html.unescape(value).strip()
    if "<platesurvey" in normalized.lower():
      return normalized
  return None


def _is_probably_gzip(payload: bytes) -> bool:
  return len(payload) >= 2 and payload[:2] == b"\x1f\x8b"


def _gzip_stream_complete(payload: bytes) -> bool:
  if not _is_probably_gzip(payload):
    return True

  decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
  try:
    decompressor.decompress(payload)
  except zlib.error:
    return False
  return decompressor.eof


def _first_result_value(result: _RpcResult) -> Any:
  for key, value in result.values.items():
    if key in {"SUCCEEDED", "Status"}:
      continue
    return value
  return None


def _name_list_from_value(value: Any) -> list[str]:
  if isinstance(value, list):
    return [str(item).strip() for item in value if str(item).strip()]
  if not isinstance(value, str):
    return [] if value in (None, "") else [str(value)]

  normalized = html.unescape(value).strip()
  if not normalized:
    return []

  if normalized.startswith("<"):
    wrapped = normalized
    if not normalized.startswith("<root>"):
      wrapped = f"<root>{normalized}</root>"
    try:
      root = ET.fromstring(wrapped)
    except ET.ParseError:
      pass
    else:
      items: list[str] = []
      for element in root.iter():
        if element is root:
          continue
        text = (element.text or "").strip()
        if text:
          items.append(text)
          continue
        for attribute_name in ("name", "Name", "value", "Value"):
          attribute_value = element.attrib.get(attribute_name)
          if attribute_value:
            items.append(attribute_value.strip())
            break
      return [item for item in items if item]

  return [part.strip() for part in normalized.replace(";", ",").split(",") if part.strip()]


def _embedded_xml_from_values(values: Dict[str, Any], marker: str) -> Optional[str]:
  marker_lower = marker.lower()
  for value in values.values():
    if not isinstance(value, str):
      continue
    normalized = html.unescape(value).strip()
    if marker_lower in normalized.lower():
      return normalized
  return None


def _print_options_xml(options: EchoTransferPrintOptions) -> str:
  root = ET.Element("PrintOptions")
  for name, _value_type, value in options.to_params():
    child = ET.SubElement(root, name)
    child.text = value
  return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _parse_event_from_message(message: _HttpMessage) -> EchoEvent:
  body_text = message.decoded_body()
  try:
    root = ET.fromstring(body_text)
  except ET.ParseError as exc:
    raise EchoProtocolError("Malformed XML in Echo event payload.") from exc

  body = next((node for node in root if _local_name(node.tag) == "Body"), None)
  if body is None or len(body) == 0:
    raise EchoProtocolError("SOAP body missing from Echo event payload.")

  outer = body[0]
  if _local_name(outer.tag) != "handleEvent" or len(outer) == 0:
    raise EchoProtocolError("Unexpected Echo event payload.")

  event_element = outer[0]
  values = {_local_name(child.tag): _element_value(child) for child in event_element}
  return EchoEvent(
    event_id=str(values.get("id")) if values.get("id") not in (None, "") else None,
    source=str(values.get("source", "")),
    payload=str(values.get("payload", "")),
    timestamp=str(values.get("timestamp")) if values.get("timestamp") not in (None, "") else None,
    raw=values,
  )


class EchoEventStream:
  """Persistent 8010 callback stream."""

  def __init__(
    self,
    driver: "EchoDriver",
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
  ):
    self._driver = driver
    self._reader = reader
    self._writer = writer
    self._closed = False

  async def __aenter__(self) -> "EchoEventStream":
    return self

  async def __aexit__(self, exc_type, exc, tb) -> None:
    await self.close()

  async def read_event(self, timeout: Optional[float] = None) -> EchoEvent:
    message = await self._driver._read_http_message(self._reader, timeout=timeout)
    return _parse_event_from_message(message)

  async def read_events(
    self,
    *,
    max_events: int,
    timeout: Optional[float] = None,
  ) -> list[EchoEvent]:
    events: list[EchoEvent] = []
    for _ in range(max_events):
      events.append(await self.read_event(timeout=timeout))
    return events

  async def close(self) -> None:
    if self._closed:
      return
    self._closed = True
    self._writer.close()
    await self._writer.wait_closed()


class EchoDriver(Driver):
  """Driver for Labcyte Echo Medman access-control RPCs."""

  def __init__(
    self,
    host: str,
    rpc_port: int = DEFAULT_RPC_PORT,
    event_port: int = DEFAULT_EVENT_PORT,
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
    self.event_port = event_port
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

  async def open_event_stream(self, timeout: Optional[float] = None) -> EchoEventStream:
    request_timeout = _resolve_timeout(timeout, self.timeout)
    reader, writer = await asyncio.wait_for(
      asyncio.open_connection(self.host, self.event_port),
      timeout=request_timeout,
    )
    try:
      writer.write(self._make_event_registration_request())
      await asyncio.wait_for(writer.drain(), timeout=request_timeout)
    except Exception:
      writer.close()
      await writer.wait_closed()
      raise
    return EchoEventStream(self, reader, writer)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.host,
      "rpc_port": self.rpc_port,
      "event_port": self.event_port,
      "timeout": self.timeout,
      "app_name": self.app_name,
      "owner": self.owner,
      "token": self._token,
      "token_slot_a": self.token_slot_a,
      "token_slot_b": self.token_slot_b,
      "client_version": self.client_version,
      "protocol_version": self.protocol_version,
    }

  async def read_events(
    self,
    *,
    max_events: int,
    timeout: Optional[float] = None,
  ) -> list[EchoEvent]:
    async with await self.open_event_stream(timeout=timeout) as stream:
      return await stream.read_events(max_events=max_events, timeout=timeout)

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

  async def get_dio(self) -> Dict[str, Any]:
    result = await self._rpc("GetDIO")
    self._ensure_success("GetDIO", result)
    return result.values

  async def get_dio_ex2(self) -> Dict[str, Any]:
    result = await self._rpc("GetDIOEx2")
    self._ensure_success("GetDIOEx2", result)
    return result.values

  async def get_access_state(self) -> PlateAccessState:
    raw = await self.get_dio_ex2()
    source_plate_position = _coerce_int(raw.get("SPP"))
    destination_plate_position = _coerce_int(raw.get("DPP"))
    source_access_open = _infer_access_open(raw.get("LSO"), source_plate_position)
    source_access_closed = _infer_access_closed(raw.get("LSI"), source_plate_position)
    destination_access_open = _infer_access_open(None, destination_plate_position)
    destination_access_closed = _infer_access_closed(None, destination_plate_position)
    return PlateAccessState(
      source_access_open=source_access_open,
      source_access_closed=source_access_closed,
      destination_access_open=destination_access_open,
      destination_access_closed=destination_access_closed,
      door_open=_infer_door_open(raw.get("DFO"), source_access_open, destination_access_open),
      door_closed=_infer_door_closed(
        raw.get("DFC"),
        source_access_closed,
        destination_access_closed,
      ),
      source_plate_position=source_plate_position,
      destination_plate_position=destination_plate_position,
      raw=raw,
    )

  async def get_current_source_plate_type(self) -> Optional[str]:
    result = await self._rpc("GetCurrentSrcPlateType")
    self._ensure_success("GetCurrentSrcPlateType", result)
    value = _first_result_value(result)
    return None if value in (None, "") else str(value)

  async def get_current_destination_plate_type(self) -> Optional[str]:
    result = await self._rpc("GetCurrentDstPlateType")
    self._ensure_success("GetCurrentDstPlateType", result)
    value = _first_result_value(result)
    return None if value in (None, "") else str(value)

  async def get_destination_plate_offset(self) -> Any:
    result = await self._rpc("GetDstPlateOffset")
    self._ensure_success("GetDstPlateOffset", result)
    return _first_result_value(result)

  async def get_all_source_plate_names(self) -> list[str]:
    result = await self._rpc("GetAllSrcPlateNames")
    self._ensure_success("GetAllSrcPlateNames", result)
    return _name_list_from_value(_first_result_value(result))

  async def get_all_destination_plate_names(self) -> list[str]:
    result = await self._rpc("GetAllDestPlateNames")
    self._ensure_success("GetAllDestPlateNames", result)
    return _name_list_from_value(_first_result_value(result))

  async def get_plate_info(self, plate_type_ex: str) -> Dict[str, Any]:
    result = await self._rpc(
      "GetPlateInfoEx",
      (("PlateTypeEx", "string", plate_type_ex),),
    )
    self._ensure_success("GetPlateInfoEx", result)
    return result.values

  async def get_plate_insert(self, plate_type: str) -> Any:
    result = await self._rpc(
      "GetPlateInsert",
      (("PlateType", "string", plate_type),),
    )
    self._ensure_success("GetPlateInsert", result)
    return _first_result_value(result)

  async def get_current_plate_insert(self) -> Any:
    result = await self._rpc("GetCurrentPlateInsert")
    self._ensure_success("GetCurrentPlateInsert", result)
    return _first_result_value(result)

  async def get_instrument_lock_state(self, lock_id: Optional[str] = None) -> Dict[str, Any]:
    params: Tuple[Tuple[str, str, str], ...] = ()
    if lock_id is not None:
      params = (("LockID", "string", lock_id),)
    result = await self._rpc("GetInstrumentLockState", params)
    # This call sometimes reports not-locked through Status while still being operationally fine.
    return result.values

  async def is_storage_mode(self) -> bool:
    result = await self._rpc("IsStorageMode")
    self._ensure_success("IsStorageMode", result)
    return bool(_first_result_value(result))

  async def has_security_key(self, security_key: str) -> bool:
    result = await self._rpc(
      "HasSecurityKey",
      (("HasSecurityKeyStg", "string", security_key),),
    )
    self._ensure_success("HasSecurityKey", result)
    return bool(_first_result_value(result))

  async def retrieve_parameter(self, param: str) -> Any:
    result = await self._rpc(
      "RetrieveParameter",
      (("Param", "string", param),),
    )
    self._ensure_success("RetrieveParameter", result)
    return _first_result_value(result)

  async def store_parameter(self, param: str, value: Any) -> None:
    value_type, value_text = _param_type_and_value(value)
    result = await self._rpc(
      "StoreParameter",
      (
        ("Param", "string", param),
        ("Value", value_type, value_text),
      ),
    )
    self._ensure_success("StoreParameter", result)

  async def get_transfer_volume_max_nl(self, plate_type: str) -> Any:
    result = await self._rpc(
      "GetTransferVolMaximumNl",
      (("Value", "string", plate_type),),
    )
    self._ensure_success("GetTransferVolMaximumNl", result)
    return _first_result_value(result)

  async def get_transfer_volume_min_nl(self, plate_type: str) -> Any:
    result = await self._rpc(
      "GetTransferVolMinimumNl",
      (("Value", "string", plate_type),),
    )
    self._ensure_success("GetTransferVolMinimumNl", result)
    return _first_result_value(result)

  async def get_transfer_volume_increment_nl(self, plate_type: str) -> Any:
    result = await self._rpc(
      "GetTransferVolIncrNl",
      (("Value", "string", plate_type),),
    )
    self._ensure_success("GetTransferVolIncrNl", result)
    return _first_result_value(result)

  async def check_source_plate_insert_compatibility(self, plate_type: str) -> Dict[str, Any]:
    result = await self._rpc(
      "CheckSrcPlateInsertCompatibility",
      (("PlateType", "string", plate_type),),
    )
    self._ensure_success("CheckSrcPlateInsertCompatibility", result)
    return result.values

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

  async def begin_session(self) -> Any:
    self._require_lock("BeginSession")
    result = await self._rpc("BeginSession")
    self._ensure_success("BeginSession", result)
    return _first_result_value(result)

  async def end_session(self) -> Any:
    self._require_lock("EndSession")
    result = await self._rpc("EndSession")
    self._ensure_success("EndSession", result)
    return _first_result_value(result)

  async def unlock(self) -> None:
    if not self._lock_held:
      return

    result = await self._rpc(
      "UnlockInstrument",
      (("LockID", "string", self.token),),
    )
    if result.succeeded is False and _unlock_already_released(result.status):
      logger.warning("UnlockInstrument reported stale local lock state: %s", result.status)
      self._lock_held = False
      return
    self._ensure_success("UnlockInstrument", result)
    self._lock_held = False

  async def open_source_plate(self, timeout: Optional[float] = None) -> None:
    self._require_lock("PresentSrcPlateGripper")
    result = await self._rpc("PresentSrcPlateGripper", timeout=timeout)
    self._ensure_success("PresentSrcPlateGripper", result)

  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> None:
    self._require_lock("RetractSrcPlateGripper")
    result = await self._rpc(
      "RetractSrcPlateGripper",
      self._make_retract_params(plate_type, barcode_location, barcode),
      timeout=_resolve_timeout(
        timeout,
        DEFAULT_LOADED_RETRACT_TIMEOUT if plate_type is not None else self.timeout,
      ),
    )
    self._ensure_success("RetractSrcPlateGripper", result)

  async def open_destination_plate(self, timeout: Optional[float] = None) -> None:
    self._require_lock("PresentDstPlateGripper")
    result = await self._rpc("PresentDstPlateGripper", timeout=timeout)
    self._ensure_success("PresentDstPlateGripper", result)

  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> None:
    self._require_lock("RetractDstPlateGripper")
    result = await self._rpc(
      "RetractDstPlateGripper",
      self._make_retract_params(plate_type, barcode_location, barcode),
      timeout=_resolve_timeout(
        timeout,
        DEFAULT_LOADED_RETRACT_TIMEOUT if plate_type is not None else self.timeout,
      ),
    )
    self._ensure_success("RetractDstPlateGripper", result)

  async def close_door(self, timeout: Optional[float] = None) -> None:
    self._require_lock("CloseDoor")
    result = await self._rpc("CloseDoor", timeout=timeout)
    self._ensure_success("CloseDoor", result)

  async def set_barcodes_check(self, enabled: bool) -> None:
    self._require_lock("SetBarcodesCheck")
    result = await self._rpc(
      "SetBarcodesCheck",
      (("DoBarcodesCheck", "boolean", _format_bool(enabled)),),
    )
    self._ensure_success("SetBarcodesCheck", result)

  async def set_plate_map(self, plate_map: EchoPlateMap) -> None:
    self._require_lock("SetPlateMap")
    result = await self._rpc(
      "SetPlateMap",
      (("xmlPlateMap", "string", plate_map.to_xml()),),
    )
    self._ensure_success("SetPlateMap", result)

  async def set_survey_data(self, survey_xml: str) -> None:
    self._require_lock("SetSurveyData")
    result = await self._rpc(
      "SetSurveyData",
      (("PlateSurveyData", "string", survey_xml),),
    )
    self._ensure_success("SetSurveyData", result)

  async def survey_plate(self, params: EchoSurveyParams) -> Optional[EchoSurveyData]:
    self._require_lock("PlateSurvey")
    result = await self._rpc(
      "PlateSurvey",
      (
        ("PlateType", "string", params.plate_type),
        ("StartRow", "int", str(params.start_row)),
        ("StartCol", "int", str(params.start_col)),
        ("NumRows", "int", str(params.num_rows)),
        ("NumCols", "int", str(params.num_cols)),
        ("Save", "boolean", "True" if params.save else "False"),
        ("CheckSrc", "boolean", "True" if params.check_source else "False"),
      ),
      timeout=_resolve_timeout(params.timeout, DEFAULT_SURVEY_TIMEOUT),
    )
    self._ensure_success("PlateSurvey", result)
    survey_xml = _survey_xml_from_values(result.values)
    return EchoSurveyData.from_xml(survey_xml) if survey_xml is not None else None

  async def get_survey_data(self) -> EchoSurveyData:
    result = await self._rpc("GetSurveyData")
    self._ensure_success("GetSurveyData", result)
    survey_xml = _survey_xml_from_values(result.values)
    if survey_xml is None:
      raise EchoProtocolError("Survey XML missing from GetSurveyData response.")
    return EchoSurveyData.from_xml(survey_xml)

  async def dry_plate(self, params: Optional[EchoDryPlateParams] = None) -> None:
    self._require_lock("DryPlate")
    params = params or EchoDryPlateParams()
    result = await self._rpc(
      "DryPlate",
      (("Type", "string", params.mode.value),),
      timeout=_resolve_timeout(params.timeout, DEFAULT_DRY_TIMEOUT),
    )
    self._ensure_success("DryPlate", result)

  async def survey_source_plate(
    self,
    plate_map: EchoPlateMap,
    survey: EchoSurveyParams,
    *,
    fetch_saved_data: bool = True,
    dry_after: bool = False,
    dry: Optional[EchoDryPlateParams] = None,
  ) -> EchoSurveyRunResult:
    await self.set_plate_map(plate_map)
    response_data = await self.survey_plate(survey)
    saved_data = None
    if fetch_saved_data and survey.save:
      saved_data = await self.get_survey_data()
    dry_mode = None
    if dry_after:
      dry = dry or EchoDryPlateParams()
      await self.dry_plate(dry)
      dry_mode = dry.mode
    return EchoSurveyRunResult(
      response_data=response_data,
      saved_data=saved_data,
      dry_mode=dry_mode,
    )

  async def do_well_transfer(
    self,
    protocol_xml: str,
    print_options: Optional[EchoTransferPrintOptions] = None,
    timeout: Optional[float] = None,
  ) -> EchoTransferResult:
    self._require_lock("DoWellTransfer")
    options = print_options or EchoTransferPrintOptions()
    result = await self._rpc(
      "DoWellTransfer",
      (
        ("ProtocolName", "string", protocol_xml),
        ("PrintOptions", "xml_element", _print_options_xml(options)),
      ),
      timeout=timeout,
    )
    self._ensure_success("DoWellTransfer", result)
    return EchoTransferResult(
      report_xml=_embedded_xml_from_values(result.values, "<transfer"),
      raw=result.values,
    )

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
    timeout: Optional[float] = None,
  ) -> _RpcResult:
    envelope = self._make_soap_envelope(method, params)
    message = await self._send_request(
      port=self.rpc_port,
      host_header=self.token,
      body_text=envelope,
      timeout=timeout,
    )
    return self._parse_rpc_result(method, message)

  async def _send_request(
    self,
    port: int,
    host_header: str,
    body_text: str,
    timeout: Optional[float] = None,
  ) -> _HttpMessage:
    request_timeout = _resolve_timeout(timeout, self.timeout)
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
      reader, writer = await asyncio.wait_for(
        asyncio.open_connection(self.host, port),
        timeout=request_timeout,
      )
      try:
        writer.write(request)
        await asyncio.wait_for(writer.drain(), timeout=request_timeout)
        return await self._read_http_message(reader, timeout=request_timeout)
      finally:
        writer.close()
        await writer.wait_closed()

  def _make_event_registration_request(self) -> bytes:
    body_bytes = gzip.compress(f"add{self.token}".encode("utf-8"))
    return (
      "POST /Medman HTTP/1.1\n"
      f"Host: {self.token}\n"
      f"Client: {self.client_version}\n"
      f"Protocol: {self.protocol_version}\n"
      'Content-Type: text/xml; charset="utf-8"\n'
      f"Content-Length: {len(body_bytes)}\n"
      'SOAPAction: "Some-URI"\r\n'
      "\r\n"
    ).encode("ascii") + body_bytes

  async def _read_http_message(
    self,
    reader: asyncio.StreamReader,
    timeout: Optional[float] = None,
  ) -> _HttpMessage:
    read_timeout = _resolve_timeout(timeout, self.timeout)
    data = bytearray()
    while HTTP_HEADER_END not in data:
      chunk = await asyncio.wait_for(reader.read(4096), timeout=read_timeout)
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
    body = await self._read_exact(
      reader,
      content_length,
      initial=rest,
      timeout=read_timeout,
    )
    if content_length > 0 and _is_probably_gzip(body) and not _gzip_stream_complete(body):
      body = await self._read_until_complete_gzip_body(
        reader,
        initial=body,
        advertised_length=content_length,
        timeout=read_timeout,
      )
    return _HttpMessage(start_line=start_line, headers=headers, body=body)

  async def _read_exact(
    self,
    reader: asyncio.StreamReader,
    want: int,
    initial: bytes = b"",
    timeout: Optional[float] = None,
  ) -> bytes:
    read_timeout = _resolve_timeout(timeout, self.timeout)
    data = bytearray(initial)
    while len(data) < want:
      chunk = await asyncio.wait_for(reader.read(want - len(data)), timeout=read_timeout)
      if not chunk:
        raise EchoProtocolError("Connection closed before full body arrived.")
      data.extend(chunk)
    return bytes(data)

  async def _read_until_complete_gzip_body(
    self,
    reader: asyncio.StreamReader,
    *,
    initial: bytes,
    advertised_length: int,
    timeout: Optional[float] = None,
  ) -> bytes:
    read_timeout = _resolve_timeout(timeout, self.timeout)
    data = bytearray(initial)
    while not _gzip_stream_complete(bytes(data)):
      chunk = await asyncio.wait_for(reader.read(4096), timeout=read_timeout)
      if not chunk:
        raise EchoProtocolError(
          "Connection closed before complete gzip body arrived "
          f"(advertised {advertised_length} bytes, received {len(data)} bytes)."
        )
      data.extend(chunk)

    if len(data) != advertised_length:
      logger.warning(
        "Echo response gzip body exceeded advertised Content-Length: header=%s actual=%s",
        advertised_length,
        len(data),
      )
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
      values[_local_name(child.tag)] = _element_value(child)

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
      if value_type == "xml_element":
        try:
          param = ET.fromstring(value)
        except ET.ParseError as exc:
          raise EchoProtocolError(f"Invalid XML payload for {method}.{name}.") from exc
        method_el.append(param)
        continue
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

  async def open_source_plate(self, timeout: Optional[float] = None) -> None:
    await self.driver.open_source_plate(timeout=timeout)

  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> None:
    await self.driver.close_source_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=timeout,
    )

  async def open_destination_plate(self, timeout: Optional[float] = None) -> None:
    await self.driver.open_destination_plate(timeout=timeout)

  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> None:
    await self.driver.close_destination_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=timeout,
    )

  async def close_door(self, timeout: Optional[float] = None) -> None:
    state = await self.driver.get_access_state()
    if state.active_access_paths:
      active_paths = ", ".join(state.active_access_paths)
      raise EchoCommandError(
        "CloseDoor",
        f"Cannot close the door while {active_paths} access is still open.",
      )
    await self.driver.close_door(timeout=timeout)


class Echo(Device):
  """Labcyte Echo access-control device frontend."""

  def __init__(
    self,
    host: str,
    rpc_port: int = DEFAULT_RPC_PORT,
    event_port: int = DEFAULT_EVENT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    app_name: str = "PyLabRobot Echo",
    owner: Optional[str] = None,
    token: Optional[str] = None,
  ):
    driver = EchoDriver(
      host=host,
      rpc_port=rpc_port,
      event_port=event_port,
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
  async def get_dio(self) -> Dict[str, Any]:
    """Return the raw ``GetDIO`` status payload."""
    return await self.driver.get_dio()

  @need_setup_finished
  async def get_dio_ex2(self) -> Dict[str, Any]:
    """Return the raw ``GetDIOEx2`` status payload."""
    return await self.driver.get_dio_ex2()

  @need_setup_finished
  async def open_event_stream(self, timeout: Optional[float] = None) -> EchoEventStream:
    """Open a persistent Medman event stream on port 8010."""
    return await self.driver.open_event_stream(timeout=timeout)

  @need_setup_finished
  async def read_events(
    self,
    *,
    max_events: int,
    timeout: Optional[float] = None,
  ) -> list[EchoEvent]:
    """Open an event stream, read a fixed number of events, then close it."""
    return await self.driver.read_events(max_events=max_events, timeout=timeout)

  @need_setup_finished
  async def get_access_state(self) -> PlateAccessState:
    """Return the current access state."""
    return await self.plate_access.get_access_state()

  @need_setup_finished
  async def get_current_source_plate_type(self) -> Optional[str]:
    """Return the Echo-reported current source plate type."""
    return await self.driver.get_current_source_plate_type()

  @need_setup_finished
  async def get_current_destination_plate_type(self) -> Optional[str]:
    """Return the Echo-reported current destination plate type."""
    return await self.driver.get_current_destination_plate_type()

  @need_setup_finished
  async def get_destination_plate_offset(self) -> Any:
    """Return the raw destination plate offset value."""
    return await self.driver.get_destination_plate_offset()

  @need_setup_finished
  async def get_all_source_plate_names(self) -> list[str]:
    """Return the Echo catalog of source plate names."""
    return await self.driver.get_all_source_plate_names()

  @need_setup_finished
  async def get_all_destination_plate_names(self) -> list[str]:
    """Return the Echo catalog of destination plate names."""
    return await self.driver.get_all_destination_plate_names()

  @need_setup_finished
  async def get_plate_info(self, plate_type_ex: str) -> Dict[str, Any]:
    """Return the raw ``GetPlateInfoEx`` payload."""
    return await self.driver.get_plate_info(plate_type_ex)

  @need_setup_finished
  async def get_plate_insert(self, plate_type: str) -> Any:
    """Return the plate-insert information for the given plate type."""
    return await self.driver.get_plate_insert(plate_type)

  @need_setup_finished
  async def get_current_plate_insert(self) -> Any:
    """Return the current plate-insert selection."""
    return await self.driver.get_current_plate_insert()

  @need_setup_finished
  async def get_instrument_lock_state(self, lock_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the raw ``GetInstrumentLockState`` payload."""
    return await self.driver.get_instrument_lock_state(lock_id=lock_id)

  @need_setup_finished
  async def is_storage_mode(self) -> bool:
    """Return whether the Echo reports being in storage mode."""
    return await self.driver.is_storage_mode()

  @need_setup_finished
  async def has_security_key(self, security_key: str) -> bool:
    """Return whether the requested security key is present."""
    return await self.driver.has_security_key(security_key)

  @need_setup_finished
  async def retrieve_parameter(self, param: str) -> Any:
    """Retrieve a named Echo parameter."""
    return await self.driver.retrieve_parameter(param)

  @need_setup_finished
  async def store_parameter(self, param: str, value: Any) -> None:
    """Store a named Echo parameter."""
    await self.driver.store_parameter(param, value)

  @need_setup_finished
  async def get_transfer_volume_max_nl(self, plate_type: str) -> Any:
    """Return the maximum transfer volume, in nL, for the given plate type."""
    return await self.driver.get_transfer_volume_max_nl(plate_type)

  @need_setup_finished
  async def get_transfer_volume_min_nl(self, plate_type: str) -> Any:
    """Return the minimum transfer volume, in nL, for the given plate type."""
    return await self.driver.get_transfer_volume_min_nl(plate_type)

  @need_setup_finished
  async def get_transfer_volume_increment_nl(self, plate_type: str) -> Any:
    """Return the transfer increment, in nL, for the given plate type."""
    return await self.driver.get_transfer_volume_increment_nl(plate_type)

  @need_setup_finished
  async def check_source_plate_insert_compatibility(self, plate_type: str) -> Dict[str, Any]:
    """Return the raw source plate insert compatibility result."""
    return await self.driver.check_source_plate_insert_compatibility(plate_type)

  @need_setup_finished
  async def lock(self, app: Optional[str] = None, owner: Optional[str] = None) -> None:
    """Lock the Echo for exclusive control."""
    await self.plate_access.lock(app=app, owner=owner)

  @need_setup_finished
  async def begin_session(self) -> Any:
    """Begin an Echo session and return the session identifier when present."""
    return await self.driver.begin_session()

  @need_setup_finished
  async def end_session(self) -> Any:
    """End the current Echo session."""
    return await self.driver.end_session()

  @need_setup_finished
  async def unlock(self) -> None:
    """Release the Echo lock held by this client."""
    await self.plate_access.unlock()

  @need_setup_finished
  async def open_source_plate(
    self,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Present the source-side access path and return the final access state."""
    return await self.plate_access.open_source_plate(timeout=timeout, poll_interval=poll_interval)

  @need_setup_finished
  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Retract the source-side access path and return the final access state."""
    return await self.plate_access.close_source_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=timeout,
      poll_interval=poll_interval,
    )

  @need_setup_finished
  async def open_destination_plate(
    self,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Present the destination-side access path and return the final access state."""
    return await self.plate_access.open_destination_plate(
      timeout=timeout,
      poll_interval=poll_interval,
    )

  @need_setup_finished
  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Retract the destination-side access path and return the final access state."""
    return await self.plate_access.close_destination_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=timeout,
      poll_interval=poll_interval,
    )

  @need_setup_finished
  async def close_door(
    self,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
  ) -> PlateAccessState:
    """Close the Echo door and return the final access state."""
    return await self.plate_access.close_door(timeout=timeout, poll_interval=poll_interval)

  @need_setup_finished
  async def set_plate_map(self, plate_map: EchoPlateMap) -> None:
    """Upload an Echo source plate map."""
    await self.driver.set_plate_map(plate_map)

  @need_setup_finished
  async def set_barcodes_check(self, enabled: bool) -> None:
    """Enable or disable barcode checking."""
    await self.driver.set_barcodes_check(enabled)

  @need_setup_finished
  async def set_survey_data(self, survey_xml: str) -> None:
    """Upload survey XML via ``SetSurveyData``."""
    await self.driver.set_survey_data(survey_xml)

  @need_setup_finished
  async def survey_plate(self, params: EchoSurveyParams) -> Optional[EchoSurveyData]:
    """Run ``PlateSurvey`` and return any survey XML included in the response."""
    return await self.driver.survey_plate(params)

  @need_setup_finished
  async def get_survey_data(self) -> EchoSurveyData:
    """Return the last saved Echo survey dataset."""
    return await self.driver.get_survey_data()

  @need_setup_finished
  async def dry_plate(self, params: Optional[EchoDryPlateParams] = None) -> None:
    """Run ``DryPlate`` with the requested mode."""
    await self.driver.dry_plate(params)

  @need_setup_finished
  async def survey_source_plate(
    self,
    plate_map: EchoPlateMap,
    survey: EchoSurveyParams,
    *,
    fetch_saved_data: bool = True,
    dry_after: bool = False,
    dry: Optional[EchoDryPlateParams] = None,
  ) -> EchoSurveyRunResult:
    """Run the Echo source-plate survey workflow without changing access state."""
    await self.set_plate_map(plate_map)
    response_data = await self.survey_plate(survey)
    saved_data = None
    if fetch_saved_data and survey.save:
      saved_data = await self.get_survey_data()
    dry_mode = None
    if dry_after:
      dry = dry or EchoDryPlateParams()
      await self.dry_plate(dry)
      dry_mode = dry.mode
    return EchoSurveyRunResult(
      response_data=response_data,
      saved_data=saved_data,
      dry_mode=dry_mode,
    )

  @need_setup_finished
  async def do_well_transfer(
    self,
    protocol_xml: str,
    print_options: Optional[EchoTransferPrintOptions] = None,
    timeout: Optional[float] = None,
  ) -> EchoTransferResult:
    """Run ``DoWellTransfer`` with an embedded protocol XML document."""
    return await self.driver.do_well_transfer(
      protocol_xml=protocol_xml,
      print_options=print_options,
      timeout=timeout,
    )

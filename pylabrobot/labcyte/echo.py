from __future__ import annotations

import asyncio
import enum
import gzip
import html
import inspect
import logging
import os
import re
import socket
import time
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from typing import (
  Any,
  AsyncIterator,
  Awaitable,
  Callable,
  Dict,
  Iterable,
  Optional,
  Sequence,
  Tuple,
  Union,
)

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_access import PlateAccess, PlateAccessBackend, PlateAccessState
from pylabrobot.device import Device, Driver, need_setup_finished
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.utils import label_to_row_index, split_identifier
from pylabrobot.resources.volume_tracker import does_volume_tracking
from pylabrobot.resources.well import Well

ET.register_namespace("SOAP-ENV", "http://schemas.xmlsoap.org/soap/envelope/")

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
DEFAULT_HOME_TIMEOUT = 60.0
DEFAULT_TRANSFER_TIMEOUT = 300.0
DEFAULT_ECHO_CONFIGURATION_QUERY = (
  '<?xml version="1.0" encoding="utf-8"?><Configuration internal="true"></Configuration>'
)
ECHO_TRANSFER_VOLUME_INCREMENT_NL = 2.5

OperatorPause = Callable[[str], Union[None, Awaitable[None]]]


def _is_loaded_plate_type(plate_type: Optional[str]) -> bool:
  return plate_type is not None and plate_type.lower() != "none"


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
class EchoFluidInfo:
  """Fluid metadata returned by ``GetFluidInfo``."""

  name: str
  description: str
  fc_min: Optional[float]
  fc_max: Optional[float]
  fc_units: str
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
  volume_nl: Optional[float] = None
  current_volume_nl: Optional[float] = None
  fluid: str = ""
  fluid_units: str = ""
  raw_attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class EchoSurveyData:
  """Parsed survey dataset plus the original XML payload."""

  plate_type: Optional[str]
  wells: list[EchoSurveyWell]
  raw_xml: str
  raw_attributes: Dict[str, str] = field(default_factory=dict)

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
          volume_nl=_float_or_none(
            attributes.get("vl") or attributes.get("volume") or attributes.get("volume_nL")
          ),
          current_volume_nl=_float_or_none(attributes.get("cvl")),
          fluid=attributes.get("fld", ""),
          fluid_units=attributes.get("fldu", ""),
          raw_attributes=attributes,
        )
      )

    return cls(
      plate_type=plate_type,
      wells=wells,
      raw_xml=normalized_xml,
      raw_attributes={str(key): str(value) for key, value in root.attrib.items()},
    )

  @property
  def barcode(self) -> Optional[str]:
    """Return the plate barcode when Echo included one in the survey payload."""

    for key, value in self.raw_attributes.items():
      normalized_key = key.lower().replace("_", "")
      if normalized_key in {"barcode", "platebarcode", "srcbarcode", "sourcebarcode", "bc"}:
        return value
    for key, value in self.raw_attributes.items():
      if "barcode" in key.lower():
        return value
    return None

  def apply_volumes_to_plate(self, plate: Plate, *, prefer_current: bool = True) -> int:
    """Set PLR well volumes from Echo survey volume fields.

    Echo reports nanoliters; PLR volume trackers use microliters.
    """

    updated = 0
    for well_data in self.wells:
      volume_nl = (
        well_data.current_volume_nl
        if prefer_current and well_data.current_volume_nl is not None
        else well_data.volume_nl
      )
      if volume_nl is None:
        continue
      well = plate.get_well(well_data.identifier)
      if well.tracker.is_disabled:
        continue
      well.tracker.set_volume(_nl_to_ul(volume_nl))
      updated += 1
    return updated


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


@dataclass(frozen=True)
class EchoPlannedTransfer:
  """One PLR well-to-well Echo transfer in Echo-native nL."""

  source: Well
  destination: Well
  volume_nl: float

  @property
  def source_identifier(self) -> str:
    return self.source.get_identifier()

  @property
  def destination_identifier(self) -> str:
    return self.destination.get_identifier()

  @property
  def volume_ul(self) -> float:
    return _nl_to_ul(self.volume_nl)


@dataclass(frozen=True)
class EchoTransferPlan:
  """Protocol XML and source plate map generated from PLR resources."""

  source_plate: Plate
  destination_plate: Plate
  source_plate_type: str
  destination_plate_type: str
  protocol_name: str
  transfers: Tuple[EchoPlannedTransfer, ...]
  protocol_xml: str
  plate_map: EchoPlateMap


EchoTransferInput = Union[EchoPlannedTransfer, Tuple[Well, Well, float]]


def _infer_transfer_plates(transfers: Sequence[EchoTransferInput]) -> Tuple[Plate, Plate]:
  source_plate: Optional[Plate] = None
  destination_plate: Optional[Plate] = None

  for transfer in transfers:
    if isinstance(transfer, EchoPlannedTransfer):
      source = transfer.source
      destination = transfer.destination
    else:
      source, destination, _volume = transfer

    if not isinstance(source.parent, Plate):
      raise ValueError(f"Source well {source.name!r} is not assigned to a PLR Plate.")
    if not isinstance(destination.parent, Plate):
      raise ValueError(f"Destination well {destination.name!r} is not assigned to a PLR Plate.")

    if source_plate is None:
      source_plate = source.parent
    elif source.parent is not source_plate:
      raise ValueError("Echo transfer() currently supports one source plate per call.")

    if destination_plate is None:
      destination_plate = destination.parent
    elif destination.parent is not destination_plate:
      raise ValueError("Echo transfer() currently supports one destination plate per call.")

  if source_plate is None or destination_plate is None:
    raise ValueError("At least one transfer is required.")

  return source_plate, destination_plate


@dataclass
class EchoTransferredWell:
  """One completed well transfer parsed from an Echo transfer report."""

  source_identifier: str
  source_row: int
  source_column: int
  destination_identifier: str
  destination_row: int
  destination_column: int
  requested_volume_nl: Optional[float] = None
  actual_volume_nl: Optional[float] = None
  current_volume_nl: Optional[float] = None
  starting_volume_nl: Optional[float] = None
  timestamp: str = ""
  fluid: str = ""
  fluid_units: str = ""
  composition: Optional[float] = None
  fluid_thickness: Optional[float] = None
  reason: str = ""
  raw_attributes: Dict[str, str] = field(default_factory=dict)

  @property
  def tracker_volume_nl(self) -> Optional[float]:
    return self.actual_volume_nl if self.actual_volume_nl is not None else self.requested_volume_nl


@dataclass
class EchoSkippedWell:
  """One skipped transfer parsed from an Echo transfer report."""

  source_identifier: str
  source_row: int
  source_column: int
  destination_identifier: str
  destination_row: int
  destination_column: int
  requested_volume_nl: Optional[float] = None
  reason: str = ""
  raw_attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class EchoTransferResult:
  """Result returned by ``DoWellTransfer``."""

  report_xml: Optional[str]
  raw: Dict[str, Any] = field(default_factory=dict)
  succeeded: Optional[bool] = None
  status: Optional[str] = None
  source_plate_type: Optional[str] = None
  destination_plate_type: Optional[str] = None
  date: str = ""
  serial_number: str = ""
  transfers: list[EchoTransferredWell] = field(default_factory=list)
  skipped: list[EchoSkippedWell] = field(default_factory=list)


@dataclass
class EchoPlateWorkflowResult:
  """Result from a high-level source/destination load or eject workflow."""

  side: str
  plate_type: Optional[str]
  plate_present: bool
  barcode: str = ""
  current_plate_type: Optional[str] = None
  dio: Dict[str, Any] = field(default_factory=dict)


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

  def decoded_body_bytes(self) -> bytes:
    payload = self.body
    if _is_probably_gzip(payload):
      if not _gzip_stream_complete(payload):
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        partial = decompressor.decompress(payload)
        text = partial.decode("utf-8", errors="replace")
        if "</SOAP-ENV:Envelope>" in text or "</soap:Envelope>" in text:
          logger.warning(
            "Echo gzip body was missing its end-of-stream marker; using complete decoded XML payload."
          )
          return text.encode("utf-8")
        repaired_text = _repair_partial_xml_document(text)
        if repaired_text is not None:
          logger.warning(
            "Echo gzip body was missing its end-of-stream marker and final XML closing tags; "
            "using repaired decoded XML payload."
          )
          return repaired_text.encode("utf-8")
        logger.warning("Incomplete Echo gzip body tail: %r", text[-500:])
        raise EchoProtocolError(
          f"Incomplete gzip-compressed Echo HTTP body ({len(payload)} bytes)."
        )
      try:
        payload = gzip.decompress(payload)
      except (EOFError, OSError, zlib.error) as exc:
        raise EchoProtocolError("Failed to decompress gzip-compressed Echo HTTP body.") from exc
    return payload

  def decoded_body(self) -> str:
    return self.decoded_body_bytes().decode("utf-8", errors="replace")


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


def _float_or_none(value: Any) -> Optional[float]:
  if value in (None, ""):
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _int_or_zero(value: Any) -> int:
  try:
    return int(value)
  except (TypeError, ValueError):
    return 0


def _nl_to_ul(volume_nl: float) -> float:
  return volume_nl / 1000.0


def _normalize_volume_nl(volume: float, volume_unit: str) -> float:
  normalized_unit = volume_unit.strip().lower().replace("µ", "u")
  if normalized_unit in {"nl", "nanoliter", "nanoliters"}:
    return float(volume)
  if normalized_unit in {"ul", "microliter", "microliters"}:
    return float(volume) * 1000.0
  raise ValueError("volume_unit must be 'nL' or 'uL'.")


def _validate_transfer_volume_nl(volume_nl: float, context: str = "") -> None:
  prefix = f"{context}: " if context else ""
  if volume_nl <= 0:
    raise ValueError(f"{prefix}volume must be positive, got {volume_nl} nL.")
  units = volume_nl / ECHO_TRANSFER_VOLUME_INCREMENT_NL
  if abs(units - round(units)) > 1e-9:
    raise ValueError(
      f"{prefix}volume {volume_nl} nL is not a multiple of {ECHO_TRANSFER_VOLUME_INCREMENT_NL} nL."
    )


def _format_transfer_volume_nl(volume_nl: float) -> str:
  if float(volume_nl).is_integer():
    return str(int(volume_nl))
  return f"{volume_nl:g}"


def _resolve_plate_type(plate: Plate, plate_type: Optional[str], role: str) -> str:
  if plate_type:
    return plate_type
  if plate.model:
    return plate.model
  raise ValueError(
    f"{role} plate type is required when the PLR plate has no model matching an Echo plate type."
  )


def _resolve_well_reference(plate: Plate, well: Union[str, Well], role: str) -> Well:
  if isinstance(well, Well):
    if well.parent is not plate:
      raise ValueError(f"{role} well {well.name!r} is not on plate {plate.name!r}.")
    return well
  return plate.get_well(str(well))


def _make_transfer_protocol_xml(
  transfers: Sequence[tuple[str, str, float]], protocol_name: str
) -> str:
  protocol = ET.Element("Protocol", {"Name": protocol_name})
  ET.SubElement(protocol, "Name")
  layout = ET.SubElement(protocol, "Layout")
  for source_identifier, destination_identifier, volume_nl in transfers:
    ET.SubElement(
      layout,
      "wp",
      {
        "n": source_identifier,
        "dn": destination_identifier,
        "v": _format_transfer_volume_nl(volume_nl),
      },
    )
  return '<?xml version="1.0" encoding="utf-8"?>' + ET.tostring(
    protocol,
    encoding="unicode",
    short_empty_elements=True,
  )


def build_echo_transfer_plan(
  source_plate: Plate,
  destination_plate: Plate,
  transfers: Sequence[Union[EchoPlannedTransfer, Tuple[Union[str, Well], Union[str, Well], float]]],
  *,
  source_plate_type: Optional[str] = None,
  destination_plate_type: Optional[str] = None,
  protocol_name: str = "transfer",
  volume_unit: str = "nL",
) -> EchoTransferPlan:
  """Build Echo protocol XML and source plate map from PLR plates and wells."""

  planned_transfers: list[EchoPlannedTransfer] = []
  protocol_transfers: list[tuple[str, str, float]] = []
  for transfer in transfers:
    if isinstance(transfer, EchoPlannedTransfer):
      planned = transfer
      if planned.source.parent is not source_plate:
        raise ValueError(f"Source well {planned.source.name!r} is not on {source_plate.name!r}.")
      if planned.destination.parent is not destination_plate:
        raise ValueError(
          f"Destination well {planned.destination.name!r} is not on {destination_plate.name!r}."
        )
    else:
      source_ref, destination_ref, volume = transfer
      source = _resolve_well_reference(source_plate, source_ref, "Source")
      destination = _resolve_well_reference(destination_plate, destination_ref, "Destination")
      planned = EchoPlannedTransfer(
        source=source,
        destination=destination,
        volume_nl=_normalize_volume_nl(float(volume), volume_unit),
      )
    source_identifier = planned.source_identifier
    destination_identifier = planned.destination_identifier
    _validate_transfer_volume_nl(
      planned.volume_nl,
      f"{source_identifier}->{destination_identifier}",
    )
    planned_transfers.append(planned)
    protocol_transfers.append((source_identifier, destination_identifier, planned.volume_nl))

  if not planned_transfers:
    raise ValueError("At least one transfer is required.")

  source_type = _resolve_plate_type(source_plate, source_plate_type, "Source")
  destination_type = _resolve_plate_type(destination_plate, destination_plate_type, "Destination")
  source_wells = tuple(
    dict.fromkeys(source for source, _destination, _volume in protocol_transfers)
  )
  return EchoTransferPlan(
    source_plate=source_plate,
    destination_plate=destination_plate,
    source_plate_type=source_type,
    destination_plate_type=destination_type,
    protocol_name=protocol_name,
    transfers=tuple(planned_transfers),
    protocol_xml=_make_transfer_protocol_xml(protocol_transfers, protocol_name),
    plate_map=EchoPlateMap(plate_type=source_type, well_identifiers=source_wells),
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
  return _split_complete_gzip_body(payload) is not None


def _split_complete_gzip_body(payload: bytes) -> Optional[Tuple[bytes, bytes]]:
  if not _is_probably_gzip(payload):
    return payload, b""
  decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
  try:
    decompressor.decompress(payload)
  except zlib.error:
    return None
  if not decompressor.eof:
    return None
  body_length = len(payload) - len(decompressor.unused_data)
  return payload[:body_length], decompressor.unused_data


_XML_TAG_RE = re.compile(r"<(/?)([A-Za-z_][\w:.-]*)([^<>]*)>")


def _repair_partial_xml_document(text: str) -> Optional[str]:
  candidate = text.strip()
  if not candidate:
    return None
  lower = candidate.lower()
  if "<soap-env:envelope" not in lower and "<soap:envelope" not in lower:
    return None
  if "</soap-env:envelope>" in lower or "</soap:envelope>" in lower:
    return candidate

  last_tag_end = candidate.rfind(">")
  if last_tag_end == -1:
    return None
  candidate = candidate[: last_tag_end + 1]

  stack: list[str] = []
  for match in _XML_TAG_RE.finditer(candidate):
    full_tag = match.group(0)
    if full_tag.startswith("<?") or full_tag.startswith("<!"):
      continue
    closing = match.group(1) == "/"
    tag_name = match.group(2)
    attrs = match.group(3).strip()
    if not closing and attrs.endswith("/"):
      continue
    if closing:
      if tag_name in stack:
        while stack:
          open_tag = stack.pop()
          if open_tag == tag_name:
            break
      continue
    stack.append(tag_name)

  if not stack:
    return None

  repaired = candidate + "".join(f"</{tag_name}>" for tag_name in reversed(stack))
  try:
    ET.fromstring(repaired)
  except ET.ParseError:
    return None
  return repaired


def _is_gzip_protocol_error(error: BaseException) -> bool:
  if isinstance(error, (EOFError, OSError, zlib.error)):
    return True
  if not isinstance(error, EchoProtocolError):
    return False
  message = str(error).lower()
  return "gzip" in message or "compressed file ended" in message or "complete gzip body" in message


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


def _parse_echo_transfer_report(
  report_xml: Optional[str],
  *,
  raw: Dict[str, Any],
  succeeded: Optional[bool],
  status: Optional[str],
) -> EchoTransferResult:
  result = EchoTransferResult(
    report_xml=report_xml,
    raw=raw,
    succeeded=succeeded,
    status=status,
  )
  if report_xml in (None, ""):
    return result

  normalized_xml = html.unescape(str(report_xml)).strip()
  result.report_xml = normalized_xml
  try:
    root = ET.fromstring(normalized_xml)
  except ET.ParseError as exc:
    raise EchoProtocolError("Malformed Echo transfer report XML.") from exc

  result.date = root.attrib.get("date", "")
  result.serial_number = root.attrib.get("serial_number", "")
  plates = root.findall(".//plateInfo/plate")
  if len(plates) >= 1:
    result.source_plate_type = plates[0].attrib.get("name", "") or None
  if len(plates) >= 2:
    result.destination_plate_type = plates[1].attrib.get("name", "") or None

  for well in root.findall(".//printmap/w"):
    attributes = {str(key): str(value) for key, value in well.attrib.items()}
    result.transfers.append(
      EchoTransferredWell(
        source_identifier=attributes.get("n", ""),
        source_row=_int_or_zero(attributes.get("r")),
        source_column=_int_or_zero(attributes.get("c")),
        destination_identifier=attributes.get("dn", ""),
        destination_row=_int_or_zero(attributes.get("dr")),
        destination_column=_int_or_zero(attributes.get("dc")),
        requested_volume_nl=_float_or_none(attributes.get("vt")),
        actual_volume_nl=_float_or_none(attributes.get("avt")),
        current_volume_nl=_float_or_none(attributes.get("cvl")),
        starting_volume_nl=_float_or_none(attributes.get("vl")),
        timestamp=attributes.get("t", ""),
        fluid=attributes.get("fld", ""),
        fluid_units=attributes.get("fldu", ""),
        composition=_float_or_none(attributes.get("fc")),
        fluid_thickness=_float_or_none(attributes.get("ft")),
        reason=attributes.get("reason", ""),
        raw_attributes=attributes,
      )
    )

  for well in root.findall(".//skippedwells/w"):
    attributes = {str(key): str(value) for key, value in well.attrib.items()}
    result.skipped.append(
      EchoSkippedWell(
        source_identifier=attributes.get("n", ""),
        source_row=_int_or_zero(attributes.get("r")),
        source_column=_int_or_zero(attributes.get("c")),
        destination_identifier=attributes.get("dn", ""),
        destination_row=_int_or_zero(attributes.get("dr")),
        destination_column=_int_or_zero(attributes.get("dc")),
        requested_volume_nl=_float_or_none(attributes.get("vt")),
        reason=attributes.get("reason", ""),
        raw_attributes=attributes,
      )
    )

  return result


async def _call_operator_pause(
  callback: Optional[OperatorPause],
  message: str,
) -> None:
  if callback is None:
    return
  result = callback(message)
  if inspect.isawaitable(result):
    await result


def _preflight_transfer_volume_tracking(plan: EchoTransferPlan) -> None:
  if not does_volume_tracking():
    return

  source_totals: Dict[Well, float] = {}
  destination_totals: Dict[Well, float] = {}
  for transfer in plan.transfers:
    source_totals[transfer.source] = source_totals.get(transfer.source, 0.0) + transfer.volume_ul
    destination_totals[transfer.destination] = (
      destination_totals.get(transfer.destination, 0.0) + transfer.volume_ul
    )

  for well, volume_ul in source_totals.items():
    if well.tracker.is_disabled:
      continue
    if (volume_ul - well.tracker.get_used_volume()) > 1e-6:
      raise EchoCommandError(
        "TransferWells",
        f"Not enough liquid in {well.get_identifier()}: "
        f"{volume_ul}uL > {well.tracker.get_used_volume()}uL.",
      )

  for well, volume_ul in destination_totals.items():
    if well.tracker.is_disabled:
      continue
    if (volume_ul - well.tracker.get_free_volume()) > 1e-6:
      raise EchoCommandError(
        "TransferWells",
        f"Not enough space in {well.get_identifier()}: "
        f"{volume_ul}uL > {well.tracker.get_free_volume()}uL.",
      )


def _apply_transfer_volume_tracking(plan: EchoTransferPlan, result: EchoTransferResult) -> int:
  if not does_volume_tracking():
    return 0

  planned_by_pair = {
    (transfer.source_identifier, transfer.destination_identifier): transfer
    for transfer in plan.transfers
  }
  touched: set[Well] = set()
  updates = 0
  try:
    for transferred in result.transfers:
      planned = planned_by_pair.get(
        (transferred.source_identifier, transferred.destination_identifier)
      )
      if planned is None:
        continue
      volume_nl = transferred.tracker_volume_nl
      if volume_nl is None:
        continue
      volume_ul = _nl_to_ul(volume_nl)
      if not planned.source.tracker.is_disabled:
        planned.source.tracker.remove_liquid(volume_ul)
        touched.add(planned.source)
      if not planned.destination.tracker.is_disabled:
        planned.destination.tracker.add_liquid(volume_ul)
        touched.add(planned.destination)
      updates += 1
  except Exception:
    for well in touched:
      if not well.tracker.is_disabled:
        well.tracker.rollback()
    raise

  for well in touched:
    if not well.tracker.is_disabled:
      well.tracker.commit()
  return updates


def _soap_fault_status(root: ET.Element) -> Optional[str]:
  body = next((node for node in root if _local_name(node.tag) == "Body"), None)
  if body is None or len(body) == 0:
    return None
  fault = next((node for node in body if _local_name(node.tag) == "Fault"), None)
  if fault is None:
    return None
  fault_string = next(
    (
      child.text for child in fault.iter() if _local_name(child.tag) == "faultstring" and child.text
    ),
    "",
  )
  return f"SOAP Fault: {fault_string}" if fault_string else "SOAP Fault"


def _print_options_xml(options: EchoTransferPrintOptions) -> str:
  root = ET.Element(
    "PrintOptions",
    {
      "xmlns:SOAP-ENV": "http://schemas.xmlsoap.org/soap/envelope/",
      "SOAP-ENV:encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/",
    },
  )
  for name, value_type, value in options.to_params():
    child = ET.SubElement(
      root,
      name,
      {
        "SOAP-ENV:encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/",
        "type": f"xsd:{value_type}",
      },
    )
    child.text = value
  return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _dump_malformed_xml_response(
  method: str,
  *,
  payload_bytes: bytes,
  body_text: str,
) -> tuple[str, str] | None:
  dump_dir = (os.getenv("PYLABROBOT_ECHO_DEBUG_DIR") or "").strip()
  if not dump_dir:
    return None
  try:
    os.makedirs(dump_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    safe_method = "".join(char.lower() if char.isalnum() else "-" for char in method).strip("-")
    base_name = f"{stamp}-{os.getpid()}-{safe_method or 'response'}"
    raw_path = os.path.join(dump_dir, f"{base_name}.body.bin")
    text_path = os.path.join(dump_dir, f"{base_name}.body.txt")
    with open(raw_path, "wb") as raw_file:
      raw_file.write(payload_bytes)
    with open(text_path, "w", encoding="utf-8", errors="replace") as text_file:
      text_file.write(body_text)
    return raw_path, text_path
  except OSError:
    logger.exception("Failed to write malformed Echo XML response dump for %s", method)
    return None


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
    self._buffer = bytearray()

  async def __aenter__(self) -> "EchoEventStream":
    return self

  async def __aexit__(self, exc_type, exc, tb) -> None:
    await self.close()

  async def read_event(self, timeout: Optional[float] = None) -> EchoEvent:
    message = await self._driver._read_http_message(
      self._reader,
      timeout=timeout,
      buffer=self._buffer,
    )
    return _parse_event_from_message(message)

  async def iter_events(self, timeout: Optional[float] = None) -> AsyncIterator[EchoEvent]:
    """Yield events from the stream until the connection closes or the caller stops iteration."""
    while True:
      yield await self.read_event(timeout=timeout)

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

  async def setup(self, backend_params: Optional[BackendParams] = None):
    del backend_params
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

  async def get_echo_configuration(
    self,
    config_xml: str = DEFAULT_ECHO_CONFIGURATION_QUERY,
  ) -> str:
    result = await self._rpc(
      "GetEchoConfiguration",
      (("xmlEchoConfig", "string", config_xml),),
    )
    self._ensure_success("GetEchoConfiguration", result)
    value = result.values.get("xmlEchoConfig", _first_result_value(result))
    return "" if value in (None, "") else str(value)

  async def get_power_calibration(self) -> Dict[str, Any]:
    result = await self._rpc("GetPwrCal")
    self._ensure_success("GetPwrCal", result)
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

  async def is_source_plate_present(self) -> bool:
    plate_type = await self.get_current_source_plate_type()
    return _is_loaded_plate_type(plate_type)

  async def is_destination_plate_present(self) -> bool:
    plate_type = await self.get_current_destination_plate_type()
    return _is_loaded_plate_type(plate_type)

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

  async def get_all_protocol_names(self) -> list[str]:
    result = await self._rpc("GetAllProtocolNames")
    self._ensure_success("GetAllProtocolNames", result)
    return _name_list_from_value(result.values.get("ProtocolName", _first_result_value(result)))

  async def get_protocol(self, name: Optional[str] = None) -> Dict[str, Any]:
    params: Tuple[Tuple[str, str, str], ...] = ()
    if name:
      params = (("ProtocolName", "string", name),)
    result = await self._rpc("GetProtocol", params)
    self._ensure_success("GetProtocol", result)
    return result.values

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

  async def get_fluid_info(self, fluid_type: str) -> EchoFluidInfo:
    result = await self._rpc(
      "GetFluidInfo",
      (("FluidType", "string", fluid_type),),
    )
    self._ensure_success("GetFluidInfo", result)
    return EchoFluidInfo(
      name=str(result.values.get("FluidName") or result.values.get("Name") or fluid_type),
      description=str(result.values.get("Description", "")),
      fc_min=_float_or_none(result.values.get("FCMin")),
      fc_max=_float_or_none(result.values.get("FCMax")),
      fc_units=str(result.values.get("FCUnits", "")),
      raw=result.values,
    )

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
  ) -> Optional[str]:
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
    barcode_value = result.values.get("BarCode")
    return None if barcode_value in (None, "") else str(barcode_value)

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
  ) -> Optional[str]:
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
    barcode_value = result.values.get("BarCode")
    return None if barcode_value in (None, "") else str(barcode_value)

  async def close_door(self, timeout: Optional[float] = None) -> None:
    self._require_lock("CloseDoor")
    result = await self._rpc("CloseDoor", timeout=timeout)
    self._ensure_success("CloseDoor", result)

  async def open_door(self, timeout: Optional[float] = None) -> None:
    self._require_lock("OpenDoor")
    result = await self._rpc("OpenDoor", timeout=timeout)
    self._ensure_success("OpenDoor", result)

  async def home_axes(self, timeout: Optional[float] = None) -> None:
    self._require_lock("HomeAxes")
    result = await self._rpc("HomeAxes", timeout=_resolve_timeout(timeout, DEFAULT_HOME_TIMEOUT))
    self._ensure_success("HomeAxes", result)

  async def set_pump_direction(self, normal: bool = True, timeout: Optional[float] = None) -> None:
    self._require_lock("SetPumpDir")
    result = await self._rpc(
      "SetPumpDir",
      (("Value", "boolean", _format_bool(normal)),),
      timeout=timeout,
    )
    self._ensure_success("SetPumpDir", result)

  async def enable_bubbler_pump(
    self,
    enabled: bool = True,
    timeout: Optional[float] = None,
  ) -> None:
    self._require_lock("EnableBubblerPump")
    result = await self._rpc(
      "EnableBubblerPump",
      (("Value", "boolean", _format_bool(enabled)),),
      timeout=timeout,
    )
    self._ensure_success("EnableBubblerPump", result)

  async def actuate_bubbler_nozzle(
    self,
    up: bool,
    timeout: Optional[float] = None,
  ) -> None:
    self._require_lock("ActuateBubblerNozzle")
    result = await self._rpc(
      "ActuateBubblerNozzle",
      (("Value", "boolean", _format_bool(up)),),
      timeout=timeout,
    )
    self._ensure_success("ActuateBubblerNozzle", result)

  async def raise_coupling_fluid(self, timeout: Optional[float] = None) -> None:
    await self.actuate_bubbler_nozzle(True, timeout=timeout)

  async def lower_coupling_fluid(self, timeout: Optional[float] = None) -> None:
    await self.actuate_bubbler_nozzle(False, timeout=timeout)

  async def enable_vacuum_nozzle(
    self,
    enabled: bool,
    timeout: Optional[float] = None,
  ) -> None:
    self._require_lock("EnableVacuumNozzle")
    result = await self._rpc(
      "EnableVacuumNozzle",
      (("Value", "boolean", _format_bool(enabled)),),
      timeout=timeout,
    )
    self._ensure_success("EnableVacuumNozzle", result)

  async def actuate_vacuum_nozzle(
    self,
    engage: bool,
    timeout: Optional[float] = None,
  ) -> None:
    self._require_lock("ActuateVacuumNozzle")
    result = await self._rpc(
      "ActuateVacuumNozzle",
      (("Value", "boolean", _format_bool(engage)),),
      timeout=timeout,
    )
    self._ensure_success("ActuateVacuumNozzle", result)

  async def actuate_ionizer(self, enabled: bool, timeout: Optional[float] = None) -> None:
    self._require_lock("ActuateIonizer")
    result = await self._rpc(
      "ActuateIonizer",
      (("Value", "boolean", _format_bool(enabled)),),
      timeout=timeout,
    )
    self._ensure_success("ActuateIonizer", result)

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
    source_plate: Optional[Plate] = None,
    update_volume_trackers: bool = True,
  ) -> EchoSurveyRunResult:
    await self.set_plate_map(plate_map)
    saved_data = None
    try:
      response_data = await self.survey_plate(survey)
    except EchoProtocolError as exc:
      if not fetch_saved_data or not survey.save or not _is_gzip_protocol_error(exc):
        raise
      logger.warning(
        "PlateSurvey returned an incomplete gzip response after the survey run; "
        "recovering by reading the saved survey data."
      )
      response_data = None
    if fetch_saved_data and survey.save:
      saved_data_error: BaseException | None = None
      for attempt in range(2):
        try:
          saved_data = await self.get_survey_data()
          saved_data_error = None
          break
        except (EchoProtocolError, EOFError, OSError, zlib.error) as exc:
          saved_data_error = exc
          if not _is_gzip_protocol_error(exc) or attempt > 0:
            break
          logger.warning(
            "Retrying GetSurveyData after gzip decode failure: %s",
            exc,
          )
          await asyncio.sleep(0.5)
      if saved_data_error is not None:
        if response_data is None:
          raise saved_data_error
        logger.warning(
          "Using PlateSurvey response data because GetSurveyData failed: %s",
          saved_data_error,
        )
    if update_volume_trackers and does_volume_tracking() and source_plate is not None:
      data = saved_data or response_data
      if data is not None:
        data.apply_volumes_to_plate(source_plate)
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
    return _parse_echo_transfer_report(
      _embedded_xml_from_values(result.values, "<transfer"),
      raw=result.values,
      succeeded=result.succeeded,
      status=result.status,
    )

  def build_transfer_plan(
    self,
    source_plate: Plate,
    destination_plate: Plate,
    transfers: Sequence[
      Union[EchoPlannedTransfer, Tuple[Union[str, Well], Union[str, Well], float]]
    ],
    *,
    source_plate_type: Optional[str] = None,
    destination_plate_type: Optional[str] = None,
    protocol_name: str = "transfer",
    volume_unit: str = "nL",
  ) -> EchoTransferPlan:
    return build_echo_transfer_plan(
      source_plate,
      destination_plate,
      transfers,
      source_plate_type=source_plate_type,
      destination_plate_type=destination_plate_type,
      protocol_name=protocol_name,
      volume_unit=volume_unit,
    )

  async def transfer_wells(
    self,
    source_plate: Plate,
    destination_plate: Plate,
    transfers: Sequence[
      Union[EchoPlannedTransfer, Tuple[Union[str, Well], Union[str, Well], float]]
    ],
    *,
    source_plate_type: Optional[str] = None,
    destination_plate_type: Optional[str] = None,
    protocol_name: str = "transfer",
    volume_unit: str = "nL",
    do_survey: bool = True,
    close_door_before_transfer: bool = True,
    print_options: Optional[EchoTransferPrintOptions] = None,
    timeout: Optional[float] = None,
    survey_timeout: Optional[float] = None,
    update_volume_trackers: bool = True,
  ) -> EchoTransferResult:
    self._require_lock("TransferWells")
    plan = self.build_transfer_plan(
      source_plate,
      destination_plate,
      transfers,
      source_plate_type=source_plate_type,
      destination_plate_type=destination_plate_type,
      protocol_name=protocol_name,
      volume_unit=volume_unit,
    )

    await self.get_current_source_plate_type()
    await self.get_current_destination_plate_type()
    await self.retrieve_parameter("Client_IgnoreDestPlateSensor")
    await self.retrieve_parameter("Client_IgnoreSourcePlateSensor")
    await self.set_plate_map(plan.plate_map)
    await self.get_plate_info(plan.source_plate_type)

    if close_door_before_transfer:
      await self.close_door()

    if do_survey:
      max_source_row = max(transfer.source.get_row() for transfer in plan.transfers)
      survey_data = await self.survey_plate(
        EchoSurveyParams(
          plate_type=plan.source_plate_type,
          start_row=0,
          start_col=0,
          num_rows=max_source_row + 1,
          num_cols=source_plate.num_items_x,
          save=True,
          check_source=False,
          timeout=survey_timeout,
        )
      )
      if update_volume_trackers and does_volume_tracking() and survey_data is not None:
        survey_data.apply_volumes_to_plate(source_plate)

    if update_volume_trackers:
      _preflight_transfer_volume_tracking(plan)

    await self.get_dio_ex2()
    await self.get_dio()

    result = await self.do_well_transfer(
      plan.protocol_xml,
      print_options
      or EchoTransferPrintOptions(
        save_survey=True,
        save_print=True,
      ),
      timeout=_resolve_timeout(timeout, DEFAULT_TRANSFER_TIMEOUT),
    )
    if update_volume_trackers:
      _apply_transfer_volume_tracking(plan, result)
    return result

  async def transfer(
    self,
    transfers: Sequence[EchoTransferInput],
    *,
    source_plate_type: Optional[str] = None,
    destination_plate_type: Optional[str] = None,
    protocol_name: str = "transfer",
    volume_unit: str = "nL",
    do_survey: bool = True,
    close_door_before_transfer: bool = True,
    print_options: Optional[EchoTransferPrintOptions] = None,
    timeout: Optional[float] = None,
    survey_timeout: Optional[float] = None,
    update_volume_trackers: bool = True,
  ) -> EchoTransferResult:
    """Plan and execute Echo transfers from PLR wells.

    This is the highest-level transfer entry point: each transfer contains real PLR
    source and destination wells, so the source and destination plates are inferred
    from the well parents. Use ``transfer_wells`` when the caller only has well
    identifiers and wants to pass the plates explicitly.
    """
    source_plate, destination_plate = _infer_transfer_plates(transfers)
    return await self.transfer_wells(
      source_plate,
      destination_plate,
      transfers,
      source_plate_type=source_plate_type,
      destination_plate_type=destination_plate_type,
      protocol_name=protocol_name,
      volume_unit=volume_unit,
      do_survey=do_survey,
      close_door_before_transfer=close_door_before_transfer,
      print_options=print_options,
      timeout=timeout,
      survey_timeout=survey_timeout,
      update_volume_trackers=update_volume_trackers,
    )

  async def load_source_plate(
    self,
    plate_type: str,
    *,
    barcode_location: str = "Right-Side",
    barcode: str = "",
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = True,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    self._require_lock("LoadSourcePlate")
    if open_door_first:
      await self.open_door()
    await self.open_source_plate(timeout=present_timeout)
    await _call_operator_pause(operator_pause, "source plate presented")
    await self.get_power_calibration()
    await self.get_plate_info(plate_type)
    await self.get_current_source_plate_type()
    barcode_result = await self.close_source_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=retract_timeout,
    )
    await self.retrieve_parameter("Client_IgnoreSourcePlateSensor")
    current_plate_type = await self.get_current_source_plate_type()
    dio = await self.get_dio_ex2()
    return EchoPlateWorkflowResult(
      side="source",
      plate_type=plate_type,
      plate_present=_is_loaded_plate_type(current_plate_type),
      barcode=barcode_result or "",
      current_plate_type=current_plate_type,
      dio=dio,
    )

  async def load_destination_plate(
    self,
    plate_type: str,
    *,
    barcode_location: str = "Right-Side",
    barcode: str = "",
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = True,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    self._require_lock("LoadDestinationPlate")
    if open_door_first:
      await self.open_door()
    await self.open_destination_plate(timeout=present_timeout)
    await _call_operator_pause(operator_pause, "destination plate presented")
    await self.get_power_calibration()
    await self.get_plate_info(plate_type)
    barcode_result = await self.close_destination_plate(
      plate_type=plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      timeout=retract_timeout,
    )
    await self.retrieve_parameter("Client_IgnoreDestPlateSensor")
    current_plate_type = await self.get_current_destination_plate_type()
    dio = await self.get_dio_ex2()
    return EchoPlateWorkflowResult(
      side="destination",
      plate_type=plate_type,
      plate_present=_is_loaded_plate_type(current_plate_type),
      barcode=barcode_result or "",
      current_plate_type=current_plate_type,
      dio=dio,
    )

  async def eject_source_plate(
    self,
    *,
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = False,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    self._require_lock("EjectSourcePlate")
    if open_door_first:
      await self.open_door()
    await self.open_source_plate(timeout=present_timeout)
    await _call_operator_pause(operator_pause, "source plate presented for removal")
    barcode_result = await self.close_source_plate(timeout=retract_timeout)
    current_plate_type = await self.get_current_source_plate_type()
    dio = await self.get_dio_ex2()
    return EchoPlateWorkflowResult(
      side="source",
      plate_type=None,
      plate_present=_is_loaded_plate_type(current_plate_type),
      barcode=barcode_result or "",
      current_plate_type=current_plate_type,
      dio=dio,
    )

  async def eject_destination_plate(
    self,
    *,
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = False,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    self._require_lock("EjectDestinationPlate")
    if open_door_first:
      await self.open_door()
    await self.open_destination_plate(timeout=present_timeout)
    await _call_operator_pause(operator_pause, "destination plate presented for removal")
    barcode_result = await self.close_destination_plate(timeout=retract_timeout)
    current_plate_type = await self.get_current_destination_plate_type()
    dio = await self.get_dio_ex2()
    return EchoPlateWorkflowResult(
      side="destination",
      plate_type=None,
      plate_present=_is_loaded_plate_type(current_plate_type),
      barcode=barcode_result or "",
      current_plate_type=current_plate_type,
      dio=dio,
    )

  async def eject_all_plates(
    self,
    *,
    operator_pause: Optional[OperatorPause] = None,
    close_door_after: bool = True,
    open_door_first: bool = False,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> Tuple[EchoPlateWorkflowResult, EchoPlateWorkflowResult]:
    source_result = await self.eject_source_plate(
      operator_pause=operator_pause,
      open_door_first=open_door_first,
      present_timeout=present_timeout,
      retract_timeout=retract_timeout,
    )
    destination_result = await self.eject_destination_plate(
      operator_pause=operator_pause,
      open_door_first=False,
      present_timeout=present_timeout,
      retract_timeout=retract_timeout,
    )
    if close_door_after:
      await self.close_door()
    return source_result, destination_result

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
    buffer: Optional[bytearray] = None,
  ) -> _HttpMessage:
    read_timeout = _resolve_timeout(timeout, self.timeout)
    data = bytearray(buffer or b"")
    if buffer is not None:
      buffer.clear()
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

    content_length_header = headers.get("content-length")
    try:
      content_length = int(content_length_header) if content_length_header is not None else None
    except ValueError:
      content_length = None

    if content_length is not None and content_length > 0:
      framed = await self._read_exact(
        reader,
        content_length,
        initial=rest,
        timeout=read_timeout,
      )
      body = framed[:content_length]
      extra = framed[content_length:]
    else:
      body = rest
      extra = b""

    if _is_probably_gzip(body) and not _gzip_stream_complete(body):
      body, extra = await self._read_until_complete_gzip_body(
        reader,
        initial=body + extra,
        advertised_length=content_length,
        timeout=read_timeout,
      )
    if buffer is not None and extra:
      buffer.extend(extra)
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
    advertised_length: Optional[int],
    timeout: Optional[float] = None,
  ) -> Tuple[bytes, bytes]:
    read_timeout = _resolve_timeout(timeout, self.timeout)
    data = bytearray(initial)
    while True:
      split = _split_complete_gzip_body(bytes(data))
      if split is not None:
        body, extra = split
        break
      chunk = await asyncio.wait_for(reader.read(4096), timeout=read_timeout)
      if not chunk:
        advertised = "unknown" if advertised_length is None else str(advertised_length)
        raise EchoProtocolError(
          "Connection closed before complete gzip body arrived "
          f"(advertised {advertised} bytes, received {len(data)} bytes)."
        )
      data.extend(chunk)

    if advertised_length is not None and len(body) != advertised_length:
      logger.warning(
        "Echo response gzip body exceeded advertised Content-Length: header=%s actual=%s",
        advertised_length,
        len(body),
      )
    return body, extra

  def _parse_rpc_result(self, method: str, message: _HttpMessage) -> _RpcResult:
    payload_bytes = message.decoded_body_bytes()
    body_text = payload_bytes.decode("utf-8", errors="replace")
    try:
      root = ET.fromstring(body_text)
    except ET.ParseError as exc:
      dump_paths = _dump_malformed_xml_response(
        method,
        payload_bytes=payload_bytes,
        body_text=body_text,
      )
      details = ""
      if dump_paths is not None:
        raw_path, text_path = dump_paths
        details = f" Saved raw body to {raw_path} and decoded body to {text_path}."
      raise EchoProtocolError(f"Malformed XML in {method} response.{details}") from exc

    fault_status = _soap_fault_status(root)
    if fault_status is not None:
      return _RpcResult(
        method=method,
        values={"SUCCEEDED": False, "Status": fault_status},
        succeeded=False,
        status=fault_status,
      )

    payload = self._extract_payload_element(root)
    values: Dict[str, Any] = {}
    for child in payload:
      key = _local_name(child.tag)
      value = _element_value(child)
      if key in values:
        existing_value = values[key]
        if isinstance(existing_value, list):
          existing_value.append(value)
        else:
          values[key] = [existing_value, value]
      else:
        values[key] = value

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
  ) -> Optional[str]:
    return await self.driver.close_source_plate(
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
  ) -> Optional[str]:
    return await self.driver.close_destination_plate(
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


class EchoPlatePosition(ResourceHolder):
  """A physical Echo source or destination plate position."""

  def __init__(self, name: str, role: str):
    super().__init__(
      name=name,
      size_x=127.76,
      size_y=85.48,
      size_z=20.0,
      category="labcyte_echo_plate_position",
      model=f"labcyte_echo_{role}_position",
      child_location=Coordinate.zero(),
    )
    self.role = role

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    if not isinstance(resource, Plate):
      raise TypeError("Echo plate positions can only hold PLR Plate resources.")
    return super().assign_child_resource(resource, location, reassign)

  @property
  def plate(self) -> Optional[Plate]:
    resource = self.resource
    return resource if isinstance(resource, Plate) else None

  @plate.setter
  def plate(self, plate: Optional[Plate]) -> None:
    self.resource = plate


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
    self.deck = Resource(
      name="labcyte_echo",
      size_x=360.0,
      size_y=300.0,
      size_z=260.0,
      category="labcyte_echo",
      model="Labcyte Echo",
    )
    self.source_position = EchoPlatePosition(name="echo_source_position", role="source")
    self.destination_position = EchoPlatePosition(
      name="echo_destination_position",
      role="destination",
    )
    self.deck.assign_child_resource(self.source_position, location=Coordinate(75.0, 120.0, 0.0))
    self.deck.assign_child_resource(
      self.destination_position,
      location=Coordinate(205.0, 120.0, 0.0),
    )

  @property
  def source_plate(self) -> Optional[Plate]:
    """Return the PLR plate assigned to the Echo source position."""
    return self.source_position.plate

  @source_plate.setter
  def source_plate(self, plate: Optional[Plate]) -> None:
    self.source_position.plate = plate

  @property
  def destination_plate(self) -> Optional[Plate]:
    """Return the PLR plate assigned to the Echo destination position."""
    return self.destination_position.plate

  @destination_plate.setter
  def destination_plate(self, plate: Optional[Plate]) -> None:
    self.destination_position.plate = plate

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
  async def get_echo_configuration(
    self,
    config_xml: str = DEFAULT_ECHO_CONFIGURATION_QUERY,
  ) -> str:
    """Return the raw Echo configuration XML."""
    return await self.driver.get_echo_configuration(config_xml=config_xml)

  @need_setup_finished
  async def get_power_calibration(self) -> Dict[str, Any]:
    """Return the raw ``GetPwrCal`` payload."""
    return await self.driver.get_power_calibration()

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
  async def is_source_plate_present(self) -> bool:
    """Return whether the Echo has a registered source plate loaded."""
    return await self.driver.is_source_plate_present()

  @need_setup_finished
  async def is_destination_plate_present(self) -> bool:
    """Return whether the Echo has a registered destination plate loaded."""
    return await self.driver.is_destination_plate_present()

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
  async def get_all_protocol_names(self) -> list[str]:
    """Return the Echo protocol-name catalog."""
    return await self.driver.get_all_protocol_names()

  @need_setup_finished
  async def get_protocol(self, name: Optional[str] = None) -> Dict[str, Any]:
    """Return the raw ``GetProtocol`` payload."""
    return await self.driver.get_protocol(name=name)

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
  async def get_fluid_info(self, fluid_type: str) -> EchoFluidInfo:
    """Return fluid metadata for a known Echo fluid type."""
    return await self.driver.get_fluid_info(fluid_type)

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
  async def open_door(self, timeout: Optional[float] = None) -> None:
    """Open the Echo door."""
    await self.driver.open_door(timeout=timeout)

  @need_setup_finished
  async def home_axes(self, timeout: Optional[float] = None) -> None:
    """Home all Echo axes."""
    await self.driver.home_axes(timeout=timeout)

  @need_setup_finished
  async def set_pump_direction(
    self,
    normal: bool = True,
    timeout: Optional[float] = None,
  ) -> None:
    """Set the coupling-fluid pump direction."""
    await self.driver.set_pump_direction(normal=normal, timeout=timeout)

  @need_setup_finished
  async def enable_bubbler_pump(
    self,
    enabled: bool = True,
    timeout: Optional[float] = None,
  ) -> None:
    """Enable or disable the coupling-fluid bubbler pump."""
    await self.driver.enable_bubbler_pump(enabled=enabled, timeout=timeout)

  @need_setup_finished
  async def actuate_bubbler_nozzle(
    self,
    up: bool,
    timeout: Optional[float] = None,
  ) -> None:
    """Raise or lower the coupling-fluid bubbler nozzle."""
    await self.driver.actuate_bubbler_nozzle(up=up, timeout=timeout)

  @need_setup_finished
  async def raise_coupling_fluid(self, timeout: Optional[float] = None) -> None:
    """Raise the coupling fluid."""
    await self.driver.raise_coupling_fluid(timeout=timeout)

  @need_setup_finished
  async def lower_coupling_fluid(self, timeout: Optional[float] = None) -> None:
    """Lower the coupling fluid."""
    await self.driver.lower_coupling_fluid(timeout=timeout)

  @need_setup_finished
  async def enable_vacuum_nozzle(
    self,
    enabled: bool,
    timeout: Optional[float] = None,
  ) -> None:
    """Enable or disable the vacuum pump/nozzle control."""
    await self.driver.enable_vacuum_nozzle(enabled=enabled, timeout=timeout)

  @need_setup_finished
  async def actuate_vacuum_nozzle(
    self,
    engage: bool,
    timeout: Optional[float] = None,
  ) -> None:
    """Engage or release the vacuum nozzle mechanism."""
    await self.driver.actuate_vacuum_nozzle(engage=engage, timeout=timeout)

  @need_setup_finished
  async def actuate_ionizer(self, enabled: bool, timeout: Optional[float] = None) -> None:
    """Enable or disable the ionizer."""
    await self.driver.actuate_ionizer(enabled=enabled, timeout=timeout)

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
    source_plate: Optional[Plate] = None,
    update_volume_trackers: bool = True,
  ) -> EchoSurveyRunResult:
    """Run the Echo source-plate survey workflow without changing access state."""
    return await self.driver.survey_source_plate(
      plate_map,
      survey,
      fetch_saved_data=fetch_saved_data,
      dry_after=dry_after,
      dry=dry,
      source_plate=source_plate,
      update_volume_trackers=update_volume_trackers,
    )

  def build_transfer_plan(
    self,
    source_plate: Plate,
    destination_plate: Plate,
    transfers: Sequence[
      Union[EchoPlannedTransfer, Tuple[Union[str, Well], Union[str, Well], float]]
    ],
    *,
    source_plate_type: Optional[str] = None,
    destination_plate_type: Optional[str] = None,
    protocol_name: str = "transfer",
    volume_unit: str = "nL",
  ) -> EchoTransferPlan:
    """Build Echo protocol XML and source plate map from PLR resources."""
    return self.driver.build_transfer_plan(
      source_plate,
      destination_plate,
      transfers,
      source_plate_type=source_plate_type,
      destination_plate_type=destination_plate_type,
      protocol_name=protocol_name,
      volume_unit=volume_unit,
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

  @need_setup_finished
  async def transfer_wells(
    self,
    source_plate: Plate,
    destination_plate: Plate,
    transfers: Sequence[
      Union[EchoPlannedTransfer, Tuple[Union[str, Well], Union[str, Well], float]]
    ],
    *,
    source_plate_type: Optional[str] = None,
    destination_plate_type: Optional[str] = None,
    protocol_name: str = "transfer",
    volume_unit: str = "nL",
    do_survey: bool = True,
    close_door_before_transfer: bool = True,
    print_options: Optional[EchoTransferPrintOptions] = None,
    timeout: Optional[float] = None,
    survey_timeout: Optional[float] = None,
    update_volume_trackers: bool = True,
  ) -> EchoTransferResult:
    """Plan and execute Echo transfers from PLR plate wells."""
    return await self.driver.transfer_wells(
      source_plate,
      destination_plate,
      transfers,
      source_plate_type=source_plate_type,
      destination_plate_type=destination_plate_type,
      protocol_name=protocol_name,
      volume_unit=volume_unit,
      do_survey=do_survey,
      close_door_before_transfer=close_door_before_transfer,
      print_options=print_options,
      timeout=timeout,
      survey_timeout=survey_timeout,
      update_volume_trackers=update_volume_trackers,
    )

  @need_setup_finished
  async def transfer(
    self,
    transfers: Sequence[EchoTransferInput],
    *,
    source_plate_type: Optional[str] = None,
    destination_plate_type: Optional[str] = None,
    protocol_name: str = "transfer",
    volume_unit: str = "nL",
    do_survey: bool = True,
    close_door_before_transfer: bool = True,
    print_options: Optional[EchoTransferPrintOptions] = None,
    timeout: Optional[float] = None,
    survey_timeout: Optional[float] = None,
    update_volume_trackers: bool = True,
  ) -> EchoTransferResult:
    """Plan and execute Echo transfers from PLR wells, inferring plates from well parents."""
    return await self.driver.transfer(
      transfers,
      source_plate_type=source_plate_type,
      destination_plate_type=destination_plate_type,
      protocol_name=protocol_name,
      volume_unit=volume_unit,
      do_survey=do_survey,
      close_door_before_transfer=close_door_before_transfer,
      print_options=print_options,
      timeout=timeout,
      survey_timeout=survey_timeout,
      update_volume_trackers=update_volume_trackers,
    )

  @need_setup_finished
  async def load_source_plate(
    self,
    plate_type: str,
    *,
    barcode_location: str = "Right-Side",
    barcode: str = "",
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = True,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    """Run the source plate load workflow."""
    return await self.driver.load_source_plate(
      plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      operator_pause=operator_pause,
      open_door_first=open_door_first,
      present_timeout=present_timeout,
      retract_timeout=retract_timeout,
    )

  @need_setup_finished
  async def load_destination_plate(
    self,
    plate_type: str,
    *,
    barcode_location: str = "Right-Side",
    barcode: str = "",
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = True,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    """Run the destination plate load workflow."""
    return await self.driver.load_destination_plate(
      plate_type,
      barcode_location=barcode_location,
      barcode=barcode,
      operator_pause=operator_pause,
      open_door_first=open_door_first,
      present_timeout=present_timeout,
      retract_timeout=retract_timeout,
    )

  @need_setup_finished
  async def eject_source_plate(
    self,
    *,
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = False,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    """Run the source plate eject workflow."""
    return await self.driver.eject_source_plate(
      operator_pause=operator_pause,
      open_door_first=open_door_first,
      present_timeout=present_timeout,
      retract_timeout=retract_timeout,
    )

  @need_setup_finished
  async def eject_destination_plate(
    self,
    *,
    operator_pause: Optional[OperatorPause] = None,
    open_door_first: bool = False,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> EchoPlateWorkflowResult:
    """Run the destination plate eject workflow."""
    return await self.driver.eject_destination_plate(
      operator_pause=operator_pause,
      open_door_first=open_door_first,
      present_timeout=present_timeout,
      retract_timeout=retract_timeout,
    )

  @need_setup_finished
  async def eject_all_plates(
    self,
    *,
    operator_pause: Optional[OperatorPause] = None,
    close_door_after: bool = True,
    open_door_first: bool = False,
    present_timeout: Optional[float] = None,
    retract_timeout: Optional[float] = None,
  ) -> Tuple[EchoPlateWorkflowResult, EchoPlateWorkflowResult]:
    """Eject source, then destination, and optionally close the door."""
    return await self.driver.eject_all_plates(
      operator_pause=operator_pause,
      close_door_after=close_door_after,
      open_door_first=open_door_first,
      present_timeout=present_timeout,
      retract_timeout=retract_timeout,
    )

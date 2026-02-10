import asyncio
import base64
import hashlib
import io
import json
import logging
import struct
import time
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Tuple

import grpc

from pylabrobot.plate_reading.backend import ImagerBackend
from pylabrobot.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources.plate import Plate

try:
  import numpy as np
except ImportError:
  np = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protobuf wire-format helpers (hand-rolled, no grpc_tools dependency)
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
  parts = bytearray()
  while value > 0x7F:
    parts.append((value & 0x7F) | 0x80)
    value >>= 7
  parts.append(value & 0x7F)
  return bytes(parts)


def _encode_signed_varint(value: int) -> bytes:
  if value < 0:
    value = (1 << 64) + value
  return _encode_varint(value)


def _length_delimited(field_number: int, data: bytes) -> bytes:
  tag = _encode_varint((field_number << 3) | 2)
  return tag + _encode_varint(len(data)) + data


def _varint_field(field_number: int, value: int) -> bytes:
  tag = _encode_varint((field_number << 3) | 0)
  return tag + _encode_signed_varint(value)


# ---------------------------------------------------------------------------
# Protobuf decoder
# ---------------------------------------------------------------------------

_WIRE_VARINT = 0
_WIRE_64BIT = 1
_WIRE_LENGTH_DELIMITED = 2
_WIRE_32BIT = 5


def _decode_varint(data: bytes, pos: int) -> Tuple[int, int]:
  result = 0
  shift = 0
  while True:
    b = data[pos]
    result |= (b & 0x7F) << shift
    pos += 1
    if not (b & 0x80):
      break
    shift += 7
  return result, pos


def _decode_fields(data: bytes) -> Dict[int, List[Tuple[int, Any]]]:
  fields: Dict[int, List[Tuple[int, Any]]] = defaultdict(list)
  pos = 0
  while pos < len(data):
    tag, pos = _decode_varint(data, pos)
    field_number = tag >> 3
    wire_type = tag & 0x07
    if wire_type == _WIRE_VARINT:
      value, pos = _decode_varint(data, pos)
      fields[field_number].append((wire_type, value))
    elif wire_type == _WIRE_LENGTH_DELIMITED:
      length, pos = _decode_varint(data, pos)
      value = data[pos : pos + length]
      pos += length
      fields[field_number].append((wire_type, value))
    elif wire_type == _WIRE_64BIT:
      value = data[pos : pos + 8]
      pos += 8
      fields[field_number].append((wire_type, value))
    elif wire_type == _WIRE_32BIT:
      value = data[pos : pos + 4]
      pos += 4
      fields[field_number].append((wire_type, value))
    else:
      break
  return dict(fields)


def _get_field_bytes(fields: dict, field_number: int) -> Optional[bytes]:
  entries = fields.get(field_number, [])
  for wire_type, value in entries:
    if wire_type == _WIRE_LENGTH_DELIMITED:
      return value
  return None


def _get_field_varint(fields: dict, field_number: int) -> Optional[int]:
  entries = fields.get(field_number, [])
  for wire_type, value in entries:
    if wire_type == _WIRE_VARINT:
      return value
  return None


def _varint_as_signed(value: int) -> int:
  if value > 0x7FFFFFFFFFFFFFFF:
    return value - (1 << 64)
  return value


def _extract_proto_strings(data: bytes) -> List[str]:
  """Recursively extract all string-like fields from a protobuf message."""
  strings = []
  try:
    fields = _decode_fields(data)
    for entries in fields.values():
      for wire_type, value in entries:
        if wire_type == _WIRE_LENGTH_DELIMITED:
          try:
            s = value.decode("utf-8")
            if s.isprintable() and len(s) > 0:
              strings.append(s)
          except UnicodeDecodeError:
            pass
          strings.extend(_extract_proto_strings(value))
  except Exception:
    pass
  return strings


def _decode_grpc_error(error: grpc.RpcError) -> str:
  """Decode a SiLA gRPC error into a human-readable string.

  SiLA error details are base64-encoded protobuf in the gRPC details field.
  """
  details = error.details() if hasattr(error, "details") else str(error)
  if not details:
    return str(error)

  try:
    raw = base64.b64decode(details)
    strings = _extract_proto_strings(raw)
    if strings:
      return ": ".join(strings)
  except Exception:
    pass

  return details


# ---------------------------------------------------------------------------
# SiLA 2 standard wrapper types
# ---------------------------------------------------------------------------


def _sila_string(value: str) -> bytes:
  return _length_delimited(1, value.encode("utf-8"))


def _sila_integer(value: int) -> bytes:
  return _varint_field(1, value)


# ---------------------------------------------------------------------------
# SiLA message encoders / decoders
# ---------------------------------------------------------------------------


def _lock_server_params(lock_id: str, timeout_seconds: int = 60) -> bytes:
  return _length_delimited(1, _sila_string(lock_id)) + _length_delimited(
    2, _sila_integer(timeout_seconds)
  )


def _unlock_server_params(lock_id: str) -> bytes:
  return _length_delimited(1, _sila_string(lock_id))


def _metadata_lock_identifier(lock_id: str) -> bytes:
  return _length_delimited(1, _sila_string(lock_id))


def _command_execution_uuid(uuid_str: str) -> bytes:
  return _length_delimited(1, uuid_str.encode("utf-8"))


def _snap_images_params(labware_json: str, snap_json: str) -> bytes:
  return _length_delimited(1, _sila_string(labware_json)) + _length_delimited(
    2, _sila_string(snap_json)
  )


def _decode_sila_string_response(data: bytes) -> str:
  """Decode a response containing a single SiLA String field (field 1)."""
  fields = _decode_fields(data)
  sila_str_msg = _get_field_bytes(fields, 1)
  if sila_str_msg is None:
    raise ValueError("No SiLA String in response")
  inner_fields = _decode_fields(sila_str_msg)
  value = _get_field_bytes(inner_fields, 1)
  if value is None:
    raise ValueError("No value in SiLA String")
  return value.decode("utf-8")


def _decode_command_confirmation(data: bytes) -> str:
  return _decode_sila_string_response(data)


def _decode_intermediate_response(data: bytes) -> Tuple[bytes, Dict[str, int]]:
  fields = _decode_fields(data)

  binary_msg = _get_field_bytes(fields, 1)
  if binary_msg is None:
    raise ValueError("No IntermediateImageData in intermediate response")
  binary_fields = _decode_fields(binary_msg)
  chunk_data = _get_field_bytes(binary_fields, 1)
  if chunk_data is None:
    chunk_data = b""

  snap_event_msg = _get_field_bytes(fields, 2)
  if snap_event_msg is None:
    raise ValueError("No SnapEventData in intermediate response")
  snap_event_fields = _decode_fields(snap_event_msg)

  metadata_struct_msg = _get_field_bytes(snap_event_fields, 1)
  if metadata_struct_msg is None:
    raise ValueError("No LargeBinaryPacketMetadata in SnapEventData")
  meta_fields = _decode_fields(metadata_struct_msg)

  def _extract_integer(field_num: int) -> int:
    integer_msg = _get_field_bytes(meta_fields, field_num)
    if integer_msg is None:
      return 0
    int_fields = _decode_fields(integer_msg)
    val = _get_field_varint(int_fields, 1)
    return _varint_as_signed(val) if val is not None else 0

  metadata = {
    "blob_index": _extract_integer(1),
    "blob_checksum": _extract_integer(2),
    "packet_count": _extract_integer(3),
    "packet_index": _extract_integer(4),
  }

  return chunk_data, metadata


# ---------------------------------------------------------------------------
# Default JSON parameter builders
# ---------------------------------------------------------------------------


def _default_labware_params() -> dict:
  return {
    "LabwareType": 1,
    "LabwareDimensions": {
      "ncavities": 96,
      "nrows": 8,
      "columns": 12,
      "labware_length": 127.6,
      "labware_width": 85.75,
      "labware_height": 13.83,
      "dist2firstcol": 14.3,
      "dist2firstrow": 11.36,
      "row_distance": 9.0,
      "well2well_dist_col": 9.0,
      "bottom_elevation": 11.93,
      "bottom_thickness": 0.17,
      "bottom_clearance": 1.73,
      "bottom_length": 6.8,
      "bottom_width": 6.8,
      "round_bottom": False,
      "volume": 350.0,
    },
    "RefractionIndex": 1.0,
  }


def _default_snap_params(
  well_row: int = 0,
  well_col: int = 0,
  exposure_us: int = 10000,
  light_channel: int = 0,
  filter_cube: str = "DAPI",
  excitation_source: str = "GUV3809",
  objective: str = "PL FLUOTAR 4x/0.13",
) -> dict:
  return {
    "imagesChannelParameters": [
      {
        "focusOffsetNm": 0,
        "imageSize": {"X": 2008, "Y": 2008},
        "exposureTimeUs": exposure_us,
        "illuminationConfig": {
          "filterCubeId": filter_cube,
          "excitationSourceId": excitation_source,
          "lightChannel": light_channel,
        },
        "imageDataFormat": {
          "ImageFormat": "Tiff",
          "BPPLayout": {
            "TotBPP": 16,
            "SigBPP": 12,
            "ShiftToMSB": True,
          },
        },
        "objectiveId": objective,
        "doAutoExposure": False,
        "autoFocusRange": "normal",
        "softwareAutofocusRange": "none",
        "hardwareAutofocusSearch": "plateBottom",
      }
    ],
    "capturePosition": {
      "cavityCoordinatesIndexXy": {"Item1": well_col, "Item2": well_row},
      "siteCoordinatesNmXy": {"Item1": 0, "Item2": 0},
    },
    "skipAutofocus": True,
    "localTimeReferencePosixMSec": int(time.time() * 1000),
    "storageId": 0,
    "focusSettings": {
      "autofocusLocation": "fixedFocus",
      "baseZPositionUm": 0.0,
    },
  }


# ---------------------------------------------------------------------------
# Image extraction helpers
# ---------------------------------------------------------------------------


def _get(d: dict, *keys):
  for k in keys:
    if k in d:
      return d[k]
  return None


def _extract_image_buffer(snap_event: dict) -> Optional[bytes]:
  image_data = _get(snap_event, "imageData", "ImageData")
  if image_data is None:
    return None
  image_buffer = _get(image_data, "imageBuffer", "ImageBuffer")
  if image_buffer is None:
    return None
  if isinstance(image_buffer, str):
    return base64.b64decode(image_buffer)
  if isinstance(image_buffer, list):
    return bytes(image_buffer)
  return None


def _get_image_info(snap_event: dict) -> dict:
  image_data = _get(snap_event, "imageData", "ImageData") or {}
  buffer_info = _get(image_data, "imageBufferInfo", "ImageBufferInfo") or {}
  captured_info = _get(image_data, "capturedImageInfo", "CapturedImageInfo") or {}
  return {
    "width": _get(buffer_info, "width", "Width") or 0,
    "height": _get(buffer_info, "height", "Height") or 0,
    "stride": _get(buffer_info, "stride", "Stride") or 0,
    "pixel_format": _get(buffer_info, "pixelFormat", "PixelFormat") or "",
    "exposure_time_us": _get(captured_info, "exposureTimeUs", "ExposureTimeUs") or 0,
    "z_focus_offset_um": _get(snap_event, "zFocusOffsetUm", "ZFocusOffsetUm") or 0,
    "no_image_data": _get(snap_event, "noImageData", "NoImageData") or False,
  }


def _buffer_to_ndarray(image_buffer: bytes, width: int, height: int):
  if np is None:
    raise ImportError("numpy is required for PicoBackend")

  if len(image_buffer) >= 4 and image_buffer[:2] in (b"II", b"MM"):
    try:
      from PIL import Image as PILImage

      img = PILImage.open(io.BytesIO(image_buffer))
      return np.array(img)
    except ImportError:
      logger.warning("PIL not available, attempting raw decode of TIFF buffer")

  expected_16bit = width * height * 2
  if width > 0 and height > 0 and len(image_buffer) >= expected_16bit:
    return np.frombuffer(image_buffer[:expected_16bit], dtype=np.uint16).reshape(height, width)

  expected_8bit = width * height
  if width > 0 and height > 0 and len(image_buffer) >= expected_8bit:
    return np.frombuffer(image_buffer[:expected_8bit], dtype=np.uint8).reshape(height, width)

  raise ValueError(
    f"Cannot decode image buffer: {len(image_buffer)} bytes, " f"expected {width}x{height} pixels"
  )


# ---------------------------------------------------------------------------
# gRPC service paths
# ---------------------------------------------------------------------------

_LOCK_SVC = "sila2.org.silastandard.core.lockcontroller.v1.LockController"
_INST_SVC = "sila2.moldev.com.instruments.instrumentcontroller.v1.InstrumentController"
_SNAP_SVC = "sila2.moldev.com.instruments.snapcontroller.v1.SnapController"
_HW_SVC = "sila2.moldev.com.instruments.hardwarecontroller.v1.HardwareController"
_OBJ_SVC = "sila2.moldev.com.instruments.objectivescontroller.v1.ObjectivesController"
_FC_SVC = "sila2.moldev.com.instruments.filtercubescontroller.v1.FilterCubesController"
_LOCK_META_KEY = "sila-org.silastandard-core-lockcontroller-v1-metadata-lockidentifier-bin"

# Mapping from ImagingMode to (light_channel, filter_cube, excitation_source)
_IMAGING_MODE_MAP: Dict[ImagingMode, Tuple[int, str, str]] = {
  ImagingMode.BRIGHTFIELD: (5, "", "5069278"),
  ImagingMode.DAPI: (0, "DAPI", "GUV3809"),
  ImagingMode.GFP: (0, "FITC", "5050029"),
  ImagingMode.RFP: (0, "TRITC", "5050028"),
  ImagingMode.TEXAS_RED: (0, "TxRed", "5050028"),
  ImagingMode.CY5: (0, "Cy5", "5050028"),
}

# Mapping from Objective enum to Pico objective ID strings
_OBJECTIVE_MAP: Dict[Objective, str] = {
  Objective.O_2_5X_N_PLAN: "N PLAN 2.5x/0.07",  # Pico
  Objective.O_4X_PL_FL: "PL FLUOTAR 4x/0.13",
  Objective.O_10X_PL_FL: "PL FLUOTAR 10x/0.30",
  Objective.O_20X_PL_FL: "PL FLUOTAR 20x/0.40",
  Objective.O_40X_PL_FL: "PL FLUOTAR 40x/0.60",
}


class PicoBackend(ImagerBackend):
  """Backend for Molecular Devices ImageXpress Pico automated microscope.

  Communicates with the instrument via SiLA 2 over gRPC. All services (imaging,
  door control, instrument control) run on a single port and share one lock.

  Args:
    host: IP address or hostname of the instrument.
    port: gRPC port (default 8091).
    lock_timeout: Instrument lock timeout in seconds. The lock auto-releases if
      no commands are sent within this period.
    labware_params: Pico labware parameters dict. If ``None``, defaults for a
      96-well glass-bottom plate (CellVis P96-1.5H-N) are used.
    objectives: Mapping from 0-indexed turret position to :class:`Objective`.
      Applied during :meth:`setup` via :meth:`change_objective`. Not all
      positions need to be specified.
    filter_cubes: Mapping from 0-indexed filter wheel position to
      :class:`ImagingMode`. The filter cube for that mode is installed at
      the given position. Applied during :meth:`setup` via
      :meth:`change_filter_cube`. Not all positions need to be specified.
  """

  def __init__(
    self,
    host: str,
    port: int = 8091,
    lock_timeout: int = 3600,
    labware_params: Optional[dict] = None,
    objectives: Optional[Dict[int, Objective]] = None,
    filter_cubes: Optional[Dict[int, ImagingMode]] = None,
  ):
    super().__init__()
    self._host = host
    self._port = port
    self._lock_timeout = lock_timeout
    self._labware_params = labware_params or _default_labware_params()

    for pos, obj in (objectives or {}).items():
      if obj not in _OBJECTIVE_MAP:
        raise ValueError(
          f"Objective {obj} not supported by Pico. " f"Supported: {list(_OBJECTIVE_MAP.keys())}"
        )
    for pos, mode in (filter_cubes or {}).items():
      if mode not in _IMAGING_MODE_MAP:
        raise ValueError(
          f"Imaging mode {mode} not supported by Pico. "
          f"Supported: {list(_IMAGING_MODE_MAP.keys())}"
        )

    self._objectives: Dict[int, Objective] = objectives or {}
    self._filter_cubes: Dict[int, ImagingMode] = filter_cubes or {}

    self._channel: Optional[grpc.Channel] = None
    self._lock_id: Optional[str] = None
    self._locked = False
    self._door_open = False

  # -- gRPC helpers --

  def _lock_metadata(self) -> List[Tuple[str, bytes]]:
    assert self._lock_id is not None
    return [(_LOCK_META_KEY, _metadata_lock_identifier(self._lock_id))]

  def _call(
    self,
    service: str,
    method: str,
    request: bytes = b"",
    with_lock: bool = False,
    timeout: float = 30.0,
  ) -> bytes:
    assert self._channel is not None
    metadata = self._lock_metadata() if with_lock else None
    rpc = self._channel.unary_unary(
      f"/{service}/{method}",
      request_serializer=lambda x: x,
      response_deserializer=lambda x: x,
    )
    try:
      return rpc(request, metadata=metadata, timeout=timeout)
    except grpc.RpcError as e:
      raise RuntimeError(f"{service}/{method}: {_decode_grpc_error(e)}") from e

  def _stream(
    self,
    service: str,
    method: str,
    request: bytes = b"",
    with_lock: bool = False,
    timeout: float = 300.0,
  ) -> Iterator[bytes]:
    assert self._channel is not None
    metadata = self._lock_metadata() if with_lock else None
    rpc = self._channel.unary_stream(
      f"/{service}/{method}",
      request_serializer=lambda x: x,
      response_deserializer=lambda x: x,
    )
    try:
      return rpc(request, metadata=metadata, timeout=timeout)
    except grpc.RpcError as e:
      raise RuntimeError(f"{service}/{method}: {_decode_grpc_error(e)}") from e

  def _lock(self) -> None:
    self._call(_LOCK_SVC, "LockServer", _lock_server_params(self._lock_id, self._lock_timeout))
    self._locked = True

  def _unlock(self) -> None:
    self._call(_LOCK_SVC, "UnlockServer", _unlock_server_params(self._lock_id))
    self._locked = False

  def _initialize(self) -> None:
    self._call(_INST_SVC, "Initialize", b"", with_lock=True)

  async def _get_installed_objectives(self) -> List[dict]:
    raw = await asyncio.to_thread(self._call, _OBJ_SVC, "Get_InstalledObjectives", b"")
    data = json.loads(_decode_sila_string_response(raw))
    return data.get("objectivesData", [])

  async def _get_installed_filter_cubes(self) -> List[dict]:
    raw = await asyncio.to_thread(self._call, _FC_SVC, "Get_InstalledFilterCubes", b"")
    data = json.loads(_decode_sila_string_response(raw))
    return data.get("filterCubesData", [])

  # -- lifecycle --

  async def setup(self) -> None:
    self._channel = grpc.insecure_channel(
      f"{self._host}:{self._port}",
      options=[
        ("grpc.keepalive_time_ms", 10000),
        ("grpc.max_receive_message_length", 64 * 1024 * 1024),
      ],
    )
    self._lock_id = "pylabrobot"

    # Try to unlock a stale lock from a previous session that didn't clean up.
    try:
      await asyncio.to_thread(self._unlock)
    except (grpc.RpcError, RuntimeError):
      pass

    await asyncio.to_thread(self._lock)

    installed_obj = await self._get_installed_objectives()
    num_obj = len(installed_obj)
    for pos, obj in self._objectives.items():
      if pos >= num_obj:
        raise ValueError(
          f"Objective position {pos} out of range (instrument has {num_obj} positions)"
        )
      await self.change_objective(pos, _OBJECTIVE_MAP[obj])

    installed_fc = await self._get_installed_filter_cubes()
    num_fc = len(installed_fc)
    for pos, mode in self._filter_cubes.items():
      if pos >= num_fc:
        raise ValueError(
          f"Filter cube position {pos} out of range (instrument has {num_fc} positions)"
        )
      await self.change_filter_cube(pos, _IMAGING_MODE_MAP[mode][1])

    logger.info("PicoBackend: connected to %s:%d", self._host, self._port)

  async def stop(self) -> None:
    if self._channel is not None:
      if self._locked:
        try:
          await asyncio.to_thread(self._unlock)
        except (grpc.RpcError, RuntimeError) as e:
          logger.warning("PicoBackend: unlock failed during stop: %s", e)
      self._channel.close()
      self._channel = None
    logger.info("PicoBackend: stopped")

  # -- configuration --

  async def get_configuration(self) -> dict:
    """Query the full instrument configuration (objectives, filter cubes, etc.).

    Returns the parsed InstrumentConfiguration JSON from the instrument. Key fields:
      - ``objectivesComponent.objectives``: list of installed objectives, each with
        ``Id``, ``Description``, ``PositionLabel``, ``Magnification``, ``NumericalAperture``
      - ``filterCubesComponent.filterCubes``: list of installed filter cubes, each with
        ``Id``, ``Description``, ``PositionLabel``
      - ``excitationSources``: list of excitation sources with ``Id``
    """
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    raw = await asyncio.to_thread(self._call, _INST_SVC, "Get_InstrumentConfiguration", b"")
    data = json.loads(_decode_sila_string_response(raw))
    return data.get("InstrumentConfiguration", data)

  # -- door --

  @property
  def door_open(self) -> bool:
    """Whether the plate drawer is currently open (tracked client-side)."""
    return self._door_open

  async def open_door(self) -> None:
    """Open the plate drawer."""
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    await asyncio.to_thread(self._initialize)
    await asyncio.to_thread(self._call, _HW_SVC, "OpenPlateDrawer", b"", True)
    self._door_open = True

  async def close_door(self) -> None:
    """Close the plate drawer."""
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    await asyncio.to_thread(self._initialize)
    await asyncio.to_thread(self._call, _HW_SVC, "ClosePlateDrawer", b"", True)
    self._door_open = False

  # -- objective maintenance --

  async def enter_objective_maintenance(self, position: int) -> None:
    """Open the objective door for swapping objectives.

    Args:
      position: 0-indexed objective turret position.
    """
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    if self._door_open:
      raise RuntimeError("Cannot enter objective maintenance while the plate drawer is open.")
    params = json.dumps({"Index": position})
    req = _length_delimited(1, _sila_string(params))
    await asyncio.to_thread(self._initialize)
    await asyncio.to_thread(self._call, _OBJ_SVC, "EnterObjectiveMaintenance", req, True)

  async def exit_objective_maintenance(self) -> None:
    """Close the objective door after swapping objectives."""
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    await asyncio.to_thread(self._call, _OBJ_SVC, "ExitObjectiveMaintenance", b"", True)

  async def get_available_objectives(self, position: int) -> List[dict]:
    """Query which objectives are compatible with a given turret position.

    Args:
      position: 0-indexed turret position.

    Returns:
      List of objective dicts, each with ``Id``, ``Description``, ``Magnification``,
      ``NumericalAperture``, ``PositionLabel``, ``IsCalibrated``, etc.
    """
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    params = json.dumps({"Index": position})
    req = _length_delimited(1, _sila_string(params))
    raw = await asyncio.to_thread(
      self._call, _OBJ_SVC, "GetAvailableObjectivesForPosition", req, True
    )
    data = json.loads(_decode_sila_string_response(raw))
    return data.get("objectives", data.get("Objectives", []))

  async def get_available_filter_cubes(self) -> List[dict]:
    """Query which filter cubes are compatible with this instrument.

    Returns:
      List of filter cube dicts, each with ``Id``, ``Description``,
      ``PositionLabel``, ``IsCalibrated``, ``EmissionFilterPassBands``,
      ``ExcitationFilterPassBands``, etc.
    """
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    raw = await asyncio.to_thread(self._call, _FC_SVC, "Get_CompatibleFilterCubes", b"")
    data = json.loads(_decode_sila_string_response(raw))
    return data.get("filterCubes", data.get("FilterCubes", []))

  async def change_objective(self, position: int, objective_id: str) -> None:
    """Register a new objective in a turret position.

    Call this after physically swapping an objective (during maintenance mode)
    to update the instrument's configuration.

    Args:
      position: 0-indexed turret position.
      objective_id: Objective ID string (e.g. ``"PL FLUOTAR 4x/0.13"``).
        Use :meth:`get_available_objectives` to list valid IDs for a position.

    Raises:
      ValueError: If ``objective_id`` is not compatible with the given position.
    """
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    available = await self.get_available_objectives(position)
    valid_ids = [obj.get("Id", obj.get("id")) for obj in available]
    if objective_id not in valid_ids:
      raise ValueError(
        f"Objective {objective_id!r} is not compatible with position {position}. "
        f"Valid IDs: {valid_ids}"
      )
    params = json.dumps({"Id": objective_id, "Index": position})
    req = _length_delimited(1, _sila_string(params))
    await asyncio.to_thread(self._call, _OBJ_SVC, "ChangeHardware", req, True)

  async def change_filter_cube(self, position: int, filter_cube_id: str) -> None:
    """Register a new filter cube in a filter wheel position.

    Call this after physically swapping a filter cube to update the
    instrument's configuration.

    Args:
      position: 0-indexed filter wheel position.
      filter_cube_id: Filter cube ID string (e.g. ``"FITC"``).
        Use :meth:`get_available_filter_cubes` to list valid IDs.

    Raises:
      ValueError: If ``filter_cube_id`` is not a compatible filter cube.
    """
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    available = await self.get_available_filter_cubes()
    valid_ids = [fc.get("Id", fc.get("id")) for fc in available]
    if filter_cube_id not in valid_ids:
      raise ValueError(
        f"Filter cube {filter_cube_id!r} is not compatible with this instrument. "
        f"Valid IDs: {valid_ids}"
      )
    params = json.dumps({"Id": filter_cube_id, "Index": position})
    req = _length_delimited(1, _sila_string(params))
    await asyncio.to_thread(self._call, _FC_SVC, "ChangeHardware", req, True)

  # -- imaging --

  def _snap_images(self, labware_params: dict, snap_params: dict) -> List[dict]:
    """Acquire images. Runs the SiLA 2 Observable Command flow synchronously."""
    labware_json = json.dumps(labware_params)
    snap_json = json.dumps(snap_params)

    self._initialize()

    # Step 1: launch SnapImages command
    request = _snap_images_params(labware_json, snap_json)
    confirmation_raw = self._call(_SNAP_SVC, "SnapImages", request, with_lock=True, timeout=60.0)
    exec_uuid = _decode_command_confirmation(confirmation_raw)
    logger.debug("SnapImages exec UUID: %s", exec_uuid[:8])

    # Step 2: stream intermediate responses (chunked image data)
    uuid_request = _command_execution_uuid(exec_uuid)
    chunks: Dict[int, Dict[int, bytes]] = defaultdict(dict)
    checksums: Dict[int, int] = {}

    for response_raw in self._stream(
      _SNAP_SVC,
      "SnapImages_Intermediate",
      uuid_request,
      with_lock=True,
      timeout=300.0,
    ):
      chunk_data, meta = _decode_intermediate_response(response_raw)
      chunks[meta["blob_index"]][meta["packet_index"]] = chunk_data
      checksums[meta["blob_index"]] = meta["blob_checksum"]

    # Step 3: get result (signals command completion)
    self._call(_SNAP_SVC, "SnapImages_Result", uuid_request, with_lock=True, timeout=60.0)

    # Step 4: reassemble blobs and verify checksums
    images = []
    for blob_idx in sorted(chunks.keys()):
      blob_chunks = chunks[blob_idx]
      reassembled = b"".join(blob_chunks[k] for k in sorted(blob_chunks.keys()))

      md5_digest = hashlib.md5(reassembled).digest()
      computed = struct.unpack("<q", md5_digest[:8])[0]
      expected = checksums[blob_idx]
      if computed != expected:
        logger.warning(
          "Blob %d: checksum mismatch (computed=%d, expected=%d)", blob_idx, computed, expected
        )

      try:
        images.append(json.loads(reassembled.decode("utf-8")))
      except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.error("Blob %d: failed to decode JSON: %s", blob_idx, e)

    logger.debug("Acquired %d image(s)", len(images))
    return images

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
  ) -> ImagingResult:
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")

    if mode not in _IMAGING_MODE_MAP:
      raise ValueError(
        f"Unsupported imaging mode {mode} for Pico. " f"Supported: {list(_IMAGING_MODE_MAP.keys())}"
      )
    if objective not in _OBJECTIVE_MAP:
      raise ValueError(
        f"Unsupported objective {objective} for Pico. " f"Supported: {list(_OBJECTIVE_MAP.keys())}"
      )

    light_channel, filter_cube, excitation_source = _IMAGING_MODE_MAP[mode]
    objective_str = _OBJECTIVE_MAP[objective]

    if objective not in self._objectives.values():
      raise ValueError(
        f"Objective {objective!r} is not configured. "
        f"Configured objectives: {dict(self._objectives)}"
      )
    if filter_cube and mode not in self._filter_cubes.values():
      raise ValueError(
        f"Imaging mode {mode!r} is not configured. "
        f"Configured filter cubes: {dict(self._filter_cubes)}"
      )

    # Convert exposure: PLR uses ms, Pico uses microseconds
    if isinstance(exposure_time, (int, float)):
      exposure_us = int(exposure_time * 1000)
      do_auto_exposure = False
    else:
      exposure_us = 10000
      do_auto_exposure = True

    # Convert focal height: PLR uses mm, Pico uses um
    if isinstance(focal_height, (int, float)):
      base_z_um = focal_height * 1000.0
      skip_autofocus = True
    else:
      base_z_um = 0.0
      skip_autofocus = False

    snap_params = _default_snap_params(
      well_row=row,
      well_col=column,
      exposure_us=exposure_us,
      light_channel=light_channel,
      filter_cube=filter_cube,
      excitation_source=excitation_source,
      objective=objective_str,
    )
    snap_params["imagesChannelParameters"][0]["doAutoExposure"] = do_auto_exposure
    snap_params["skipAutofocus"] = skip_autofocus
    snap_params["focusSettings"]["baseZPositionUm"] = base_z_um

    images = await asyncio.to_thread(self._snap_images, self._labware_params, snap_params)

    result_images: List = []
    actual_exposure_us = exposure_us
    for snap_event in images:
      image_buffer = _extract_image_buffer(snap_event)
      if image_buffer is None:
        info = _get_image_info(snap_event)
        if info.get("no_image_data"):
          logger.debug("Skipping autofocus-only frame")
        else:
          logger.warning("Could not extract image buffer from snap event")
        continue

      info = _get_image_info(snap_event)
      actual_exposure_us = info.get("exposure_time_us", exposure_us)
      arr = _buffer_to_ndarray(image_buffer, info["width"], info["height"])
      result_images.append(arr)

    return ImagingResult(
      images=result_images,
      exposure_time=actual_exposure_us / 1000.0,
      focal_height=focal_height if isinstance(focal_height, (int, float)) else 0.0,
    )

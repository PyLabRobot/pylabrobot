import base64
import hashlib
import io
import json
import logging
import struct
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.sila.grpc import (
  command_execution_uuid,
  decode_command_confirmation,
  decode_fields,
  decode_grpc_error,
  decode_sila_string_response,
  get_field_bytes,
  get_field_varint,
  length_delimited,
  lock_server_params,
  metadata_lock_identifier,
  sila_string,
  unlock_server_params,
  varint_as_signed,
)
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
from pylabrobot.resources.utils import row_index_to_label
from pylabrobot.resources.well import WellBottomType

try:
  import grpc  # type: ignore[import-untyped]

  HAS_GRPC = True
except ImportError as e:
  grpc = None  # type: ignore[assignment]
  HAS_GRPC = False
  _GRPC_IMPORT_ERROR = e

try:
  import numpy as np  # type: ignore[import-not-found]

  HAS_NUMPY = True
except ImportError:
  HAS_NUMPY = False

try:
  from PIL import Image as PILImage  # type: ignore[import-not-found]

  HAS_PIL = True
except ImportError:
  HAS_PIL = False

logger = logging.getLogger(__name__)


def _snap_images_params(labware_json: str, snap_json: str) -> bytes:
  return length_delimited(1, sila_string(labware_json)) + length_delimited(
    2, sila_string(snap_json)
  )


def _decode_intermediate_response(data: bytes) -> Tuple[bytes, Dict[str, int]]:
  fields = decode_fields(data)

  binary_msg = get_field_bytes(fields, 1)
  if binary_msg is None:
    raise ValueError("No IntermediateImageData in intermediate response")
  binary_fields = decode_fields(binary_msg)
  chunk_data = get_field_bytes(binary_fields, 1)
  if chunk_data is None:
    chunk_data = b""

  snap_event_msg = get_field_bytes(fields, 2)
  if snap_event_msg is None:
    raise ValueError("No SnapEventData in intermediate response")
  snap_event_fields = decode_fields(snap_event_msg)

  metadata_struct_msg = get_field_bytes(snap_event_fields, 1)
  if metadata_struct_msg is None:
    raise ValueError("No LargeBinaryPacketMetadata in SnapEventData")
  meta_fields = decode_fields(metadata_struct_msg)

  def _extract_integer(field_num: int) -> int:
    integer_msg = get_field_bytes(meta_fields, field_num)
    if integer_msg is None:
      return 0
    int_fields = decode_fields(integer_msg)
    val = get_field_varint(int_fields, 1)
    return varint_as_signed(val) if val is not None else 0

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


def _labware_params_from_plate(plate: Plate) -> dict:
  """Derive Pico labware JSON from a PLR :class:`Plate`.

  Inspects well positions and geometry so the caller doesn't have to supply a
  hand-crafted dict.
  """
  well_a1 = plate.get_well("A1")
  nrows = plate.num_items_y
  ncols = plate.num_items_x

  col_spacing = plate.item_dx if ncols > 1 else 9.0
  row_spacing = plate.item_dy if nrows > 1 else 9.0

  a1_loc = well_a1.location
  assert a1_loc is not None, "Well A1 must have a location"
  well_size_x = well_a1.get_size_x()
  well_size_y = well_a1.get_size_y()

  # PLR locations are Left-Front-Bottom of the well bounding box; Pico
  # dist2first* are measured to the well center.
  dist2firstcol = a1_loc.x + well_size_x / 2.0

  last_row_label = row_index_to_label(nrows - 1)
  last_row_well = plate.get_well(f"{last_row_label}1")
  last_row_loc = last_row_well.location
  assert last_row_loc is not None, f"Well {last_row_label}1 must have a location"
  dist2firstrow = last_row_loc.y + well_size_y / 2.0

  bottom_length = well_size_x
  bottom_width = well_size_y
  volume = well_a1.max_volume or 350.0
  bottom_thickness = well_a1.material_z_thickness
  bottom_elevation = a1_loc.z or 0.0
  round_bottom = well_a1.bottom_type in (WellBottomType.U, WellBottomType.V)

  return {
    "LabwareType": 1,
    "LabwareDimensions": {
      "ncavities": nrows * ncols,
      "nrows": nrows,
      "columns": ncols,
      "labware_length": plate.get_size_x(),
      "labware_width": plate.get_size_y(),
      "labware_height": plate.get_size_z(),
      "dist2firstcol": dist2firstcol,
      "dist2firstrow": dist2firstrow,
      "row_distance": row_spacing,
      "well2well_dist_col": col_spacing,
      "bottom_elevation": bottom_elevation,
      "bottom_thickness": bottom_thickness,
      "bottom_clearance": 1.73,
      "bottom_length": bottom_length,
      "bottom_width": bottom_width,
      "round_bottom": round_bottom,
      "volume": volume,
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
  if not HAS_NUMPY:
    raise ImportError("numpy is required for PicoBackend")

  if len(image_buffer) >= 4 and image_buffer[:2] in (b"II", b"MM"):
    if HAS_PIL:
      img = PILImage.open(io.BytesIO(image_buffer))
      return np.array(img)
    logger.warning("PIL not available, attempting raw decode of TIFF buffer")

  expected_16bit = width * height * 2
  if width > 0 and height > 0 and len(image_buffer) >= expected_16bit:
    return np.frombuffer(image_buffer[:expected_16bit], dtype=np.uint16).reshape(height, width)

  expected_8bit = width * height
  if width > 0 and height > 0 and len(image_buffer) >= expected_8bit:
    return np.frombuffer(image_buffer[:expected_8bit], dtype=np.uint8).reshape(height, width)

  raise ValueError(
    f"Cannot decode image buffer: {len(image_buffer)} bytes, expected {width}x{height} pixels"
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


class ExperimentalPicoBackend(ImagerBackend):
  """Backend for Molecular Devices ImageXpress Pico automated microscope.

  Communicates with the instrument via SiLA 2 over gRPC. All services (imaging,
  door control, instrument control) run on a single port and share one lock.

  Args:
    host: IP address or hostname of the instrument.
    port: gRPC port (default 8091).
    lock_timeout: Instrument lock timeout in seconds. The lock auto-releases if
      no commands are sent within this period.
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
    objectives: Optional[Dict[int, Objective]] = None,
    filter_cubes: Optional[Dict[int, ImagingMode]] = None,
  ):
    super().__init__()
    self._host = host
    self._port = port
    self._lock_timeout = lock_timeout

    for pos, obj in (objectives or {}).items():
      if obj not in _OBJECTIVE_MAP:
        raise ValueError(
          f"Objective {obj} not supported by Pico. Supported: {list(_OBJECTIVE_MAP.keys())}"
        )
    for pos, mode in (filter_cubes or {}).items():
      if mode not in _IMAGING_MODE_MAP:
        raise ValueError(
          f"Imaging mode {mode} not supported by Pico. Supported: {list(_IMAGING_MODE_MAP.keys())}"
        )

    self._objectives: Dict[int, Objective] = objectives or {}
    self._filter_cubes: Dict[int, ImagingMode] = filter_cubes or {}

    self._channel: Optional["grpc.Channel"] = None
    self._lock_id: Optional[str] = None
    self._locked = False
    self._door_open = False

  # -- gRPC helpers --

  @property
  def channel(self) -> "grpc.Channel":
    if self._channel is None:
      raise RuntimeError("Backend not set up. Call setup() first.")
    return self._channel

  def _lock_metadata(self) -> List[Tuple[str, bytes]]:
    assert self._lock_id is not None
    return [(_LOCK_META_KEY, metadata_lock_identifier(self._lock_id))]

  async def _relock(self) -> None:
    """Force-release any stale lock and re-acquire."""
    try:
      await self._unlock()
    except (grpc.RpcError, RuntimeError):
      pass
    await self._lock()

  _T = TypeVar("_T")

  async def _rpc(
    self,
    service: str,
    method: str,
    fn: Callable[[], _T],
    with_lock: bool,
  ) -> _T:
    for attempt in range(2):
      try:
        return await anyio.to_thread.run_sync(fn)
      except grpc.RpcError as e:
        if attempt == 0 and with_lock and "CommandRequiresLock" in decode_grpc_error(e):
          await self._relock()
          continue
        raise RuntimeError(f"{service}/{method}: {decode_grpc_error(e)}") from e
    raise RuntimeError("unreachable")

  async def _call(
    self,
    service: str,
    method: str,
    request: bytes = b"",
    with_lock: bool = False,
    timeout: float = 30.0,
  ) -> bytes:
    channel = self.channel
    metadata = self._lock_metadata() if with_lock else None

    def fn():
      rpc = channel.unary_unary(
        f"/{service}/{method}",
        request_serializer=lambda x: x,
        response_deserializer=lambda x: x,
      )
      return rpc(request, metadata=metadata, timeout=timeout)

    return await self._rpc(service, method, fn, with_lock)

  async def _stream(
    self,
    service: str,
    method: str,
    request: bytes = b"",
    with_lock: bool = False,
    timeout: float = 300.0,
  ) -> List[bytes]:
    channel = self.channel
    metadata = self._lock_metadata() if with_lock else None

    def fn():
      rpc = channel.unary_stream(
        f"/{service}/{method}",
        request_serializer=lambda x: x,
        response_deserializer=lambda x: x,
      )
      return list(rpc(request, metadata=metadata, timeout=timeout))

    return await self._rpc(service, method, fn, with_lock)

  async def _lock(self) -> None:
    assert self._lock_id is not None
    await self._call(_LOCK_SVC, "LockServer", lock_server_params(self._lock_id, self._lock_timeout))
    self._locked = True

  async def _unlock(self) -> None:
    assert self._lock_id is not None
    await self._call(_LOCK_SVC, "UnlockServer", unlock_server_params(self._lock_id))
    self._locked = False

  async def _initialize(self) -> None:
    await self._call(_INST_SVC, "Initialize", b"", with_lock=True)

  async def _get_installed_objectives(self) -> List[dict]:
    raw = await self._call(_OBJ_SVC, "Get_InstalledObjectives", b"")
    data: dict = json.loads(decode_sila_string_response(raw))
    return list(data.get("objectivesData", []))

  async def _get_installed_filter_cubes(self) -> List[dict]:
    raw = await self._call(_FC_SVC, "Get_InstalledFilterCubes", b"")
    data: dict = json.loads(decode_sila_string_response(raw))
    return list(data.get("filterCubesData", []))

  # -- lifecycle --

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)

    if not HAS_GRPC:
      raise RuntimeError(
        f"grpcio is required for the PicoBackend. Import error: {_GRPC_IMPORT_ERROR}"
      )
    # TODO: We really shouldn't use the sync API here, even if we use thread-hopping.
    # There is in fact grcp.aio, which would be a lot cleaner.
    self._channel = stack.enter_context(
      grpc.insecure_channel(
        f"{self._host}:{self._port}",
        options=[
          ("grpc.keepalive_time_ms", 10000),
          ("grpc.max_receive_message_length", 64 * 1024 * 1024),
        ],
      )
    )
    self._lock_id = "pylabrobot"

    async def cleanup():
      if self._locked:
        try:
          await self._unlock()
        except (grpc.RpcError, RuntimeError) as e:
          logger.warning("PicoBackend: unlock failed during stop: %s", e)
      self._channel = None
      logger.info("PicoBackend: stopped")

    stack.push_shielded_async_callback(cleanup)

    # Try to unlock a stale lock from a previous session that didn't clean up.
    try:
      await self._unlock()
    except (grpc.RpcError, RuntimeError):
      pass

    await self._lock()

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

    raw = await self._call(_INST_SVC, "Get_InstrumentConfiguration", b"")
    data: dict = json.loads(decode_sila_string_response(raw))
    return dict(data.get("InstrumentConfiguration", data))

  # -- door --

  @property
  def door_open(self) -> bool:
    """Whether the plate drawer is currently open (tracked client-side)."""
    return self._door_open

  async def open_door(self) -> None:
    """Open the plate drawer."""

    await self._initialize()
    await self._call(_HW_SVC, "OpenPlateDrawer", b"", True)
    self._door_open = True

  async def close_door(self) -> None:
    """Close the plate drawer."""

    await self._initialize()
    await self._call(_HW_SVC, "ClosePlateDrawer", b"", True)
    self._door_open = False

  # -- objective maintenance --

  async def enter_objective_maintenance(self, position: int) -> None:
    """Open the objective door for swapping objectives.

    Args:
      position: 0-indexed objective turret position.
    """

    if self._door_open:
      raise RuntimeError("Cannot enter objective maintenance while the plate drawer is open.")
    params = json.dumps({"Index": position})
    req = length_delimited(1, sila_string(params))
    await self._initialize()
    await self._call(_OBJ_SVC, "EnterObjectiveMaintenance", req, True)

  async def exit_objective_maintenance(self) -> None:
    """Close the objective door after swapping objectives."""

    await self._call(_OBJ_SVC, "ExitObjectiveMaintenance", b"", True)

  async def get_available_objectives(self, position: int) -> List[dict]:
    """Query which objectives are compatible with a given turret position.

    Args:
      position: 0-indexed turret position.

    Returns:
      List of objective dicts, each with ``Id``, ``Description``, ``Magnification``,
      ``NumericalAperture``, ``PositionLabel``, ``IsCalibrated``, etc.
    """

    params = json.dumps({"Index": position})
    req = length_delimited(1, sila_string(params))
    raw = await self._call(_OBJ_SVC, "GetAvailableObjectivesForPosition", req, True)
    data: dict = json.loads(decode_sila_string_response(raw))
    return list(data.get("objectives", data.get("Objectives", [])))

  async def get_available_filter_cubes(self) -> List[dict]:
    """Query which filter cubes are compatible with this instrument.

    Returns:
      List of filter cube dicts, each with ``Id``, ``Description``,
      ``PositionLabel``, ``IsCalibrated``, ``EmissionFilterPassBands``,
      ``ExcitationFilterPassBands``, etc.
    """

    raw = await self._call(_FC_SVC, "Get_CompatibleFilterCubes", b"")
    data: dict = json.loads(decode_sila_string_response(raw))
    return list(data.get("filterCubes", data.get("FilterCubes", [])))

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

    available = await self.get_available_objectives(position)
    valid_ids = [obj.get("Id", obj.get("id")) for obj in available]
    if objective_id not in valid_ids:
      raise ValueError(
        f"Objective {objective_id!r} is not compatible with position {position}. "
        f"Valid IDs: {valid_ids}"
      )
    params = json.dumps({"Id": objective_id, "Index": position})
    req = length_delimited(1, sila_string(params))
    await self._call(_OBJ_SVC, "ChangeHardware", req, True)

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

    available = await self.get_available_filter_cubes()
    valid_ids = [fc.get("Id", fc.get("id")) for fc in available]
    if filter_cube_id not in valid_ids:
      raise ValueError(
        f"Filter cube {filter_cube_id!r} is not compatible with this instrument. "
        f"Valid IDs: {valid_ids}"
      )
    params = json.dumps({"Id": filter_cube_id, "Index": position})
    req = length_delimited(1, sila_string(params))
    await self._call(_FC_SVC, "ChangeHardware", req, True)

  # -- imaging --

  async def _snap_images(self, labware_params: dict, snap_params: dict) -> List[dict]:
    """Acquire images via the SiLA 2 Observable Command flow."""
    labware_json = json.dumps(labware_params)
    snap_json = json.dumps(snap_params)

    await self._initialize()

    # Step 1: launch SnapImages command
    request = _snap_images_params(labware_json, snap_json)
    confirmation_raw = await self._call(
      _SNAP_SVC, "SnapImages", request, with_lock=True, timeout=60.0
    )
    exec_uuid = decode_command_confirmation(confirmation_raw)
    logger.debug("SnapImages exec UUID: %s", exec_uuid[:8])

    # Step 2: stream intermediate responses (chunked image data)
    uuid_request = command_execution_uuid(exec_uuid)
    chunks: Dict[int, Dict[int, bytes]] = defaultdict(dict)
    checksums: Dict[int, int] = {}

    for response_raw in await self._stream(
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
    await self._call(_SNAP_SVC, "SnapImages_Result", uuid_request, with_lock=True, timeout=60.0)

    # Step 4: reassemble blobs and verify checksums
    images = []
    for blob_idx in sorted(chunks.keys()):
      blob_chunks = chunks[blob_idx]
      reassembled = b"".join(blob_chunks[k] for k in sorted(blob_chunks.keys()))

      md5_digest = hashlib.md5(reassembled, usedforsecurity=False).digest()
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
    if mode not in _IMAGING_MODE_MAP:
      raise ValueError(
        f"Unsupported imaging mode {mode} for Pico. Supported: {list(_IMAGING_MODE_MAP.keys())}"
      )
    if objective not in _OBJECTIVE_MAP:
      raise ValueError(
        f"Unsupported objective {objective} for Pico. Supported: {list(_OBJECTIVE_MAP.keys())}"
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

    labware_params = _labware_params_from_plate(plate)
    images = await self._snap_images(labware_params, snap_params)

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

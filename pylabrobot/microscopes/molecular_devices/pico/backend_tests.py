"""Tests for PicoBackend.

Focus: verify the gRPC commands generated and responses decoded for each
high-level method. The mock channel records every (path, request, metadata)
tuple so tests can assert on exact service paths, protobuf payloads, and
lock metadata propagation.
"""

import base64
import hashlib
import json
import struct
import unittest
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest

pytest.importorskip("numpy")
pytest.importorskip("grpc")

import numpy as np  # type: ignore[import-not-found]

from pylabrobot.io.sila.grpc import (
  decode_fields,
  get_field_bytes,
  length_delimited,
  sila_string,
  varint_field,
)
from pylabrobot.microscopes.molecular_devices.pico.backend import (
  _FC_SVC,
  _HW_SVC,
  _INST_SVC,
  _LOCK_META_KEY,
  _LOCK_SVC,
  _OBJ_SVC,
  _SNAP_SVC,
  ExperimentalPicoBackend,
  _decode_intermediate_response,
  _extract_image_buffer,
  _get_image_info,
)
from pylabrobot.plate_reading.standard import ImagingMode, Objective
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType

# ---------------------------------------------------------------------------
# Test plate fixture
# ---------------------------------------------------------------------------


def _test_plate() -> Plate:
  """Create a 96-well plate with known geometry for testing.

  dx/dy are LFB (left-front-bottom of well bounding box).  Pico dist2first*
  values are center-based, so the expected centers are dx + size/2 and
  dy + size/2:  10.9 + 3.4 = 14.3,  7.96 + 3.4 = 11.36.
  """
  return Plate(
    name="test_plate",
    size_x=127.6,
    size_y=85.75,
    size_z=13.83,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.9,
      dy=7.96,
      dz=1.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.8,
      size_y=6.8,
      size_z=10.67,
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=0.17,
      max_volume=350.0,
    ),
  )


# ---------------------------------------------------------------------------
# Helpers: build fake SiLA protobuf responses
# ---------------------------------------------------------------------------


def _sila_string_response(value: str) -> bytes:
  """Wrap a string as a SiLA String response: field 1 -> SiLA String(field 1 -> utf8)."""
  return length_delimited(1, sila_string(value))


def _sila_integer_msg(value: int) -> bytes:
  return varint_field(1, value)


def _intermediate_response(
  chunk_data: bytes,
  blob_index: int = 0,
  blob_checksum: int = 0,
  packet_count: int = 1,
  packet_index: int = 0,
) -> bytes:
  """Build a fake SnapImages_Intermediate protobuf response."""
  binary_msg = length_delimited(1, chunk_data)
  metadata_struct = (
    length_delimited(1, _sila_integer_msg(blob_index))
    + length_delimited(2, _sila_integer_msg(blob_checksum))
    + length_delimited(3, _sila_integer_msg(packet_count))
    + length_delimited(4, _sila_integer_msg(packet_index))
  )
  snap_event = length_delimited(1, metadata_struct)
  return length_delimited(1, binary_msg) + length_delimited(2, snap_event)


# ---------------------------------------------------------------------------
# Mock gRPC channel that records calls with metadata
# ---------------------------------------------------------------------------


class _Call:
  """A recorded gRPC call."""

  __slots__ = ("path", "request", "metadata", "timeout")

  def __init__(self, path: str, request: bytes, metadata, timeout):
    self.path = path
    self.request = request
    self.metadata = metadata
    self.timeout = timeout

  @property
  def has_lock_metadata(self) -> bool:
    if self.metadata is None:
      return False
    return any(k == _LOCK_META_KEY for k, _ in self.metadata)


class _MockChannel:
  """Mock gRPC channel that records every call with full context."""

  def __init__(self):
    self.calls: List[_Call] = []
    self.responses: Dict[str, bytes] = {}
    self.stream_responses: Dict[str, list] = {}
    self.closed = False

  def close(self):
    self.closed = True

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.close()

  def set_response(self, path: str, response: bytes):
    self.responses[path] = response

  def set_stream_response(self, path: str, responses: list):
    self.stream_responses[path] = responses

  def unary_unary(self, path, request_serializer=None, response_deserializer=None):
    def handler(request, metadata=None, timeout=None):
      self.calls.append(_Call(path, request, metadata, timeout))
      if path in self.responses:
        return self.responses[path]
      return b""

    return handler

  def unary_stream(self, path, request_serializer=None, response_deserializer=None):
    def handler(request, metadata=None, timeout=None):
      self.calls.append(_Call(path, request, metadata, timeout))
      return iter(self.stream_responses.get(path, []))

    return handler

  def get_calls(self, path: str) -> List[_Call]:
    return [c for c in self.calls if c.path == path]


def _make_backend(
  objectives=None,
  filter_cubes=None,
  lock_timeout=3600,
) -> Tuple[ExperimentalPicoBackend, _MockChannel]:
  """Create a PicoBackend with a mock channel, bypassing setup()."""
  backend = ExperimentalPicoBackend(
    host="127.0.0.1",
    port=8091,
    lock_timeout=lock_timeout,
    objectives=objectives or {},
    filter_cubes=filter_cubes or {},
  )
  channel = _MockChannel()
  backend._channel = channel
  backend._lock_id = "pylabrobot"
  backend._locked = True
  return backend, channel


def _decode_sila_string_from_request(data: bytes) -> str:
  """Decode the SiLA String from field 1 of a request message."""
  fields = decode_fields(data)
  sila_str_msg = get_field_bytes(fields, 1)
  assert sila_str_msg is not None
  inner = decode_fields(sila_str_msg)
  value = get_field_bytes(inner, 1)
  assert value is not None
  return value.decode("utf-8")


def _unwrap_sila_string(data: bytes) -> str:
  """Unwrap a raw SiLA String message (field 1 = utf8 bytes)."""
  fields = decode_fields(data)
  value = get_field_bytes(fields, 1)
  assert value is not None
  return value.decode("utf-8")


# ---------------------------------------------------------------------------
# Tests: setup() command sequence
# ---------------------------------------------------------------------------


class TestSetup(unittest.IsolatedAsyncioTestCase):
  async def test_setup_sends_correct_sequence(self):
    """setup() with no objectives/filter_cubes: unlock stale, lock, query hardware."""
    backend = ExperimentalPicoBackend(host="127.0.0.1", lock_timeout=120)
    channel = _MockChannel()

    channel.set_response(f"/{_LOCK_SVC}/UnlockServer", b"")
    channel.set_response(f"/{_LOCK_SVC}/LockServer", b"")
    channel.set_response(
      f"/{_OBJ_SVC}/Get_InstalledObjectives",
      _sila_string_response(json.dumps({"objectivesData": []})),
    )
    channel.set_response(
      f"/{_FC_SVC}/Get_InstalledFilterCubes",
      _sila_string_response(json.dumps({"filterCubesData": []})),
    )

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        self.assertEqual(len(channel.calls), 4)
        self.assertEqual(channel.calls[0].path, f"/{_LOCK_SVC}/UnlockServer")
        self.assertEqual(channel.calls[1].path, f"/{_LOCK_SVC}/LockServer")
        self.assertEqual(channel.calls[2].path, f"/{_OBJ_SVC}/Get_InstalledObjectives")
        self.assertEqual(channel.calls[3].path, f"/{_FC_SVC}/Get_InstalledFilterCubes")

    self.assertEqual(len(channel.calls), 5)
    self.assertEqual(channel.calls[4].path, f"/{_LOCK_SVC}/UnlockServer")

    # Unlock request contains lock ID
    self.assertEqual(_decode_sila_string_from_request(channel.calls[0].request), "pylabrobot")

    # Lock request contains lock ID "pylabrobot"
    self.assertEqual(_decode_sila_string_from_request(channel.calls[1].request), "pylabrobot")

  async def test_setup_configures_objectives_and_filter_cubes(self):
    """When objectives/filter_cubes are specified, setup() calls ChangeHardware."""
    backend = ExperimentalPicoBackend(
      host="127.0.0.1",
      objectives={0: Objective.O_4X_PL_FL},
      filter_cubes={0: ImagingMode.DAPI},
    )
    channel = _MockChannel()

    channel.set_response(f"/{_LOCK_SVC}/UnlockServer", b"")
    channel.set_response(f"/{_LOCK_SVC}/LockServer", b"")
    channel.set_response(
      f"/{_OBJ_SVC}/Get_InstalledObjectives",
      _sila_string_response(json.dumps({"objectivesData": [{"Id": "PL FLUOTAR 4x/0.13"}]})),
    )
    channel.set_response(
      f"/{_FC_SVC}/Get_InstalledFilterCubes",
      _sila_string_response(json.dumps({"filterCubesData": [{"Id": "DAPI"}]})),
    )
    # get_available_objectives / get_available_filter_cubes for validation
    channel.set_response(
      f"/{_OBJ_SVC}/GetAvailableObjectivesForPosition",
      _sila_string_response(json.dumps({"objectives": [{"Id": "PL FLUOTAR 4x/0.13"}]})),
    )
    channel.set_response(
      f"/{_FC_SVC}/Get_CompatibleFilterCubes",
      _sila_string_response(json.dumps({"filterCubes": [{"Id": "DAPI"}]})),
    )
    channel.set_response(f"/{_OBJ_SVC}/ChangeHardware", b"")
    channel.set_response(f"/{_FC_SVC}/ChangeHardware", b"")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        pass

    # Verify ChangeHardware was called with correct JSON params
    obj_change_calls = channel.get_calls(f"/{_OBJ_SVC}/ChangeHardware")
    self.assertEqual(len(obj_change_calls), 1)
    obj_params = json.loads(_decode_sila_string_from_request(obj_change_calls[0].request))
    self.assertEqual(obj_params["Id"], "PL FLUOTAR 4x/0.13")
    self.assertEqual(obj_params["Index"], 0)

    fc_change_calls = channel.get_calls(f"/{_FC_SVC}/ChangeHardware")
    self.assertEqual(len(fc_change_calls), 1)
    fc_params = json.loads(_decode_sila_string_from_request(fc_change_calls[0].request))
    self.assertEqual(fc_params["Id"], "DAPI")
    self.assertEqual(fc_params["Index"], 0)


# ---------------------------------------------------------------------------
# Tests: stop() command sequence
# ---------------------------------------------------------------------------


class TestStop(unittest.IsolatedAsyncioTestCase):
  async def test_stop_sends_unlock(self):
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()

    channel.set_response(f"/{_LOCK_SVC}/UnlockServer", b"")
    channel.set_response(f"/{_LOCK_SVC}/LockServer", b"")
    channel.set_response(
      f"/{_OBJ_SVC}/Get_InstalledObjectives",
      _sila_string_response(json.dumps({"objectivesData": []})),
    )
    channel.set_response(
      f"/{_FC_SVC}/Get_InstalledFilterCubes",
      _sila_string_response(json.dumps({"filterCubesData": []})),
    )

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        pass

    # Expected calls:
    # 0. UnlockServer (stale)
    # 1. LockServer
    # 2. Get_InstalledObjectives
    # 3. Get_InstalledFilterCubes
    # 4. UnlockServer (from cleanup!)
    self.assertEqual(len(channel.calls), 5)
    self.assertEqual(channel.calls[4].path, f"/{_LOCK_SVC}/UnlockServer")
    self.assertEqual(_decode_sila_string_from_request(channel.calls[4].request), "pylabrobot")
    self.assertTrue(channel.closed)


# ---------------------------------------------------------------------------
# Tests: door control commands
# ---------------------------------------------------------------------------


class TestDoorCommands(unittest.IsolatedAsyncioTestCase):
  async def test_open_door(self):
    backend, channel = _make_backend()
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    channel.set_response(f"/{_HW_SVC}/OpenPlateDrawer", b"")

    await backend.open_door()

    self.assertEqual(len(channel.calls), 2)
    self.assertEqual(channel.calls[0].path, f"/{_INST_SVC}/Initialize")
    self.assertTrue(channel.calls[0].has_lock_metadata)
    self.assertEqual(channel.calls[1].path, f"/{_HW_SVC}/OpenPlateDrawer")
    self.assertTrue(channel.calls[1].has_lock_metadata)
    self.assertTrue(backend.door_open)

  async def test_close_door(self):
    backend, channel = _make_backend()
    backend._door_open = True
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    channel.set_response(f"/{_HW_SVC}/ClosePlateDrawer", b"")

    await backend.close_door()

    self.assertEqual(len(channel.calls), 2)
    self.assertEqual(channel.calls[0].path, f"/{_INST_SVC}/Initialize")
    self.assertTrue(channel.calls[0].has_lock_metadata)
    self.assertEqual(channel.calls[1].path, f"/{_HW_SVC}/ClosePlateDrawer")
    self.assertTrue(channel.calls[1].has_lock_metadata)
    self.assertFalse(backend.door_open)


# ---------------------------------------------------------------------------
# Tests: objective maintenance commands
# ---------------------------------------------------------------------------


class TestObjectiveMaintenanceCommands(unittest.IsolatedAsyncioTestCase):
  async def test_enter_maintenance(self):
    backend, channel = _make_backend()
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    channel.set_response(f"/{_OBJ_SVC}/EnterObjectiveMaintenance", b"")

    await backend.enter_objective_maintenance(2)

    self.assertEqual(len(channel.calls), 2)
    self.assertEqual(channel.calls[0].path, f"/{_INST_SVC}/Initialize")
    self.assertEqual(channel.calls[1].path, f"/{_OBJ_SVC}/EnterObjectiveMaintenance")
    self.assertTrue(channel.calls[1].has_lock_metadata)
    params = json.loads(_decode_sila_string_from_request(channel.calls[1].request))
    self.assertEqual(params, {"Index": 2})

  async def test_exit_maintenance(self):
    backend, channel = _make_backend()
    channel.set_response(f"/{_OBJ_SVC}/ExitObjectiveMaintenance", b"")

    await backend.exit_objective_maintenance()

    self.assertEqual(len(channel.calls), 1)
    self.assertEqual(channel.calls[0].path, f"/{_OBJ_SVC}/ExitObjectiveMaintenance")
    self.assertEqual(channel.calls[0].request, b"")
    self.assertTrue(channel.calls[0].has_lock_metadata)


# ---------------------------------------------------------------------------
# Tests: get_configuration command + response decoding
# ---------------------------------------------------------------------------


class TestGetConfiguration(unittest.IsolatedAsyncioTestCase):
  async def test_decodes_instrument_configuration(self):
    backend, channel = _make_backend()
    config = {
      "InstrumentConfiguration": {
        "objectivesComponent": {"objectives": [{"Id": "4x", "Magnification": 4}]},
        "filterCubesComponent": {"filterCubes": [{"Id": "DAPI"}]},
      }
    }
    channel.set_response(
      f"/{_INST_SVC}/Get_InstrumentConfiguration",
      _sila_string_response(json.dumps(config)),
    )

    result = await backend.get_configuration()

    self.assertEqual(len(channel.calls), 1)
    self.assertEqual(channel.calls[0].path, f"/{_INST_SVC}/Get_InstrumentConfiguration")
    self.assertEqual(channel.calls[0].request, b"")
    # Response unwrapped from SiLA String → JSON → InstrumentConfiguration key extracted
    self.assertEqual(result, config["InstrumentConfiguration"])


# ---------------------------------------------------------------------------
# Tests: change_objective / change_filter_cube commands
# ---------------------------------------------------------------------------


class TestChangeHardwareCommands(unittest.IsolatedAsyncioTestCase):
  async def test_change_objective(self):
    backend, channel = _make_backend()
    available = [{"Id": "PL FLUOTAR 4x/0.13"}, {"Id": "PL FLUOTAR 10x/0.30"}]
    channel.set_response(
      f"/{_OBJ_SVC}/GetAvailableObjectivesForPosition",
      _sila_string_response(json.dumps({"objectives": available})),
    )
    channel.set_response(f"/{_OBJ_SVC}/ChangeHardware", b"")

    await backend.change_objective(1, "PL FLUOTAR 10x/0.30")

    # Query available, then change
    self.assertEqual(len(channel.calls), 2)
    self.assertEqual(channel.calls[0].path, f"/{_OBJ_SVC}/GetAvailableObjectivesForPosition")
    query_params = json.loads(_decode_sila_string_from_request(channel.calls[0].request))
    self.assertEqual(query_params, {"Index": 1})
    self.assertTrue(channel.calls[0].has_lock_metadata)

    self.assertEqual(channel.calls[1].path, f"/{_OBJ_SVC}/ChangeHardware")
    change_params = json.loads(_decode_sila_string_from_request(channel.calls[1].request))
    self.assertEqual(change_params, {"Id": "PL FLUOTAR 10x/0.30", "Index": 1})
    self.assertTrue(channel.calls[1].has_lock_metadata)

  async def test_change_objective_rejects_invalid_id(self):
    backend, channel = _make_backend()
    channel.set_response(
      f"/{_OBJ_SVC}/GetAvailableObjectivesForPosition",
      _sila_string_response(json.dumps({"objectives": [{"Id": "4x"}]})),
    )

    with self.assertRaises(ValueError) as ctx:
      await backend.change_objective(0, "INVALID")
    self.assertIn("not compatible", str(ctx.exception))

    # Only the query was sent, ChangeHardware was NOT called
    self.assertEqual(len(channel.calls), 1)
    self.assertEqual(channel.calls[0].path, f"/{_OBJ_SVC}/GetAvailableObjectivesForPosition")

  async def test_change_filter_cube(self):
    backend, channel = _make_backend()
    channel.set_response(
      f"/{_FC_SVC}/Get_CompatibleFilterCubes",
      _sila_string_response(json.dumps({"filterCubes": [{"Id": "DAPI"}, {"Id": "FITC"}]})),
    )
    channel.set_response(f"/{_FC_SVC}/ChangeHardware", b"")

    await backend.change_filter_cube(1, "FITC")

    self.assertEqual(len(channel.calls), 2)
    self.assertEqual(channel.calls[0].path, f"/{_FC_SVC}/Get_CompatibleFilterCubes")
    self.assertEqual(channel.calls[1].path, f"/{_FC_SVC}/ChangeHardware")
    params = json.loads(_decode_sila_string_from_request(channel.calls[1].request))
    self.assertEqual(params, {"Id": "FITC", "Index": 1})
    self.assertTrue(channel.calls[1].has_lock_metadata)


# ---------------------------------------------------------------------------
# Tests: capture() — full command flow + response decoding
# ---------------------------------------------------------------------------


class TestCapture(unittest.IsolatedAsyncioTestCase):
  def _setup_capture_channel(
    self,
    channel: _MockChannel,
    exec_uuid: str,
    snap_event_json: dict,
  ):
    """Wire up the mock channel for a complete capture() flow."""
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    channel.set_response(f"/{_SNAP_SVC}/SnapImages", _sila_string_response(exec_uuid))
    channel.set_response(f"/{_SNAP_SVC}/SnapImages_Result", b"")

    blob_bytes = json.dumps(snap_event_json).encode("utf-8")
    md5_digest = hashlib.md5(blob_bytes).digest()
    checksum = struct.unpack("<q", md5_digest[:8])[0]

    channel.set_stream_response(
      f"/{_SNAP_SVC}/SnapImages_Intermediate",
      [_intermediate_response(blob_bytes, blob_index=0, blob_checksum=checksum)],
    )

  async def test_capture_sends_correct_snap_params(self):
    """Verify SnapImages request contains the right labware + snap JSON."""

    backend, channel = _make_backend(
      objectives={0: Objective.O_4X_PL_FL},
      filter_cubes={0: ImagingMode.DAPI},
    )

    pixel_data = np.zeros((2, 2), dtype=np.uint16)
    snap_event = {
      "imageData": {
        "imageBuffer": base64.b64encode(pixel_data.tobytes()).decode(),
        "imageBufferInfo": {"width": 2, "height": 2},
        "capturedImageInfo": {"exposureTimeUs": 10000},
      },
    }
    self._setup_capture_channel(channel, "uuid-1", snap_event)

    await backend.capture(
      row=3,
      column=7,
      mode=ImagingMode.DAPI,
      objective=Objective.O_4X_PL_FL,
      exposure_time=15.0,  # ms
      focal_height=2.5,  # mm
      gain=0,
      plate=_test_plate(),
    )

    # Decode the SnapImages request: field 1 = SiLA String(labware JSON),
    # field 2 = SiLA String(snap JSON)
    snap_call = channel.get_calls(f"/{_SNAP_SVC}/SnapImages")[0]
    self.assertTrue(snap_call.has_lock_metadata)
    req_fields = decode_fields(snap_call.request)
    labware_bytes = get_field_bytes(req_fields, 1)
    assert labware_bytes is not None
    labware_json = json.loads(_unwrap_sila_string(labware_bytes))
    snap_bytes = get_field_bytes(req_fields, 2)
    assert snap_bytes is not None
    snap_json = json.loads(_unwrap_sila_string(snap_bytes))

    # Verify labware params derived from the test plate
    dims = labware_json["LabwareDimensions"]
    self.assertEqual(labware_json["LabwareType"], 1)
    self.assertAlmostEqual(labware_json["RefractionIndex"], 1.0)
    self.assertEqual(dims["ncavities"], 96)
    self.assertEqual(dims["nrows"], 8)
    self.assertEqual(dims["columns"], 12)
    self.assertAlmostEqual(dims["labware_length"], 127.6)
    self.assertAlmostEqual(dims["labware_width"], 85.75)
    self.assertAlmostEqual(dims["labware_height"], 13.83)
    self.assertAlmostEqual(dims["dist2firstcol"], 14.3)
    self.assertAlmostEqual(dims["dist2firstrow"], 11.36)
    self.assertAlmostEqual(dims["row_distance"], 9.0)
    self.assertAlmostEqual(dims["well2well_dist_col"], 9.0)
    self.assertAlmostEqual(dims["bottom_thickness"], 0.17)
    self.assertAlmostEqual(dims["bottom_length"], 6.8)
    self.assertAlmostEqual(dims["bottom_width"], 6.8)
    self.assertAlmostEqual(dims["volume"], 350.0)
    self.assertFalse(dims["round_bottom"])

    # Verify snap params reflect our inputs
    self.assertEqual(snap_json["capturePosition"]["cavityCoordinatesIndexXy"]["Item2"], 3)  # row
    self.assertEqual(snap_json["capturePosition"]["cavityCoordinatesIndexXy"]["Item1"], 7)  # col
    self.assertEqual(snap_json["imagesChannelParameters"][0]["exposureTimeUs"], 15000)  # 15ms
    self.assertEqual(snap_json["imagesChannelParameters"][0]["objectiveId"], "PL FLUOTAR 4x/0.13")
    self.assertEqual(
      snap_json["imagesChannelParameters"][0]["illuminationConfig"]["filterCubeId"], "DAPI"
    )
    self.assertTrue(snap_json["skipAutofocus"])
    self.assertAlmostEqual(snap_json["focusSettings"]["baseZPositionUm"], 2500.0)  # 2.5mm

  async def test_capture_auto_exposure_and_autofocus(self):
    """When exposure_time='auto' and focal_height='auto', verify params."""

    backend, channel = _make_backend(
      objectives={0: Objective.O_4X_PL_FL},
      filter_cubes={0: ImagingMode.DAPI},
    )

    pixel_data = np.zeros((2, 2), dtype=np.uint16)
    snap_event = {
      "imageData": {
        "imageBuffer": base64.b64encode(pixel_data.tobytes()).decode(),
        "imageBufferInfo": {"width": 2, "height": 2},
        "capturedImageInfo": {"exposureTimeUs": 8000},
      },
    }
    self._setup_capture_channel(channel, "uuid-2", snap_event)

    await backend.capture(
      row=0,
      column=0,
      mode=ImagingMode.DAPI,
      objective=Objective.O_4X_PL_FL,
      exposure_time="machine-auto",
      focal_height="machine-auto",
      gain=0,
      plate=_test_plate(),
    )

    snap_call = channel.get_calls(f"/{_SNAP_SVC}/SnapImages")[0]
    req_fields = decode_fields(snap_call.request)
    snap_bytes = get_field_bytes(req_fields, 2)
    assert snap_bytes is not None
    snap_json = json.loads(_unwrap_sila_string(snap_bytes))

    self.assertTrue(snap_json["imagesChannelParameters"][0]["doAutoExposure"])
    self.assertFalse(snap_json["skipAutofocus"])
    self.assertAlmostEqual(snap_json["focusSettings"]["baseZPositionUm"], 0.0)

  async def test_capture_observable_command_flow(self):
    """Verify the 3-step observable command protocol: start, stream, result."""

    backend, channel = _make_backend(
      objectives={0: Objective.O_4X_PL_FL},
      filter_cubes={0: ImagingMode.DAPI},
    )

    pixel_data = np.array([[100, 200], [300, 400]], dtype=np.uint16)
    snap_event = {
      "imageData": {
        "imageBuffer": base64.b64encode(pixel_data.tobytes()).decode(),
        "imageBufferInfo": {"width": 2, "height": 2, "stride": 4},
        "capturedImageInfo": {"exposureTimeUs": 5000},
      },
    }
    self._setup_capture_channel(channel, "exec-uuid-abc", snap_event)

    result = await backend.capture(
      row=0,
      column=0,
      mode=ImagingMode.DAPI,
      objective=Objective.O_4X_PL_FL,
      exposure_time=10.0,
      focal_height=1.0,
      gain=0,
      plate=_test_plate(),
    )

    # Initialize, SnapImages, SnapImages_Intermediate, SnapImages_Result
    self.assertEqual(len(channel.calls), 4)
    self.assertEqual(channel.calls[0].path, f"/{_INST_SVC}/Initialize")
    self.assertEqual(channel.calls[1].path, f"/{_SNAP_SVC}/SnapImages")
    self.assertEqual(channel.calls[2].path, f"/{_SNAP_SVC}/SnapImages_Intermediate")
    self.assertEqual(channel.calls[3].path, f"/{_SNAP_SVC}/SnapImages_Result")

    # Intermediate and Result both carry the execution UUID from the confirmation
    for call in channel.calls[2:4]:
      uuid_bytes = get_field_bytes(decode_fields(call.request), 1)
      self.assertEqual(uuid_bytes, b"exec-uuid-abc")

    # All calls carry lock metadata
    for call in channel.calls:
      self.assertTrue(call.has_lock_metadata, f"{call.path} missing lock metadata")

    # Image correctly decoded
    self.assertEqual(len(result.images), 1)
    np.testing.assert_array_equal(result.images[0], pixel_data)
    self.assertAlmostEqual(result.exposure_time, 5.0)  # 5000us -> 5.0ms
    self.assertAlmostEqual(result.focal_height, 1.0)

  async def test_capture_multi_chunk_reassembly(self):
    """Verify image data is correctly reassembled from multiple chunks."""

    backend, channel = _make_backend(
      objectives={0: Objective.O_4X_PL_FL},
      filter_cubes={0: ImagingMode.DAPI},
    )

    pixel_data = np.array([[10, 20], [30, 40]], dtype=np.uint16)
    snap_event = {
      "imageData": {
        "imageBuffer": base64.b64encode(pixel_data.tobytes()).decode(),
        "imageBufferInfo": {"width": 2, "height": 2},
        "capturedImageInfo": {"exposureTimeUs": 1000},
      },
    }
    blob_bytes = json.dumps(snap_event).encode("utf-8")
    md5_digest = hashlib.md5(blob_bytes).digest()
    checksum = struct.unpack("<q", md5_digest[:8])[0]

    # Split blob into two chunks
    mid = len(blob_bytes) // 2
    chunk_0 = blob_bytes[:mid]
    chunk_1 = blob_bytes[mid:]

    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    channel.set_response(f"/{_SNAP_SVC}/SnapImages", _sila_string_response("uuid-multi"))
    channel.set_response(f"/{_SNAP_SVC}/SnapImages_Result", b"")
    channel.set_stream_response(
      f"/{_SNAP_SVC}/SnapImages_Intermediate",
      [
        _intermediate_response(
          chunk_0, blob_index=0, blob_checksum=checksum, packet_count=2, packet_index=0
        ),
        _intermediate_response(
          chunk_1, blob_index=0, blob_checksum=checksum, packet_count=2, packet_index=1
        ),
      ],
    )

    result = await backend.capture(
      row=0,
      column=0,
      mode=ImagingMode.DAPI,
      objective=Objective.O_4X_PL_FL,
      exposure_time=1.0,
      focal_height=0.5,
      gain=0,
      plate=_test_plate(),
    )

    self.assertEqual(len(result.images), 1)
    np.testing.assert_array_equal(result.images[0], pixel_data)

  async def test_capture_brightfield_uses_correct_illumination(self):
    """Brightfield mode uses different light_channel/excitation_source."""

    backend, channel = _make_backend(
      objectives={0: Objective.O_4X_PL_FL},
      filter_cubes={0: ImagingMode.BRIGHTFIELD},
    )

    pixel_data = np.zeros((2, 2), dtype=np.uint16)
    snap_event = {
      "imageData": {
        "imageBuffer": base64.b64encode(pixel_data.tobytes()).decode(),
        "imageBufferInfo": {"width": 2, "height": 2},
        "capturedImageInfo": {"exposureTimeUs": 10000},
      },
    }
    self._setup_capture_channel(channel, "uuid-bf", snap_event)

    await backend.capture(
      row=0,
      column=0,
      mode=ImagingMode.BRIGHTFIELD,
      objective=Objective.O_4X_PL_FL,
      exposure_time=10.0,
      focal_height=1.0,
      gain=0,
      plate=_test_plate(),
    )

    snap_call = channel.get_calls(f"/{_SNAP_SVC}/SnapImages")[0]
    req_fields = decode_fields(snap_call.request)
    snap_bytes = get_field_bytes(req_fields, 2)
    assert snap_bytes is not None
    snap_json = json.loads(_unwrap_sila_string(snap_bytes))

    illum = snap_json["imagesChannelParameters"][0]["illuminationConfig"]
    self.assertEqual(illum["lightChannel"], 5)
    self.assertEqual(illum["filterCubeId"], "")
    self.assertEqual(illum["excitationSourceId"], "5069278")


# ---------------------------------------------------------------------------
# Tests: image extraction helpers (important for response decoding)
# ---------------------------------------------------------------------------


class TestExtractImageBuffer(unittest.TestCase):
  def test_base64_string(self):
    raw = b"\x01\x02\x03"
    event = {"imageData": {"imageBuffer": base64.b64encode(raw).decode()}}
    self.assertEqual(_extract_image_buffer(event), raw)

  def test_byte_list(self):
    event = {"imageData": {"imageBuffer": [1, 2, 3]}}
    self.assertEqual(_extract_image_buffer(event), bytes([1, 2, 3]))

  def test_pascal_case_keys(self):
    raw = b"\xff"
    event = {"ImageData": {"ImageBuffer": base64.b64encode(raw).decode()}}
    self.assertEqual(_extract_image_buffer(event), raw)

  def test_no_image_data_returns_none(self):
    self.assertIsNone(_extract_image_buffer({}))


class TestGetImageInfo(unittest.TestCase):
  def test_extracts_all_fields(self):
    event = {
      "imageData": {
        "imageBufferInfo": {"width": 2008, "height": 2008, "stride": 4016},
        "capturedImageInfo": {"exposureTimeUs": 5000},
      },
      "zFocusOffsetUm": 100.5,
    }
    info = _get_image_info(event)
    self.assertEqual(info["width"], 2008)
    self.assertEqual(info["height"], 2008)
    self.assertEqual(info["stride"], 4016)
    self.assertEqual(info["exposure_time_us"], 5000)
    self.assertAlmostEqual(info["z_focus_offset_um"], 100.5)

  def test_pascal_case_keys(self):
    event = {
      "ImageData": {
        "ImageBufferInfo": {"Width": 1024, "Height": 768},
        "CapturedImageInfo": {"ExposureTimeUs": 3000},
      },
    }
    info = _get_image_info(event)
    self.assertEqual(info["width"], 1024)
    self.assertEqual(info["height"], 768)


class TestDecodeIntermediateResponse(unittest.TestCase):
  def test_roundtrip(self):
    data = _intermediate_response(
      chunk_data=b"hello",
      blob_index=3,
      blob_checksum=42,
      packet_count=5,
      packet_index=2,
    )
    chunk, meta = _decode_intermediate_response(data)
    self.assertEqual(chunk, b"hello")
    self.assertEqual(meta["blob_index"], 3)
    self.assertEqual(meta["blob_checksum"], 42)
    self.assertEqual(meta["packet_count"], 5)
    self.assertEqual(meta["packet_index"], 2)

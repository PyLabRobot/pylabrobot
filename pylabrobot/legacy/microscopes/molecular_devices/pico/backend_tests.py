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
import threading
import unittest
from typing import Callable, Dict, List, Optional, Tuple
from unittest.mock import Mock, patch

import anyio
import pytest
from trio.testing import MockClock

pytest.importorskip("numpy")
pytest.importorskip("grpc")

import grpc
import numpy as np  # type: ignore[import-not-found]

from pylabrobot.concurrency import HasLifespan
from pylabrobot.io.sila.grpc import (
  decode_fields,
  get_field_bytes,
  length_delimited,
  sila_string,
  varint_field,
)
from pylabrobot.legacy.microscopes.molecular_devices.pico.backend import (
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
from pylabrobot.legacy.plate_reading.standard import ImagingMode, Objective
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.testing.concurrency import AnyioTestBase

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
      name_prefix="test_plate",
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

  def __init__(self, *, events: Optional[List[str]] = None):
    self.calls: List[_Call] = []
    self.responses: Dict[str, bytes] = {}
    self.stream_responses: Dict[str, list] = {}
    self.handlers: Dict[str, Callable[[], bytes]] = {}
    self.closed = False
    self.events = events if events is not None else []

  def close(self):
    self.closed = True
    self.events.append("channel.close")

  def set_response(self, path: str, response: bytes):
    self.responses[path] = response

  def set_stream_response(self, path: str, responses: list):
    self.stream_responses[path] = responses

  def set_handler(self, path: str, handler: Callable[[], bytes]):
    self.handlers[path] = handler

  def unary_unary(self, path, request_serializer=None, response_deserializer=None):
    def handler(request, metadata=None, timeout=None):
      self.calls.append(_Call(path, request, metadata, timeout))
      self.events.append(path)
      if path in self.handlers:
        return self.handlers[path]()
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
  backend._channel = Mock(spec=grpc.Channel, wraps=channel)
  backend._lock_id = "pylabrobot"
  backend._locked = True
  return backend, channel


def _configure_setup_channel(channel: _MockChannel) -> None:
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


class TestSetup(AnyioTestBase):
  def test_inherits_managed_methods(self) -> None:
    assert ExperimentalPicoBackend.__aenter__ is HasLifespan.__aenter__
    assert ExperimentalPicoBackend.__aexit__ is HasLifespan.__aexit__
    assert ExperimentalPicoBackend.setup is HasLifespan.setup
    assert ExperimentalPicoBackend.stop is HasLifespan.stop

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
        assert len(channel.calls) == 4
        assert channel.calls[0].path == f"/{_LOCK_SVC}/UnlockServer"
        assert channel.calls[1].path == f"/{_LOCK_SVC}/LockServer"
        assert channel.calls[2].path == f"/{_OBJ_SVC}/Get_InstalledObjectives"
        assert channel.calls[3].path == f"/{_FC_SVC}/Get_InstalledFilterCubes"

        # Unlock and lock requests contain lock ID "pylabrobot".
        assert _decode_sila_string_from_request(channel.calls[0].request) == "pylabrobot"
        assert _decode_sila_string_from_request(channel.calls[1].request) == "pylabrobot"

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
        # Verify ChangeHardware was called with correct JSON params.
        obj_change_calls = channel.get_calls(f"/{_OBJ_SVC}/ChangeHardware")
        assert len(obj_change_calls) == 1
        obj_params = json.loads(_decode_sila_string_from_request(obj_change_calls[0].request))
        assert obj_params["Id"] == "PL FLUOTAR 4x/0.13"
        assert obj_params["Index"] == 0

        fc_change_calls = channel.get_calls(f"/{_FC_SVC}/ChangeHardware")
        assert len(fc_change_calls) == 1
        fc_params = json.loads(_decode_sila_string_from_request(fc_change_calls[0].request))
        assert fc_params["Id"] == "DAPI"
        assert fc_params["Index"] == 0

  async def test_rejects_options_before_channel_creation(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")

    with patch("grpc.insecure_channel") as create_channel:
      with pytest.raises(TypeError, match="unexpected keyword argument"):
        async with backend.lifespan(unknown=True):
          pytest.fail("invalid options reached the body")

    create_channel.assert_not_called()

  async def test_rejects_missing_grpc_before_channel_creation(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")

    with (
      patch("pylabrobot.legacy.microscopes.molecular_devices.pico.backend.HAS_GRPC", False),
      patch(
        "pylabrobot.legacy.microscopes.molecular_devices.pico.backend._GRPC_IMPORT_ERROR",
        ImportError("grpc unavailable"),
        create=True,
      ),
      patch("grpc.insecure_channel") as create_channel,
    ):
      with pytest.raises(RuntimeError, match="grpcio is required"):
        async with backend:
          pytest.fail("missing dependency reached the body")

    create_channel.assert_not_called()

  async def test_channel_creation_is_registered_before_first_rpc(self) -> None:
    failure = ValueError("first RPC failed")
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    channel.set_handler(f"/{_LOCK_SVC}/UnlockServer", lambda: (_ for _ in ()).throw(failure))

    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(ValueError) as error_info:
        async with backend:
          pytest.fail("failed entry reached the body")

    assert error_info.value is failure
    assert channel.closed

  async def test_lock_failure_closes_channel(self) -> None:
    failure = RuntimeError("lock failed")
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    channel.set_response(f"/{_LOCK_SVC}/UnlockServer", b"")
    channel.set_handler(f"/{_LOCK_SVC}/LockServer", lambda: (_ for _ in ()).throw(failure))

    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          pytest.fail("failed lock reached the body")

    assert error_info.value is failure
    assert channel.closed
    assert [call.path for call in channel.calls] == [
      f"/{_LOCK_SVC}/UnlockServer",
      f"/{_LOCK_SVC}/LockServer",
    ]

  async def test_objective_query_failure_unlocks_then_closes(self) -> None:
    events: List[str] = []
    failure = RuntimeError("objective query failed")
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel(events=events)
    _configure_setup_channel(channel)
    channel.set_handler(
      f"/{_OBJ_SVC}/Get_InstalledObjectives", lambda: (_ for _ in ()).throw(failure)
    )

    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          pytest.fail("failed objective query reached the body")

    assert error_info.value is failure
    assert events[-2:] == [f"/{_LOCK_SVC}/UnlockServer", "channel.close"]

  async def test_filter_configuration_failure_unlocks_then_closes(self) -> None:
    events: List[str] = []
    failure = RuntimeError("filter configuration failed")
    backend = ExperimentalPicoBackend(host="127.0.0.1", filter_cubes={0: ImagingMode.DAPI})
    channel = _MockChannel(events=events)
    _configure_setup_channel(channel)
    channel.set_response(
      f"/{_FC_SVC}/Get_InstalledFilterCubes",
      _sila_string_response(json.dumps({"filterCubesData": [{"Id": "DAPI"}]})),
    )
    channel.set_handler(
      f"/{_FC_SVC}/Get_CompatibleFilterCubes", lambda: (_ for _ in ()).throw(failure)
    )

    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          pytest.fail("failed filter configuration reached the body")

    assert error_info.value is failure
    assert events[-2:] == [f"/{_LOCK_SVC}/UnlockServer", "channel.close"]

  async def test_cancelled_lock_rpc_reconciles_before_channel_close(self) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel(events=events)
    channel.set_response(f"/{_LOCK_SVC}/UnlockServer", b"")
    started = threading.Event()
    release = threading.Event()
    scopes: List[anyio.CancelScope] = []

    def blocking_lock() -> bytes:
      started.set()
      release.wait(2)
      events.append("lock.rpc.complete")
      return b""

    channel.set_handler(f"/{_LOCK_SVC}/LockServer", blocking_lock)

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          pytest.fail("cancelled lock reached the body")

    with patch("grpc.insecure_channel", return_value=channel):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter)
        assert await anyio.to_thread.run_sync(started.wait, 1)
        scopes[0].cancel()
        await anyio.lowlevel.checkpoint()
        assert not channel.closed
        release.set()

    unlock_path = f"/{_LOCK_SVC}/UnlockServer"
    unlock_indices = [index for index, event in enumerate(events) if event == unlock_path]
    assert len(unlock_indices) == 2
    assert events.index("lock.rpc.complete") < unlock_indices[1] < events.index("channel.close")
    assert channel.closed


# ---------------------------------------------------------------------------
# Tests: stop() command sequence
# ---------------------------------------------------------------------------


class TestStop(AnyioTestBase):
  async def test_stop_sends_unlock(self):
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    _configure_setup_channel(channel)

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        pass

    unlock_calls = channel.get_calls(f"/{_LOCK_SVC}/UnlockServer")
    assert len(unlock_calls) == 2
    assert _decode_sila_string_from_request(unlock_calls[-1].request) == "pylabrobot"
    assert channel.closed

  async def test_body_cancellation_force_exits_unlocks_and_closes(self) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel(events=events)
    _configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")

    with patch("grpc.insecure_channel", return_value=channel):
      with anyio.CancelScope() as scope:
        async with backend:
          await backend.enter_objective_maintenance(0)
          scope.cancel()
          await anyio.sleep_forever()

    assert events[-3:] == [
      f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
      f"/{_LOCK_SVC}/UnlockServer",
      "channel.close",
    ]

  async def test_force_exit_failure_preserves_body_context_and_closes_channel(self) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel(events=events)
    _configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    body_failure = RuntimeError("body failed")
    exit_failure = RuntimeError("force exit failed")

    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          await backend.enter_objective_maintenance(0)
          channel.set_handler(
            f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
            lambda: (_ for _ in ()).throw(exit_failure),
          )
          raise body_failure

    assert error_info.value is exit_failure
    assert error_info.value.__context__ is body_failure
    assert events[-3:] == [
      f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
      f"/{_LOCK_SVC}/UnlockServer",
      "channel.close",
    ]

  async def test_sequential_reuse_creates_fresh_channels(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    first_channel = _MockChannel()
    second_channel = _MockChannel()
    _configure_setup_channel(first_channel)
    _configure_setup_channel(second_channel)

    with patch("grpc.insecure_channel", side_effect=[first_channel, second_channel]):
      async with backend:
        assert backend.channel is first_channel
      async with backend:
        assert backend.channel is second_channel

    assert first_channel.closed and second_channel.closed


class TestCleanupTimeouts(AnyioTestBase):
  _anyio_backends = {
    "asyncio": None,
    "trio": {"clock": MockClock(autojump_threshold=0)},
  }

  async def test_force_exit_waiting_for_maintenance_lock_times_out_then_cleans_up(
    self,
  ) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    backend._CLEANUP_TIMEOUT = 0.01
    first_channel = _MockChannel(events=events)
    second_channel = _MockChannel(events=events)
    _configure_setup_channel(first_channel)
    _configure_setup_channel(second_channel)
    lock_held = anyio.Event()
    release_lock = anyio.Event()
    scope_token = object()

    async def hold_maintenance_lock() -> None:
      async with backend._objective_maintenance_transition_lock:
        lock_held.set()
        await release_lock.wait()

    async def release_later() -> None:
      await lock_held.wait()
      await anyio.wait_all_tasks_blocked()
      await anyio.sleep(0.1)
      release_lock.set()

    with patch("grpc.insecure_channel", side_effect=[first_channel, second_channel]):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(release_later)
        with pytest.raises(TimeoutError) as error_info:
          async with backend:
            backend._objective_maintenance_active = True
            backend._objective_maintenance_scope_token = scope_token
            task_group.start_soon(hold_maintenance_lock)
            await lock_held.wait()

      assert isinstance(error_info.value.__context__, anyio.get_cancelled_exc_class())
      assert events[-2:] == [f"/{_LOCK_SVC}/UnlockServer", "channel.close"]
      assert first_channel.closed
      assert backend._objective_maintenance_active
      assert not backend._objective_maintenance_state_unknown
      assert backend._objective_maintenance_scope_token is scope_token

      async with backend:
        pass

    assert events[-3:] == [
      f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
      f"/{_LOCK_SVC}/UnlockServer",
      "channel.close",
    ]
    assert second_channel.closed
    assert not backend._objective_maintenance_active
    assert not backend._objective_maintenance_state_unknown

  async def test_force_exit_waiting_for_worker_times_out_then_unlock_and_close_continue(
    self,
  ) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    backend._CLEANUP_TIMEOUT = 0.01
    first_channel = _MockChannel(events=events)
    second_channel = _MockChannel(events=events)
    _configure_setup_channel(first_channel)
    _configure_setup_channel(second_channel)
    worker_started = anyio.Event()
    worker_release = threading.Event()
    scope_token = object()
    limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = limiter.total_tokens
    limiter.total_tokens = 1
    original_force_exit = backend._force_exit_objective_maintenance
    original_unlock = backend._unlock_during_stop

    async def record_force_exit() -> None:
      events.append("force.cleanup.start")
      await original_force_exit()

    async def record_unlock() -> None:
      events.append("unlock.cleanup.start")
      await original_unlock()

    backend._force_exit_objective_maintenance = record_force_exit  # type: ignore[method-assign]
    backend._unlock_during_stop = record_unlock  # type: ignore[method-assign]

    def occupy_worker() -> None:
      anyio.from_thread.run_sync(worker_started.set)
      worker_release.wait()

    async def release_later() -> None:
      await worker_started.wait()
      await anyio.wait_all_tasks_blocked()
      await anyio.sleep(0.1)
      events.append("worker.release")
      worker_release.set()

    try:
      with patch("grpc.insecure_channel", side_effect=[first_channel, second_channel]):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(release_later)
          with pytest.raises(TimeoutError) as error_info:
            async with backend:
              backend._objective_maintenance_active = True
              backend._objective_maintenance_scope_token = scope_token
              task_group.start_soon(anyio.to_thread.run_sync, occupy_worker)
              await worker_started.wait()

        assert isinstance(error_info.value.__context__, anyio.get_cancelled_exc_class())
        assert events.index("force.cleanup.start") < events.index("unlock.cleanup.start")
        assert events.index("unlock.cleanup.start") < events.index("channel.close")
        assert events.index("channel.close") < events.index("worker.release")
        assert first_channel.closed
        assert backend._objective_maintenance_active
        assert backend._objective_maintenance_state_unknown
        assert backend._objective_maintenance_scope_token is scope_token

        async with backend:
          pass

      assert events[-5:] == [
        "force.cleanup.start",
        f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
        "unlock.cleanup.start",
        f"/{_LOCK_SVC}/UnlockServer",
        "channel.close",
      ]
      assert second_channel.closed
      assert not backend._objective_maintenance_active
      assert not backend._objective_maintenance_state_unknown
      assert backend._objective_maintenance_scope_token is None
    finally:
      worker_release.set()
      limiter.total_tokens = original_tokens

  async def test_unlock_waiting_for_worker_times_out_then_closes_and_reuses(
    self,
  ) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    backend._CLEANUP_TIMEOUT = 0.01
    first_channel = _MockChannel(events=events)
    second_channel = _MockChannel(events=events)
    _configure_setup_channel(first_channel)
    _configure_setup_channel(second_channel)
    worker_started = anyio.Event()
    worker_release = threading.Event()
    limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = limiter.total_tokens
    limiter.total_tokens = 1

    def occupy_worker() -> None:
      anyio.from_thread.run_sync(worker_started.set)
      worker_release.wait()

    async def release_later() -> None:
      await worker_started.wait()
      await anyio.wait_all_tasks_blocked()
      await anyio.sleep(0.1)
      events.append("worker.release")
      worker_release.set()

    try:
      with patch("grpc.insecure_channel", side_effect=[first_channel, second_channel]):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(release_later)
          with pytest.raises(TimeoutError) as error_info:
            async with backend:
              task_group.start_soon(anyio.to_thread.run_sync, occupy_worker)
              await worker_started.wait()

        assert isinstance(error_info.value.__context__, anyio.get_cancelled_exc_class())
        assert events.index("channel.close") < events.index("worker.release")
        assert first_channel.closed
        assert len(first_channel.get_calls(f"/{_LOCK_SVC}/UnlockServer")) == 1

        async with backend:
          pass

      assert second_channel.closed
      assert len(second_channel.get_calls(f"/{_LOCK_SVC}/UnlockServer")) == 2
    finally:
      worker_release.set()
      limiter.total_tokens = original_tokens

  async def test_dispatched_unlock_reconciles_after_deadline_before_channel_close(
    self,
  ) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    backend._CLEANUP_TIMEOUT = 0.01
    channel = _MockChannel(events=events)
    _configure_setup_channel(channel)
    unlock_started = threading.Event()
    unlock_release = threading.Event()
    unlock_attempts = 0

    def block_cleanup_unlock() -> bytes:
      nonlocal unlock_attempts
      unlock_attempts += 1
      if unlock_attempts == 2:
        unlock_started.set()
        unlock_release.wait(2)
        events.append("unlock.rpc.complete")
      return b""

    async def release_later() -> None:
      assert await anyio.to_thread.run_sync(unlock_started.wait, 1)
      await anyio.sleep(0.1)
      unlock_release.set()

    channel.set_handler(f"/{_LOCK_SVC}/UnlockServer", block_cleanup_unlock)
    try:
      with patch("grpc.insecure_channel", return_value=channel):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(release_later)
          async with backend:
            pass

      assert events.index("unlock.rpc.complete") < events.index("channel.close")
      assert not backend._locked
      assert channel.closed
    finally:
      unlock_release.set()


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


class TestObjectiveMaintenanceScope(AnyioTestBase):
  def _configure_setup_channel(self, channel: _MockChannel) -> None:
    _configure_setup_channel(channel)

  async def test_scope_enters_and_exits_maintenance(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    channel.set_response(f"/{_OBJ_SVC}/EnterObjectiveMaintenance", b"")
    channel.set_response(f"/{_OBJ_SVC}/ExitObjectiveMaintenance", b"")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        channel.calls.clear()
        async with backend.objective_maintenance(2):
          assert [call.path for call in channel.calls] == [
            f"/{_INST_SVC}/Initialize",
            f"/{_OBJ_SVC}/EnterObjectiveMaintenance",
          ]

    assert [call.path for call in channel.calls[:3]] == [
      f"/{_INST_SVC}/Initialize",
      f"/{_OBJ_SVC}/EnterObjectiveMaintenance",
      f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
    ]

  async def test_scope_rejects_open_drawer_before_rpc(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        channel.calls.clear()
        backend._door_open = True
        with pytest.raises(RuntimeError, match="plate drawer is open"):
          async with backend.objective_maintenance(1):
            pytest.fail("open drawer reached scope body")
        assert channel.calls == []

  async def test_scope_body_failure_exits_maintenance(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    failure = RuntimeError("body failed")

    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          channel.calls.clear()
          async with backend.objective_maintenance(3):
            raise failure

    assert error_info.value is failure
    assert [call.path for call in channel.calls[:3]] == [
      f"/{_INST_SVC}/Initialize",
      f"/{_OBJ_SVC}/EnterObjectiveMaintenance",
      f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
    ]

  async def test_scope_exit_failure_propagates_and_direct_exit_can_retry(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    failure = RuntimeError("exit failed")
    attempts = 0

    def fail_once() -> bytes:
      nonlocal attempts
      attempts += 1
      if attempts == 1:
        raise failure
      return b""

    channel.set_handler(f"/{_OBJ_SVC}/ExitObjectiveMaintenance", fail_once)
    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        with pytest.raises(RuntimeError) as error_info:
          async with backend.objective_maintenance(0):
            pass
        await backend.exit_objective_maintenance()

    assert error_info.value is failure
    assert attempts == 2

  async def test_repeated_durable_enter_failure_preserves_existing_maintenance(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    failure = RuntimeError("repeated enter failed")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        await backend.enter_objective_maintenance(0)
        channel.set_handler(
          f"/{_OBJ_SVC}/EnterObjectiveMaintenance",
          lambda: (_ for _ in ()).throw(failure),
        )
        with pytest.raises(RuntimeError) as error_info:
          await backend.enter_objective_maintenance(0)
        assert channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance") == []
        channel.handlers.pop(f"/{_OBJ_SVC}/EnterObjectiveMaintenance")
        await backend.exit_objective_maintenance()

    assert error_info.value is failure
    assert len(channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance")) == 1

  async def test_repeated_durable_enter_failure_preserves_prior_unknown_state(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    exit_failure = RuntimeError("exit failed")
    enter_failure = RuntimeError("repeated enter failed")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        await backend.enter_objective_maintenance(0)
        channel.set_handler(
          f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
          lambda: (_ for _ in ()).throw(exit_failure),
        )
        with pytest.raises(RuntimeError) as exit_error:
          await backend.exit_objective_maintenance()
        channel.handlers.pop(f"/{_OBJ_SVC}/ExitObjectiveMaintenance")
        channel.set_handler(
          f"/{_OBJ_SVC}/EnterObjectiveMaintenance",
          lambda: (_ for _ in ()).throw(enter_failure),
        )
        with pytest.raises(RuntimeError) as enter_error:
          await backend.enter_objective_maintenance(0)
        assert backend._objective_maintenance_active
        assert backend._objective_maintenance_state_unknown
        channel.handlers.pop(f"/{_OBJ_SVC}/EnterObjectiveMaintenance")

    assert exit_error.value is exit_failure
    assert enter_error.value is enter_failure

  async def test_repeated_durable_enter_cancellation_preserves_existing_maintenance(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    started = anyio.Event()

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        await backend.enter_objective_maintenance(0)
        original_rpc = backend._rpc

        async def cancellable_rpc(service, method, fn, with_lock):
          if method == "EnterObjectiveMaintenance":
            started.set()
            await anyio.sleep_forever()
          return await original_rpc(service, method, fn, with_lock)

        backend._rpc = cancellable_rpc  # type: ignore[method-assign]

        async with anyio.create_task_group() as task_group:
          scope = anyio.CancelScope()

          async def repeat_enter() -> None:
            with scope:
              await backend.enter_objective_maintenance(0)

          task_group.start_soon(repeat_enter)
          await started.wait()
          scope.cancel()

        assert channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance") == []
        backend._rpc = original_rpc  # type: ignore[method-assign]
        await backend.exit_objective_maintenance()

    assert len(channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance")) == 1

  async def test_body_cancellation_exits_maintenance(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        with anyio.CancelScope() as scope:
          async with backend.objective_maintenance(0):
            scope.cancel()
            await anyio.sleep_forever()
        assert len(channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance")) == 1

  async def test_exit_failure_preserves_body_failure_as_context(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    body_failure = RuntimeError("body failed")
    exit_failure = RuntimeError("exit failed")
    attempts = 0

    def fail_once() -> bytes:
      nonlocal attempts
      attempts += 1
      if attempts == 1:
        raise exit_failure
      return b""

    channel.set_handler(f"/{_OBJ_SVC}/ExitObjectiveMaintenance", fail_once)
    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          async with backend.objective_maintenance(0):
            raise body_failure

    assert error_info.value is exit_failure
    assert error_info.value.__context__ is body_failure

  async def test_scope_rejects_unknown_maintenance_state(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    attempts = 0

    def fail_once() -> bytes:
      nonlocal attempts
      attempts += 1
      if attempts == 1:
        raise RuntimeError("exit failed")
      return b""

    channel.set_handler(f"/{_OBJ_SVC}/ExitObjectiveMaintenance", fail_once)
    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        await backend.enter_objective_maintenance(0)
        with pytest.raises(RuntimeError, match="exit failed"):
          await backend.exit_objective_maintenance()
        calls = len(channel.calls)
        with pytest.raises(RuntimeError, match="already active"):
          async with backend.objective_maintenance(0):
            pytest.fail("unknown state reached body")
        assert len(channel.calls) == calls

  async def test_scope_rejects_unknown_only_state_before_rpc(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        channel.calls.clear()
        backend._objective_maintenance_active = False
        backend._objective_maintenance_state_unknown = True
        with pytest.raises(RuntimeError, match="already active"):
          async with backend.objective_maintenance(0):
            pytest.fail("unknown state reached body")
        assert channel.calls == []

  async def test_scope_rejects_nested_and_direct_transitions(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        async with backend.objective_maintenance(0):
          calls = len(channel.calls)
          with pytest.raises(RuntimeError, match="already scoped"):
            async with backend.objective_maintenance(1):
              pytest.fail("nested scope reached body")
          with pytest.raises(RuntimeError, match="scoped"):
            await backend.enter_objective_maintenance(1)
          with pytest.raises(RuntimeError, match="scoped"):
            await backend.exit_objective_maintenance()
          assert len(channel.calls) == calls

  async def test_scope_rejects_concurrent_entry(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    entered = anyio.Event()
    release = anyio.Event()

    async def hold_scope() -> None:
      async with backend.objective_maintenance(0):
        entered.set()
        await release.wait()

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(hold_scope)
          await entered.wait()
          calls = len(channel.calls)
          with pytest.raises(RuntimeError, match="already scoped"):
            async with backend.objective_maintenance(1):
              pytest.fail("concurrent scope reached body")
          assert len(channel.calls) == calls
          release.set()

  async def test_force_exit_failure_still_unlocks_and_closes_channel(self) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel(events=events)
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    failure = RuntimeError("force exit failed")

    with patch("grpc.insecure_channel", return_value=channel):
      with pytest.raises(RuntimeError) as error_info:
        async with backend:
          await backend.enter_objective_maintenance(0)
          channel.set_handler(
            f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
            lambda: (_ for _ in ()).throw(failure),
          )

    assert error_info.value is failure
    assert events[-3:] == [
      f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
      f"/{_LOCK_SVC}/UnlockServer",
      "channel.close",
    ]

  async def test_scope_rejects_durable_maintenance_and_supports_reuse(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        await backend.enter_objective_maintenance(0)
        with pytest.raises(RuntimeError, match="already active"):
          async with backend.objective_maintenance(0):
            pytest.fail("durable maintenance reached scope body")
        await backend.exit_objective_maintenance()
        async with backend.objective_maintenance(1):
          pass
        async with backend.objective_maintenance(2):
          pass

  async def test_lifespan_force_exits_before_unlock_and_invalidates_scope(self) -> None:
    events: List[str] = []
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel(events=events)
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        manager = backend.objective_maintenance(0)
        await manager.__aenter__()
      calls_after_lifespan = len(channel.calls)
      await manager.__aexit__(None, None, None)

    assert len(channel.calls) == calls_after_lifespan
    assert events[-3:] == [
      f"/{_OBJ_SVC}/ExitObjectiveMaintenance",
      f"/{_LOCK_SVC}/UnlockServer",
      "channel.close",
    ]

  async def test_cancelled_entry_waits_for_rpc_and_reconciles(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    channel.set_response(f"/{_INST_SVC}/Initialize", b"")
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    scopes: List[anyio.CancelScope] = []

    def blocking_enter() -> bytes:
      started.set()
      release.wait(2)
      completed.set()
      return b""

    channel.set_handler(f"/{_OBJ_SVC}/EnterObjectiveMaintenance", blocking_enter)

    async def enter_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend.objective_maintenance(0):
          pytest.fail("cancelled entry reached scope body")

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(enter_scope)
          assert await anyio.to_thread.run_sync(started.wait, 1)
          scopes[0].cancel()
          await anyio.sleep(0)
          assert not completed.is_set()
          assert not channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance")
          release.set()

        assert completed.is_set()
        assert len(channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance")) == 1

  async def test_cancelled_entry_before_worker_dispatch_does_not_send_maintenance_rpc(self) -> None:
    backend = ExperimentalPicoBackend(host="127.0.0.1")
    channel = _MockChannel()
    self._configure_setup_channel(channel)
    worker_started = anyio.Event()
    worker_release = threading.Event()
    entry_started = anyio.Event()
    entry_finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    def occupy_worker() -> None:
      anyio.from_thread.run_sync(worker_started.set)
      worker_release.wait()

    async def initialize_without_rpc() -> None:
      return None

    async def enter_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          entry_started.set()
          async with backend.objective_maintenance(0):
            pytest.fail("cancelled pre-dispatch entry reached scope body")
      finally:
        entry_finished.set()

    with patch("grpc.insecure_channel", return_value=channel):
      async with backend:
        channel.calls.clear()
        backend._initialize = initialize_without_rpc  # type: ignore[method-assign]
        limiter = anyio.to_thread.current_default_thread_limiter()
        original_tokens = limiter.total_tokens
        limiter.total_tokens = 1
        try:
          async with anyio.create_task_group() as task_group:
            try:
              task_group.start_soon(anyio.to_thread.run_sync, occupy_worker)
              await worker_started.wait()
              task_group.start_soon(enter_scope)
              await entry_started.wait()
              await anyio.wait_all_tasks_blocked()
              scopes[0].cancel()
              with anyio.move_on_after(1):
                await entry_finished.wait()
              returned_before_worker_release = entry_finished.is_set()
            finally:
              worker_release.set()

          assert returned_before_worker_release
          assert channel.get_calls(f"/{_OBJ_SVC}/EnterObjectiveMaintenance") == []
          assert channel.get_calls(f"/{_OBJ_SVC}/ExitObjectiveMaintenance") == []
          assert backend._objective_maintenance_scope_token is None
          assert not backend._objective_maintenance_active
          assert not backend._objective_maintenance_state_unknown
        finally:
          limiter.total_tokens = original_tokens


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


if __name__ == "__main__":
  unittest.main()

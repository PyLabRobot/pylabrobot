"""Hardware adapter behavior over real TLS RPCs, with a fake PLR driver only."""

import asyncio
import hashlib
import io
from contextlib import asynccontextmanager
from dataclasses import replace

import grpc
import numpy as np
import pytest
import pytest_asyncio
from PIL import Image

from pylabrobot.revvity.celigo.camera import CameraFrame
from pylabrobot.revvity.celigo.celigo import AcquisitionResult, ControllerStatus
from pylabrobot.revvity.celigo.config import DigitalIOConfig, IlluminationChannelConfig, IOConfig
from pylabrobot.revvity.celigo.grpc.client import (
  CeligoClient,
  PrototypeClient,
  acquisition_metadata,
)
from pylabrobot.revvity.celigo.grpc.hardware import CeligoCore, CeligoServer
from pylabrobot.revvity.celigo.grpc.storage import Store
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.revvity.celigo.tests.helpers import make_linear_axis_config, make_test_config
from pylabrobot.rpc.errors import BusyError, StateError, StorageError
from pylabrobot.rpc.security import create_certificates
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


@pytest.fixture
def config():
  value = make_test_config()
  value.calibration = replace(
    value.calibration, image_width_pixels=64, image_height_pixels=48, calibrated_z_position=2.6
  )
  value.hardware.x_axis = make_linear_axis_config(min_position=-150, max_position=150)
  value.hardware.y_axis = make_linear_axis_config(min_position=-100, max_position=100)
  value.hardware.z_axis = make_linear_axis_config(min_position=0, max_position=6.5)
  value.hardware.io = IOConfig(analog_ins=[], digital_ios=[], lighting_ios=[])
  value.channels_by_magnification[value.magnification] = {
    "brightfield": IlluminationChannelConfig(
      name="brightfield",
      display_name="Brightfield",
      logical_filter=1,
      bit_value=None,
      intensity_percent=10,
      lighting_io_name="brightfield",
      strobe=False,
      z_offset_to_brightfield_mm=0,
      mm_per_pixel_x_correction_to_brightfield=1,
      mm_per_pixel_y_correction_to_brightfield=1,
    )
  }
  return value


class FakeDriver:
  def __init__(self):
    self.calls = []
    self.interlock_open = False
    self.controller_flags = 0
    self.door_input_high = True
    self.door_input_after_move = None
    self.capture_error = False
    self.stop_error = False
    self.illuminated = False
    self.capture_entered = asyncio.Event()
    self.stop_entered = asyncio.Event()
    self.capture_gate = asyncio.Event()
    self.capture_gate.set()
    self.stop_gate = asyncio.Event()
    self.stop_gate.set()
    self.pixels = np.arange(64 * 48, dtype=np.uint16).reshape(48, 64)

  async def setup(self):
    self.calls.append("setup")

  async def stop(self):
    self.calls.append("stop")
    self.stop_entered.set()
    await self.stop_gate.wait()
    self.illuminated = False
    if self.stop_error:
      raise RuntimeError("fake cleanup failure")

  async def request_controller_status(self):
    self.calls.append("status")
    return ControllerStatus(self.controller_flags | (4 if self.interlock_open else 0), 0)

  async def request_digital_input(self, bit_index):
    assert bit_index == 5
    self.calls.append("door")
    return self.door_input_high

  async def move_to_scan_block(self, block):
    self.calls.append("move")
    if self.door_input_after_move is not None:
      self.door_input_high = self.door_input_after_move
    return block.stage_x_mm + 0.001, block.stage_y_mm + 0.002

  async def acquire_scan_position(self, **kwargs):
    assert kwargs["channel"] == "brightfield"
    assert kwargs["autofocus"] is None and kwargs["machine_auto_exposure"] is False
    self.calls.append("capture")
    self.illuminated = True
    self.capture_entered.set()
    try:
      await self.capture_gate.wait()
      if self.capture_error:
        raise RuntimeError("fake camera failure")
      frame = CameraFrame(
        self.pixels.tobytes(),
        64,
        48,
        16,
        kwargs["exposure_ms"] + 0.25,
        kwargs["gain"] + 0.1,
        1700000000.125,
      )
      x, y = kwargs["settled_stage_position_mm"]
      return AcquisitionResult(
        kwargs["label"],
        "brightfield",
        x,
        y,
        kwargs["z_mm"] + 0.001,
        frame,
        None,
        (0.2, 0.3),
      )
    finally:
      self.illuminated = False
      self.calls.append("off")


@asynccontextmanager
async def hardware_system(tmp_path, config, certs=None):
  certs = certs or create_certificates(tmp_path / "certs")
  driver = FakeDriver()
  created = []

  def factory():
    created.append(driver)
    return driver

  core = CeligoCore(tmp_path / "data", "celigo-test", config, factory, tmp_path / "hardware.lock")
  server = CeligoServer(certs, core)
  await server.start()
  try:
    async with CeligoClient(server.endpoint, certs, device_id="celigo-test") as client:
      yield server, client, driver, created
  finally:
    driver.capture_gate.set()
    driver.stop_gate.set()
    driver.stop_error = False
    await server.stop()


@pytest_asyncio.fixture
async def hardware(tmp_path, config):
  async with hardware_system(tmp_path, config) as system:
    yield system


async def initialize(client):
  job = await client.create_initialization("initialize")
  await job.start()
  assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
  return job


async def capture(client, request_id="capture", **changes):
  settings = {
    "sample_x_mm": 63.5,
    "sample_y_mm": 43.0,
    "z_mm": 2.6,
    "exposure_ms": 1.0,
    "gain": 1.0,
  }
  settings.update(changes)
  return await client.create_brightfield_capture(request_id, **settings)


@pytest.mark.asyncio
async def test_startup_inspection_and_job_creation_never_construct_driver(hardware):
  _, client, driver, created = hardware
  assert (await client.device_info()).device_type == "Celigo"
  assert (await client.configuration()).channels == ["brightfield"]
  assert (await client.status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED
  job = await capture(client)
  assert (await capture(client)).identifier == job.identifier
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await job.start()
  assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
  await job.cancel()
  await client.create_initialization("ready-initialize")
  assert created == [] and driver.calls == []


@pytest.mark.asyncio
async def test_generic_controller_interlock_does_not_block_brightfield(hardware):
  _, client, driver, _ = hardware
  driver.interlock_open = True
  await initialize(client)
  job = await capture(client)
  await job.start()
  assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
  assert len(await job.list_frames()) == 1
  assert "door" not in driver.calls


@pytest.mark.asyncio
async def test_physical_service_has_no_simulation_rpc(hardware):
  server, client, _, created = hardware
  with pytest.raises(ValueError, match="only accepts a CeligoSimulator"):
    async with PrototypeClient(server.endpoint, client.certs, device_id="celigo-test"):
      pass
  stub = rpc.SimulationServiceStub(client.channel)
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await stub.CreateInitialization(
      job_pb.RequestLookup(device_id="celigo-test", request_id="fake")
    )
  assert error.value.code() == grpc.StatusCode.UNIMPLEMENTED
  assert created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "changes",
  [
    {"sample_x_mm": float("nan")},
    {"sample_x_mm": 100000},
    {"z_mm": 100},
    {"exposure_ms": 0},
    {"exposure_ms": 1001},
    {"gain": 17},
  ],
)
async def test_invalid_settings_rejected_before_hardware(hardware, changes):
  _, client, _, created = hardware
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await capture(client, **changes)
  assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
  assert created == []


@pytest.mark.asyncio
async def test_absent_fields_and_stale_configuration_rejected(hardware):
  _, client, _, created = hardware
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await client.operations.CreateBrightfieldCapture(
      pb.BrightfieldRequest(
        device_id=client.device_id,
        request_id="absent",
        configuration_id=client.configuration_id,
        settings=pb.BrightfieldSettings(sample_x_mm=63.5),
      )
    )
  assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
  client.configuration_id = "stale"
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await capture(client)
  assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
  assert created == []


@pytest.mark.asyncio
async def test_capture_uses_public_plr_sequence_and_preserves_pixels_and_actual_metadata(hardware):
  _, client, driver, created = hardware
  await initialize(client)
  job = await capture(client)
  await job.start()
  assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
  assert len(created) == 1 and driver.calls == [
    "setup",
    "status",
    "status",
    "move",
    "status",
    "capture",
    "off",
  ]
  assert not driver.illuminated
  info = (await job.list_frames())[0]
  frame = await job.get_frame(info.frame_id)
  assert hashlib.sha256(frame.image).hexdigest() == info.sha256
  np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(frame.image))), driver.pixels)
  metadata = acquisition_metadata(frame)
  assert metadata.sample_x_mm == 63.5
  assert metadata.stage_x_mm != 63.5
  assert metadata.exposure_ms == 1.25 and metadata.gain == 1.1
  assert metadata.z_mm == pytest.approx(2.601)
  assert metadata.configuration_id == client.configuration_id
  assert frame.captured_at.seconds == 1700000000 and frame.captured_at.nanos == 125000000
  again = await client.create_initialization("reinitialize")
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await again.start()
  assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.asyncio
async def test_cancel_keeps_ownership_until_capture_and_cleanup_finish(hardware):
  _, client, driver, _ = hardware
  await initialize(client)
  driver.capture_gate.clear()
  driver.stop_gate.clear()
  job = await capture(client)
  other = await capture(client, "other")
  await job.start()
  await asyncio.wait_for(driver.capture_entered.wait(), 5)
  await job.cancel()
  assert (await job.status()).state == job_pb.JOB_STATE_RUNNING
  driver.capture_gate.set()
  await asyncio.wait_for(driver.stop_entered.wait(), 5)
  assert (await job.status()).state == job_pb.JOB_STATE_RUNNING
  with pytest.raises(grpc.aio.AioRpcError) as busy:
    await other.start()
  assert busy.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
  driver.stop_gate.set()
  final = await job.wait()
  assert final.outcome == job_pb.OUTCOME_CANCELLED and final.completed == 1
  assert not driver.illuminated
  assert (await client.status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED


@pytest.mark.asyncio
async def test_client_disconnection_does_not_cancel_capture(hardware):
  _, client, driver, _ = hardware
  await initialize(client)
  driver.capture_gate.clear()
  job = await capture(client)
  await job.start()
  await asyncio.wait_for(driver.capture_entered.wait(), 5)
  await client.disconnect()
  driver.capture_gate.set()
  await client.connect()
  recovered = await client.recover("capture")
  assert recovered.identifier == job.identifier
  assert (await recovered.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
  assert driver.calls.count("capture") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["controller", "camera", "cleanup"])
async def test_hardware_failure_cleans_up_and_faults_without_retry(hardware, failure):
  _, client, driver, _ = hardware
  await initialize(client)
  job = await capture(client)
  driver.controller_flags = 2 if failure == "controller" else 0
  driver.capture_error = failure in ("camera", "cleanup")
  driver.stop_error = failure == "cleanup"
  await job.start()
  final = await job.wait()
  assert final.outcome == job_pb.OUTCOME_FAILED
  assert (await client.status()).state == device_pb.OPERATIONAL_STATE_FAULTED
  assert "stop" in driver.calls
  if failure == "controller":
    assert "move" not in driver.calls and "capture" not in driver.calls
  else:
    assert driver.calls.count("capture") == 1
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await client.create_initialization("retry")
  assert error.value.code() == grpc.StatusCode.UNAVAILABLE


@pytest.mark.asyncio
async def test_start_commit_failure_never_constructs_driver(hardware, monkeypatch):
  server, client, _, created = hardware
  job = await client.create_initialization("initialize")

  def fail(*_args):
    raise StorageError("disk full")

  monkeypatch.setattr(server.simulator.store, "save", fail)
  with pytest.raises(grpc.aio.AioRpcError) as error:
    await job.start()
  assert error.value.code() == grpc.StatusCode.UNAVAILABLE
  assert created == []


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [2, 8])
async def test_controller_fault_after_setup_prevents_ready_and_capture(hardware, flags):
  _, client, driver, _ = hardware
  driver.controller_flags = flags
  job = await client.create_initialization("controller-fault")
  await job.start()
  final = await job.wait()
  assert final.outcome == job_pb.OUTCOME_FAILED
  assert "controller fault" in final.error
  assert driver.calls == ["setup", "status", "stop"]
  assert (await client.status()).state == device_pb.OPERATIONAL_STATE_FAULTED
  assert not await job.list_frames()


@pytest.mark.asyncio
@pytest.mark.parametrize("inverted", [False, True])
@pytest.mark.parametrize("phase", ["initialization", "before_move", "after_move"])
async def test_configured_door_input_blocks_imaging(tmp_path, config, inverted, phase):
  door = DigitalIOConfig(1000, "In", 5, inverted, True, "DoorInterlock")
  config = replace(
    config,
    hardware=replace(config.hardware, io=replace(config.hardware.io, digital_ios=[door])),
  )
  async with hardware_system(tmp_path, config) as (_, client, driver, _):
    driver.door_input_high = not inverted
    if phase == "initialization":
      driver.door_input_high = inverted
      job = await client.create_initialization("open-door")
    else:
      await initialize(client)
      job = await capture(client)
      if phase == "before_move":
        driver.door_input_high = inverted
      else:
        driver.door_input_after_move = inverted
    await job.start()
    final = await job.wait()
    assert final.outcome == job_pb.OUTCOME_FAILED
    assert "configured door interlock is open" in final.error
    assert "capture" not in driver.calls
    assert ("move" in driver.calls) == (phase == "after_move")
    assert driver.calls[-1] == "stop"


@pytest.mark.asyncio
async def test_unexpected_executor_cancel_waits_for_driver_and_cannot_report_success(hardware):
  server, client, driver, _ = hardware
  await initialize(client)
  driver.capture_gate.clear()
  job = await capture(client)
  await job.start()
  await asyncio.wait_for(driver.capture_entered.wait(), 5)
  execution = server.simulator.jobs[job.identifier]
  execution.task.cancel()
  await asyncio.sleep(0)
  assert (await job.status()).state == job_pb.JOB_STATE_RUNNING
  assert server.simulator.active_job is execution
  driver.capture_gate.set()
  assert (await job.wait()).outcome == job_pb.OUTCOME_INTERRUPTED
  assert "stop" in driver.calls and not driver.illuminated


@pytest.mark.asyncio
async def test_restart_restores_images_and_ready_intent_without_constructing_driver(
  tmp_path, config
):
  certs = create_certificates(tmp_path / "certs")
  async with hardware_system(tmp_path, config, certs) as (_, client, _, _):
    await initialize(client)
    job = await capture(client)
    ready = await capture(client, "ready")
    await job.start()
    await job.wait()
    frame_info = (await job.list_frames())[0]
    frame = await job.get_frame(frame_info.frame_id)
  async with hardware_system(tmp_path, config, certs) as (_, client, driver, created):
    assert created == [] and driver.calls == []
    recovered = await client.recover("capture")
    assert await recovered.get_frame(frame_info.frame_id) == frame
    inert = await client.recover("ready")
    assert inert.identifier == ready.identifier
    assert (await inert.status()).state == job_pb.JOB_STATE_READY
    with pytest.raises(grpc.aio.AioRpcError):
      await inert.start()
    assert created == []


@pytest.mark.asyncio
async def test_service_lock_and_store_codec_prevent_cross_backend_execution(tmp_path, config):
  certs = create_certificates(tmp_path / "certs")
  async with hardware_system(tmp_path, config, certs):
    other = CeligoCore(
      tmp_path / "other", "celigo-test", config, FakeDriver, tmp_path / "hardware.lock"
    )
    with pytest.raises(BusyError):
      await other.open()
    await other.stop()
  store = Store(tmp_path / "data", "celigo-test")
  with pytest.raises(StateError, match="specification format"):
    await store.open()

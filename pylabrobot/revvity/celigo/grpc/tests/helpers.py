"""Mocked PLR driver and calibration for exercising the actual Celigo RPC service."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import create_autospec

import numpy as np

from pylabrobot.revvity.celigo.camera import CameraFrame
from pylabrobot.revvity.celigo.celigo import AcquisitionResult, ControllerStatus
from pylabrobot.revvity.celigo.config import IlluminationChannelConfig, IOConfig
from pylabrobot.revvity.celigo.grpc.hardware import CeligoCore, CeligoServer, Driver
from pylabrobot.revvity.celigo.grpc.storage import DEFAULT_QUOTA
from pylabrobot.revvity.celigo.tests.helpers import make_linear_axis_config, make_test_config
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


def make_config(width=64, height=48):
  value = make_test_config()
  value.calibration = replace(
    value.calibration,
    image_width_pixels=width,
    image_height_pixels=height,
    calibrated_z_position=2.6,
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


def make_driver(width=64, height=48, depth=16):
  """Mock the existing asynchronous driver interface with a known camera response."""
  driver = create_autospec(Driver, instance=True, spec_set=True)
  dtype = np.uint16 if depth == 16 else np.uint8
  pixels = np.random.default_rng(1).integers(0, 2**depth, size=(height, width), dtype=dtype)
  frame = CameraFrame(pixels.tobytes(), width, height, depth, 1.25, 1.1, 1700000000.125)
  driver.request_controller_status.return_value = ControllerStatus(0, 0)
  driver.request_digital_input.return_value = True
  driver.move_to_scan_block.return_value = (1.001, 2.002)
  driver.acquire_scan_position.return_value = AcquisitionResult(
    "capture", "brightfield", 1.001, 2.002, 2.601, frame, None, (0.2, 0.3)
  )
  return driver


@contextmanager
def block_calls(method):
  """Hold an AsyncMock call until the test releases it, including on assertion failure."""
  entered, release = asyncio.Event(), asyncio.Event()

  async def wait(*_args, **_kwargs):
    entered.set()
    await release.wait()
    return method.return_value

  method.side_effect = wait
  try:
    yield entered, release
  finally:
    release.set()
    method.side_effect = None


def make_server(
  directory,
  certs,
  *,
  config=None,
  driver=None,
  device_id="celigo-test",
  storage_quota=DEFAULT_QUOTA,
  max_jobs=1024,
  connection_id="default",
):
  """Construct the real service with only its PLR driver replaced."""
  config = config or make_config()
  driver = driver or make_driver(
    config.calibration.image_width_pixels, config.calibration.image_height_pixels
  )
  core = CeligoCore(
    directory,
    device_id,
    config,
    lambda: driver,
    directory / "hardware.lock",
    connection_id=connection_id,
  )
  core.store.quota = storage_quota
  core.store.max_jobs = max_jobs
  return CeligoServer(certs, core), driver


async def initialize(client, request_id="initialize"):
  job = await client.create_initialization(request_id)
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

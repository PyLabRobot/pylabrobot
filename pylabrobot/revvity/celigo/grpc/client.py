"""Typed Celigo operations composed with shared device, job, and image clients."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from typing_extensions import Self

from pylabrobot.revvity.celigo.grpc.limits import MAX_MESSAGE_BYTES
from pylabrobot.revvity.celigo.grpc.remote_job import CeligoImagingJob
from pylabrobot.revvity.celigo.grpc.scan_serialization import (
  decode_scan_plan,
  encode_estimate_model,
  encode_scan_plan,
  encode_scan_specification,
)
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.revvity.celigo.scan import ScanEstimateModel, ScanPlan, ScanSpec
from pylabrobot.rpc.device import DeviceClient
from pylabrobot.rpc.images import SERVICE_NAME as IMAGE_SERVICE
from pylabrobot.rpc.images import ImageClient
from pylabrobot.rpc.jobs import SERVICE_NAME as JOB_SERVICE
from pylabrobot.rpc.jobs import JobClient
from pylabrobot.rpc.transport import Updates
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


def unpack_acquisition_metadata(frame: image_pb.ImageFrame) -> pb.AcquisitionMetadata:
  """Decode Celigo metadata explicitly, rejecting a different device's metadata type."""
  result = pb.AcquisitionMetadata()
  if not frame.metadata.Unpack(result):
    raise ValueError("image does not contain Celigo acquisition metadata")
  return result


class CeligoClient(DeviceClient):
  """Expose Celigo operations with shared job and image clients on one channel."""

  expected_device_type = "Celigo"

  def __init__(self, endpoint: str, device_id: str = "celigo-01", timeout: float = 20) -> None:
    """Configure image-sized message limits without imposing them on other devices."""
    super().__init__(endpoint, device_id, timeout, maximum_message_bytes=MAX_MESSAGE_BYTES)
    self.jobs: JobClient
    self.images: ImageClient
    self.operations: rpc.CeligoServiceAsyncStub

  async def connect(self) -> Self:
    """Validate device identity and required capabilities before binding optional clients."""
    await super().connect()
    try:
      info = await self.get_device_info()
      if not {JOB_SERVICE, IMAGE_SERVICE}.issubset(info.services):
        raise ValueError("device does not advertise the required job and image capabilities")
      assert self.channel is not None
      self.jobs = JobClient(self.channel, self.device_id, self.timeout)
      self.images = ImageClient(self.channel, self.device_id, self.timeout)
      self.operations = rpc.CeligoServiceStub(self.channel)
    except BaseException:
      await self.disconnect()
      raise
    return self

  def bind_job(self, ref: job_pb.JobRef) -> CeligoImagingJob:
    """Bind a typed creation result to this device and its image client."""
    job = self.jobs.bind_job(ref)
    return CeligoImagingJob(self.jobs.stub, self.images, self.operations, job.ref, self.timeout)

  async def recover_job(self, request_id: str) -> CeligoImagingJob:
    """Recover the original operation without creating another acquisition."""
    return self.bind_job((await self.jobs.recover_job(request_id)).ref)

  @asynccontextmanager
  async def watch_previews(self) -> AsyncIterator[Updates[image_pb.ImageFrame]]:
    """Observe complete previews without starting capture."""
    async with self.images.watch_previews() as updates:
      yield updates

  async def get_configuration(self) -> pb.CeligoConfiguration:
    """Read the calibration-derived configuration for request binding."""
    return await self.operations.GetConfiguration(self.request, timeout=self.timeout)

  async def build_scan_plan(
    self, spec: ScanSpec, *, estimate_model: ScanEstimateModel | None = None
  ) -> ScanPlan:
    """Compile a PLR scan specification using the remote instrument's calibration."""
    return decode_scan_plan(
      await self.operations.BuildScanPlan(
        pb.BuildScanPlanRequest(
          device_id=self.device_id,
          specification=encode_scan_specification(spec),
          estimate_model=encode_estimate_model(estimate_model)
          if estimate_model is not None
          else None,
        ),
        timeout=self.timeout,
      )
    )

  async def create_scan(self, request_id: str, *, plan: ScanPlan) -> CeligoImagingJob:
    """Create an inert scan job from the inspected plan without capture overrides."""
    return self.bind_job(
      await self.operations.CreateScan(
        pb.CreateScanRequest(
          device_id=self.device_id,
          request_id=request_id,
          configuration_id=self.configuration_id,
          plan=encode_scan_plan(plan),
        ),
        timeout=self.timeout,
      )
    )

  async def open_door(self) -> None:
    """Eject the initialized instrument's drawer and wait for motion to finish."""
    await self.operations.OpenDoor(self.request, timeout=self.timeout)

  async def close_door(self, *, sample_x_mm: float, sample_y_mm: float) -> None:
    """Load the drawer at sample-relative XY and wait for motion to finish."""
    await self.operations.CloseDoor(
      pb.CloseDoorRequest(
        device_id=self.device_id, sample_x_mm=sample_x_mm, sample_y_mm=sample_y_mm
      ),
      timeout=self.timeout,
    )

  async def create_initialization(self, request_id: str) -> CeligoImagingJob:
    """Create inert initialization tied to the current hardware configuration."""
    ref = await self.operations.CreateInitialization(
      pb.InitializationRequest(
        device_id=self.device_id, request_id=request_id, configuration_id=self.configuration_id
      ),
      timeout=self.timeout,
    )
    return self.bind_job(ref)

  async def create_brightfield_capture(
    self,
    request_id: str,
    *,
    sample_x_mm: float,
    sample_y_mm: float,
    z_mm: float,
    exposure_ms: float,
    gain: float,
  ) -> CeligoImagingJob:
    """Create one inert fixed-Z capture with explicit physical units in the wire contract."""
    ref = await self.operations.CreateBrightfieldCapture(
      pb.BrightfieldRequest(
        device_id=self.device_id,
        request_id=request_id,
        configuration_id=self.configuration_id,
        settings=pb.BrightfieldSettings(
          sample_x_mm=sample_x_mm,
          sample_y_mm=sample_y_mm,
          z_mm=z_mm,
          exposure_ms=exposure_ms,
          gain=gain,
        ),
      ),
      timeout=self.timeout,
    )
    return self.bind_job(ref)

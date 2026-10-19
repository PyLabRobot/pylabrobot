"""Typed Celigo operations composed with shared device, job, and image clients."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from typing_extensions import Self

from pylabrobot.revvity.celigo.grpc.model import MAX_MESSAGE_BYTES
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.rpc.device import DeviceClient
from pylabrobot.rpc.images import SERVICE_NAME as IMAGE_SERVICE
from pylabrobot.rpc.images import ImageClient
from pylabrobot.rpc.jobs import SERVICE_NAME as JOB_SERVICE
from pylabrobot.rpc.jobs import Job, JobClient
from pylabrobot.rpc.transport import Updates
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb
from pylabrobot.rpc.v1 import jobs_pb2_grpc as job_rpc


def acquisition_metadata(frame: image_pb.ImageFrame) -> pb.AcquisitionMetadata:
  """Decode Celigo metadata explicitly, rejecting a different device's metadata type."""
  result = pb.AcquisitionMetadata()
  if not frame.metadata.Unpack(result):
    raise ValueError("image does not contain Celigo acquisition metadata")
  return result


class ImagingJob(Job):
  """Celigo job convenience methods combining independent job and image capabilities."""

  def __init__(
    self, stub: job_rpc.JobServiceStub, images: ImageClient, ref: job_pb.JobRef, timeout: float = 20
  ) -> None:
    """Bind optional image access alongside the common job handle."""
    super().__init__(stub, ref, timeout)
    self.images = images

  async def release_images(self) -> image_pb.ImageRetention:
    """Explicitly release retained pixels after archiving them elsewhere."""
    return await self.images.release(self.ref)

  async def image_retention(self) -> image_pb.ImageRetention:
    """Read pixel availability separately from common job state."""
    return await self.images.retention(self.ref)

  async def list_frames(self, after_sequence: int = 0, limit: int = 64) -> list[image_pb.FrameInfo]:
    """Read one page of metadata for images owned by this job."""
    return await self.images.list_frames(self.ref, after_sequence, limit)

  async def get_frame(self, frame_id: str) -> image_pb.ImageFrame:
    """Retrieve a whole image and its typed acquisition metadata envelope."""
    return await self.images.get_frame(self.ref, frame_id)


class ImagingClient(DeviceClient):
  """Compose the Celigo job and image capabilities."""

  def __init__(self, endpoint: str, device_id: str = "celigo-01", timeout: float = 20) -> None:
    """Configure image-sized message limits without imposing them on other devices."""
    super().__init__(endpoint, device_id, timeout, maximum_message_bytes=MAX_MESSAGE_BYTES)
    self.jobs: JobClient
    self.images: ImageClient

  async def connect(self) -> Self:
    """Validate device identity and required capabilities before binding optional clients."""
    await super().connect()
    try:
      info = await self.device_info()
      if not {JOB_SERVICE, IMAGE_SERVICE}.issubset(info.services):
        raise ValueError("device does not advertise the required job and image capabilities")
      assert self.channel is not None
      self.jobs = JobClient(self.channel, self.device_id, self.timeout)
      self.images = ImageClient(self.channel, self.device_id, self.timeout)
    except BaseException:
      await self.disconnect()
      raise
    return self

  def imaging_job(self, ref: job_pb.JobRef) -> ImagingJob:
    """Bind a typed creation result to this device and its image client."""
    job = self.jobs.bind(ref)
    return ImagingJob(self.jobs.stub, self.images, job.ref, self.timeout)

  async def recover(self, request_id: str) -> ImagingJob:
    """Recover the original operation without creating another acquisition."""
    return self.imaging_job((await self.jobs.recover(request_id)).ref)

  @asynccontextmanager
  async def previews(self) -> AsyncIterator[Updates[image_pb.ImageFrame]]:
    """Observe complete previews without starting capture."""
    async with self.images.previews() as updates:
      yield updates


class CeligoClient(ImagingClient):
  """Typed physical Celigo operations, separated from common dashboard state."""

  expected_device_type = "Celigo"

  def __init__(self, endpoint: str, device_id: str = "celigo-01", timeout: float = 20) -> None:
    """Configure a connection without creating or initializing a hardware driver."""
    super().__init__(endpoint, device_id, timeout)

  async def connect(self) -> Self:
    """Bind physical operations after validating identity and capabilities."""
    await super().connect()
    assert self.channel is not None
    self.operations = rpc.CeligoServiceStub(self.channel)
    return self

  async def configuration(self) -> pb.CeligoConfiguration:
    """Read the calibration-derived configuration for request binding."""
    return cast(
      pb.CeligoConfiguration,
      await self.operations.GetConfiguration(self.request, timeout=self.timeout),
    )

  async def create_initialization(self, request_id: str) -> ImagingJob:
    """Create inert initialization tied to the current hardware configuration."""
    ref = await self.operations.CreateInitialization(
      pb.InitializationRequest(
        device_id=self.device_id, request_id=request_id, configuration_id=self.configuration_id
      ),
      timeout=self.timeout,
    )
    return self.imaging_job(ref)

  async def create_brightfield_capture(
    self,
    request_id: str,
    *,
    sample_x_mm: float,
    sample_y_mm: float,
    z_mm: float,
    exposure_ms: float,
    gain: float,
  ) -> ImagingJob:
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
    return self.imaging_job(ref)

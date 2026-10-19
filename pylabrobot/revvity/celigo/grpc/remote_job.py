"""Client handle for a Celigo operation and its remotely retained images."""

from __future__ import annotations

from pylabrobot.rpc.images import ImageClient
from pylabrobot.rpc.jobs import Job
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb
from pylabrobot.rpc.v1 import jobs_pb2_grpc as job_rpc


class CeligoImagingJob(Job):
  """Celigo job convenience methods combining independent job and image capabilities."""

  def __init__(
    self,
    stub: job_rpc.JobServiceAsyncStub,
    images: ImageClient,
    ref: job_pb.JobRef,
    timeout: float = 20,
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

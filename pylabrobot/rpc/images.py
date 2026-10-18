"""Optional whole-image transport; codecs, acquisition metadata, and storage stay external."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Protocol, cast

import grpc

from pylabrobot.rpc.transport import Updates, require_device, rpc_errors
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import images_pb2 as pb
from pylabrobot.rpc.v1 import images_pb2_grpc as rpc
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

SERVICE_NAME = "pylabrobot.rpc.v1.ImageService"


class ImageBackend(Protocol):
  """Device-owned retention and image delivery; this interface never acquires pixels."""

  @property
  def device_id(self) -> str:
    """Return the instrument owning these images."""
    ...

  def image_capabilities(self) -> pb.ImageCapabilities:
    """Describe supported representations and payload/retention limits."""
    ...

  async def image_retention(self, job_id: str) -> pb.ImageRetention:
    """Read whether a job's images have been explicitly released."""
    ...

  async def release_images(self, job_id: str) -> pb.ImageRetention:
    """Release pixels after durable completion while preserving request identity."""
    ...

  async def list_frames(self, job_id: str, after_sequence: int, limit: int) -> pb.FramePage:
    """Enumerate metadata using an exclusive sequence cursor."""
    ...

  async def get_frame(self, job_id: str, frame_id: str) -> pb.ImageFrame:
    """Read one complete retained image without triggering another acquisition."""
    ...

  def watch_preview(self) -> AsyncGenerator[pb.ImageFrame, None]:
    """Observe complete previews without starting capture."""
    ...


class ImageService(rpc.ImageServiceServicer):
  """Map whole-image requests onto a device's optional retention capability."""

  def __init__(self, backend: ImageBackend) -> None:
    """Bind image delivery without selecting a codec or storage implementation."""
    self.backend = backend

  async def GetCapabilities(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> pb.ImageCapabilities:
    """Read limits specific to image-capable devices."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return self.backend.image_capabilities()

  async def GetJobImageRetention(
    self, request: job_pb.JobRef, context: grpc.aio.ServicerContext
  ) -> pb.ImageRetention:
    """Read pixel retention separately from generic job status."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return await self.backend.image_retention(request.job_id)

  async def ReleaseJobImages(
    self, request: job_pb.JobRef, context: grpc.aio.ServicerContext
  ) -> pb.ImageRetention:
    """Request explicit release under the device's durability rules."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return await self.backend.release_images(request.job_id)

  async def ListFrames(
    self, request: pb.ListFramesRequest, context: grpc.aio.ServicerContext
  ) -> pb.FramePage:
    """Page frame metadata, never image bytes."""
    async with rpc_errors(context):
      require_device(request.job.device_id, self.backend.device_id)
      return await self.backend.list_frames(
        request.job.job_id, request.after_sequence, request.limit
      )

  async def GetFrame(
    self, request: pb.GetFrameRequest, context: grpc.aio.ServicerContext
  ) -> pb.ImageFrame:
    """Deliver one complete encoded image."""
    async with rpc_errors(context):
      require_device(request.job.device_id, self.backend.device_id)
      return await self.backend.get_frame(request.job.job_id, request.frame_id)

  async def WatchPreview(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> AsyncIterator[pb.ImageFrame]:
    """Observe latest complete previews without owning an acquisition task."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      async with aclosing(self.backend.watch_preview()) as updates:
        async for frame in updates:
          yield frame


class ImageClient:
  """Compose binary image support without depending on NumPy, Pillow, or Celigo."""

  def __init__(self, channel: grpc.aio.Channel, device_id: str, timeout: float = 20) -> None:
    """Use a channel configured for the device's maximum complete-image message size."""
    self.stub = rpc.ImageServiceStub(channel)
    self.device_id = device_id
    self.timeout = timeout

  async def capabilities(self) -> pb.ImageCapabilities:
    """Read image limits without pulling them into common device identity."""
    return cast(
      pb.ImageCapabilities,
      await self.stub.GetCapabilities(
        device_pb.DeviceRequest(device_id=self.device_id), timeout=self.timeout
      ),
    )

  async def retention(self, job: job_pb.JobRef) -> pb.ImageRetention:
    """Read whether retained pixels were explicitly released."""
    require_device(job.device_id, self.device_id)
    return cast(pb.ImageRetention, await self.stub.GetJobImageRetention(job, timeout=self.timeout))

  async def release(self, job: job_pb.JobRef) -> pb.ImageRetention:
    """Explicitly release a finished job's pixels after the application archives them."""
    require_device(job.device_id, self.device_id)
    return cast(pb.ImageRetention, await self.stub.ReleaseJobImages(job, timeout=self.timeout))

  async def list_frames(
    self, job: job_pb.JobRef, after_sequence: int = 0, limit: int = 64
  ) -> list[pb.FrameInfo]:
    """Read one metadata page for the owning job."""
    require_device(job.device_id, self.device_id)
    page = await self.stub.ListFrames(
      pb.ListFramesRequest(job=job, after_sequence=after_sequence, limit=limit),
      timeout=self.timeout,
    )
    return list(page.frames)

  async def get_frame(self, job: job_pb.JobRef, frame_id: str) -> pb.ImageFrame:
    """Retrieve complete bytes and typed metadata without decoding the image."""
    require_device(job.device_id, self.device_id)
    return cast(
      pb.ImageFrame,
      await self.stub.GetFrame(
        pb.GetFrameRequest(job=job, frame_id=frame_id), timeout=self.timeout
      ),
    )

  @asynccontextmanager
  async def previews(self) -> AsyncIterator[Updates[pb.ImageFrame]]:
    """Watch previews; context exit cancels only this subscription."""
    call = self.stub.WatchPreview(device_pb.DeviceRequest(device_id=self.device_id))
    try:
      yield Updates[pb.ImageFrame](call)
    finally:
      call.cancel()

"""Adapt Celigo's durable acquisition engine to the shared optional RPC capabilities."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncGenerator
from functools import wraps
from pathlib import Path

from pylabrobot.__version__ import __version__
from pylabrobot.revvity.celigo.grpc.model import (
  MAX_FRAME_BYTES,
  MAX_MESSAGE_BYTES,
  MAX_PIXELS,
  SimulationSpec,
)
from pylabrobot.revvity.celigo.grpc.simulator import Simulator
from pylabrobot.revvity.celigo.grpc.storage import DEFAULT_QUOTA
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.rpc import device, images, jobs
from pylabrobot.rpc.device import DeviceServer
from pylabrobot.rpc.errors import StateError
from pylabrobot.rpc.security import Certificates
from pylabrobot.rpc.transport import API_VERSION, copy_message, require_device, rpc_errors
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import images_pb2_grpc as image_rpc
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb
from pylabrobot.rpc.v1 import jobs_pb2_grpc as job_rpc


def public_job(status: stored_pb.JobStatus) -> job_pb.JobStatus:
  """Project durable state onto the common job contract, omitting image retention."""
  result = job_pb.JobStatus.FromString(status.SerializeToString())
  result.DiscardUnknownFields()
  return result


def public_frame(frame: stored_pb.ImageFrame) -> image_pb.ImageFrame:
  """Wrap complete pixels and explicitly typed Celigo metadata in a shared envelope."""
  result = image_pb.ImageFrame(
    job=frame.job,
    frame_id=frame.frame_id,
    sequence=frame.sequence,
    representation=frame.representation,
    captured_at=frame.captured_at,
    width=frame.width,
    height=frame.height,
    pixel_format=frame.pixel_format,
    encoding=frame.encoding,
    image=frame.image,
    sha256=frame.sha256,
  )
  result.metadata.Pack(frame.metadata)
  return result


def unary(method):
  """Translate errors from typed Celigo operation methods into shared RPC status codes."""

  @wraps(method)
  async def call(self, request, context):
    """Apply the same error mapping as the shared service adapters."""
    async with rpc_errors(context):
      return await method(self, request, context)

  return call


class ImagingAdapter:
  """Present Celigo state through shared interfaces without moving its executor/storage."""

  def __init__(self, core: Simulator) -> None:
    """Bind an initialized service core, which may still have uninitialized hardware."""
    self.core = core

  @property
  def device_id(self) -> str:
    """Return the stable device identifier owned by the core."""
    return self.core.device_id

  def get_info(self) -> device_pb.DeviceInfo:
    """Advertise actual registered services independently of image-specific limits."""
    operations = (
      "celigo.v1.SimulationService"
      if self.core.device_type == "CeligoSimulator"
      else "celigo.v1.CeligoService"
    )
    return device_pb.DeviceInfo(
      device_id=self.device_id,
      display_name=self.core.display_name,
      device_type=self.core.device_type,
      api_version=API_VERSION,
      configuration_id=self.core.configuration_id,
      supported_operations=self.core.supported_operations,
      services=[device.SERVICE_NAME, jobs.SERVICE_NAME, images.SERVICE_NAME, operations],
      manufacturer="Revvity",
      model="Celigo",
      implementation_version=__version__,
      instance_id=self.core.status.instance_id,
    )

  def get_status(self) -> device_pb.DeviceStatus:
    """Read the cached operational snapshot without polling or moving hardware."""
    return copy_message(self.core.status)

  async def watch_status(self) -> AsyncGenerator[device_pb.DeviceStatus, None]:
    """Publish current operational state followed by bounded updates."""
    with self.core.updates.subscribe() as queue:
      while True:
        yield await queue.get()

  async def get_job(self, job_id: str) -> job_pb.JobStatus:
    """Read a job through the image-independent public status type."""
    return public_job(self.core.jobs[job_id].status)

  async def find_job(self, request_id: str) -> job_pb.JobRef:
    """Recover an existing request without creating or starting work."""
    return copy_message(self.core.requests[request_id].ref)

  async def start_job(self, job_id: str) -> job_pb.JobStatus:
    """Let durable acceptance and executor bookkeeping finish if the RPC disconnects."""
    return public_job(await self.core.command(self.core.jobs[job_id].start()))

  async def cancel_job(self, job_id: str) -> job_pb.JobStatus:
    """Persist stopping intent while the executor retains hardware ownership."""
    return public_job(await self.core.command(self.core.jobs[job_id].cancel()))

  async def watch_job(self, job_id: str) -> AsyncGenerator[job_pb.JobStatus, None]:
    """Project bounded stored snapshots into the common job contract."""
    with self.core.jobs[job_id].updates.subscribe() as queue:
      while True:
        status = await queue.get()
        yield public_job(status)
        if status.state == job_pb.JOB_STATE_FINISHED:
          return

  def image_capabilities(self) -> image_pb.ImageCapabilities:
    """Keep pixel limits and retention capacity in the optional image capability."""
    return image_pb.ImageCapabilities(
      maximum_frame_bytes=MAX_FRAME_BYTES,
      maximum_pixels=MAX_PIXELS,
      retention_bytes=self.core.store.quota,
      pixel_formats=["Mono8", "Mono16"],
      encodings=["PNG"],
    )

  async def image_retention(self, job_id: str) -> image_pb.ImageRetention:
    """Read the durable image-release flag without adding it to common job status."""
    status = self.core.jobs[job_id].status
    return image_pb.ImageRetention(job=status.job, images_released=status.images_released)

  async def release_images(self, job_id: str) -> image_pb.ImageRetention:
    """Commit explicit image release independently of the initiating RPC lifetime."""
    status = await self.core.command(self.core.jobs[job_id].release_images())
    return image_pb.ImageRetention(job=status.job, images_released=status.images_released)

  async def list_frames(self, job_id: str, after_sequence: int, limit: int) -> image_pb.FramePage:
    """Read one metadata page from the existing durable store."""
    return await self.core.jobs[job_id].list_frames(after_sequence, limit)

  async def get_frame(self, job_id: str, frame_id: str) -> image_pb.ImageFrame:
    """Return a shared image envelope after validating the retained pixels."""
    return public_frame(await self.core.jobs[job_id].get_frame(frame_id))

  async def watch_preview(self) -> AsyncGenerator[image_pb.ImageFrame, None]:
    """Read complete retained previews; subscribing never starts acquisition."""
    with self.core.previews.subscribe() as queue:
      while True:
        ref = await queue.get()
        try:
          yield await self.get_frame(ref.job.job_id, ref.frame_id)
        except StateError:
          continue  # Explicit image release can race with a queued preview reference.


class SimulationOperations(rpc.SimulationServiceServicer):
  """Create typed synthetic acquisition requests for the Celigo simulator."""

  def __init__(self, core: Simulator) -> None:
    """Bind the simulator's operation-specific creation API."""
    self.core = core

  async def create(self, request: pb.CreateRequest, preview: bool) -> job_pb.JobRef:
    """Validate and durably create an inert synthetic request."""
    require_device(request.device_id, self.core.device_id)
    if not request.HasField("specification"):
      raise ValueError("specification is required")
    spec = SimulationSpec.from_proto(request.specification)
    job = await self.core.command(
      self.core.create(request.request_id, spec, preview, request.configuration_id)
    )
    return copy_message(job.ref)

  @unary
  async def CreateInitialization(self, request, context):
    """Create an inert synthetic initialization job."""
    require_device(request.device_id, self.core.device_id)
    job = await self.core.command(
      self.core.create(request.request_id, SimulationSpec(), initialize=True)
    )
    return copy_message(job.ref)

  @unary
  async def CreateAcquisition(self, request, context):
    """Create a synthetic multi-frame request without starting it."""
    return await self.create(request, False)

  @unary
  async def CreatePreview(self, request, context):
    """Create a synthetic one-frame preview request without starting it."""
    return await self.create(request, True)


class SimulatorServer(DeviceServer):
  """Compose shared transport capabilities around the Celigo acquisition engine."""

  def __init__(
    self,
    certs: Certificates,
    *,
    port=0,
    device_id="celigo-sim-01",
    data_dir: Path | None = None,
    storage_quota=DEFAULT_QUOTA,
    max_jobs=1024,
    configuration_id="simulation-v1",
    core: Simulator | None = None,
  ):
    """Build the imaging service; startup does not open physical hardware."""
    self.temporary = (
      tempfile.TemporaryDirectory(prefix="celigo-store-")
      if data_dir is None and core is None
      else None
    )
    directory = Path(self.temporary.name) if self.temporary else data_dir
    if core is None:
      assert directory is not None
      core = Simulator(directory, device_id, storage_quota, max_jobs, configuration_id)
    self.simulator = core
    self.adapter = ImagingAdapter(core)
    super().__init__(self.adapter, certs, port=port, maximum_message_bytes=MAX_MESSAGE_BYTES)
    job_rpc.add_JobServiceServicer_to_server(jobs.JobService(self.adapter), self.server)
    image_rpc.add_ImageServiceServicer_to_server(images.ImageService(self.adapter), self.server)
    if core.device_type == "CeligoSimulator":
      rpc.add_SimulationServiceServicer_to_server(SimulationOperations(core), self.server)

  async def start(self) -> None:
    """Recover durable records before accepting requests; never replay interrupted work."""
    try:
      await self.simulator.open()
      await super().start()
    except BaseException:
      await self.stop()
      raise

  async def stop(self) -> None:
    """Stop accepting commands, finish in-flight work, and close the owned store."""
    self.simulator.closing = True
    await super().stop()
    await self.simulator.stop()
    if self.temporary:
      self.temporary.cleanup()

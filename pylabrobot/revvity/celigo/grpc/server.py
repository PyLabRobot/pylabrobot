"""gRPC mapping for the simulator; no hardware imports or startup motion."""

from __future__ import annotations

import logging
import tempfile
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path

import grpc

from pylabrobot.revvity.celigo.grpc.errors import (
  BusyError,
  CapacityError,
  DataLossError,
  StateError,
  StorageError,
)
from pylabrobot.revvity.celigo.grpc.model import (
  CHANNEL_OPTIONS,
  MAX_FRAME_BYTES,
  MAX_PIXELS,
  SimulationSpec,
  copy_message,
)
from pylabrobot.revvity.celigo.grpc.security import Certificates
from pylabrobot.revvity.celigo.grpc.simulator import Simulator
from pylabrobot.revvity.celigo.grpc.storage import DEFAULT_QUOTA
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc

logger = logging.getLogger(__name__)


@asynccontextmanager
async def errors(context):
  try:
    yield
  except (ValueError, TypeError) as exc:
    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
  except KeyError:
    await context.abort(grpc.StatusCode.NOT_FOUND, "device, job, request, or frame not found")
  except (BusyError, CapacityError) as exc:
    await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc))
  except StateError as exc:
    await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
  except DataLossError as exc:
    await context.abort(grpc.StatusCode.DATA_LOSS, str(exc))
  except StorageError as exc:
    await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
  except Exception:
    logger.exception("Simulator RPC failed")
    await context.abort(grpc.StatusCode.INTERNAL, "internal simulator error")


def unary(method):
  @wraps(method)
  async def call(self, request, context):
    async with errors(context):
      return await method(self, request, context)

  return call


class Service(
  rpc.DeviceServiceServicer,
  rpc.SimulationServiceServicer,
  rpc.JobServiceServicer,
  rpc.ImageServiceServicer,
):
  def __init__(self, simulator: Simulator):
    self.simulator = simulator

  def device(self, device_id: str) -> Simulator:
    if device_id != self.simulator.device_id:
      raise KeyError(device_id)
    return self.simulator

  def job(self, ref: pb.JobRef):
    return self.device(ref.device_id).jobs[ref.job_id]

  @unary
  async def GetDevice(self, request, context):
    sim = self.device(request.device_id)
    return pb.DeviceInfo(
      device_id=sim.device_id,
      display_name=sim.display_name,
      device_type=sim.device_type,
      api_version="celigo.v1",
      maximum_frame_bytes=MAX_FRAME_BYTES,
      maximum_pixels=MAX_PIXELS,
      retention_bytes=sim.store.quota,
      configuration_id=sim.configuration_id,
      supported_operations=sim.supported_operations,
    )

  @unary
  async def GetStatus(self, request, context):
    return copy_message(self.device(request.device_id).status)

  async def WatchStatus(self, request, context):
    async with errors(context):
      with self.device(request.device_id).updates.subscribe() as queue:
        while True:
          yield await queue.get()

  async def create(self, request: pb.CreateRequest, preview: bool) -> pb.JobRef:
    sim = self.device(request.device_id)
    if not request.HasField("specification"):
      raise ValueError("specification is required")
    spec = SimulationSpec.from_proto(request.specification)
    job = await sim.command(sim.create(request.request_id, spec, preview, request.configuration_id))
    return copy_message(job.ref)

  @unary
  async def CreateInitialization(self, request, context):
    sim = self.device(request.device_id)
    job = await sim.command(sim.create(request.request_id, SimulationSpec(), initialize=True))
    return copy_message(job.ref)

  @unary
  async def CreateAcquisition(self, request, context):
    return await self.create(request, False)

  @unary
  async def CreatePreview(self, request, context):
    return await self.create(request, True)

  @unary
  async def GetJob(self, request, context):
    return copy_message(self.job(request).status)

  @unary
  async def GetJobByRequestId(self, request, context):
    return copy_message(self.device(request.device_id).requests[request.request_id].ref)

  @unary
  async def StartJob(self, request, context):
    return await self.simulator.command(self.job(request).start())

  @unary
  async def CancelJob(self, request, context):
    return await self.simulator.command(self.job(request).cancel())

  @unary
  async def ReleaseJobImages(self, request, context):
    return await self.simulator.command(self.job(request).release_images())

  async def WatchJob(self, request, context):
    async with errors(context):
      with self.job(request).updates.subscribe() as queue:
        while True:
          snapshot = await queue.get()
          yield snapshot
          if snapshot.state == pb.JOB_STATE_FINISHED:
            return

  @unary
  async def ListFrames(self, request, context):
    return await self.job(request.job).list_frames(request.after_sequence, request.limit)

  @unary
  async def GetFrame(self, request, context):
    return await self.job(request.job).get_frame(request.frame_id)

  async def WatchPreview(self, request, context):
    async with errors(context):
      with self.device(request.device_id).previews.subscribe() as queue:
        while True:
          ref = await queue.get()
          try:
            yield await self.job(ref.job).get_frame(ref.frame_id)
          except StateError:
            # A terminal job may have released images while this update was queued.
            continue


class SimulatorServer:
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
    self.server = grpc.aio.server(options=CHANNEL_OPTIONS, maximum_concurrent_rpcs=32)
    service = Service(self.simulator)
    rpc.add_DeviceServiceServicer_to_server(service, self.server)
    if core.device_type == "CeligoSimulator":
      rpc.add_SimulationServiceServicer_to_server(service, self.server)
    rpc.add_JobServiceServicer_to_server(service, self.server)
    rpc.add_ImageServiceServicer_to_server(service, self.server)
    # Prototype intentionally exposes no configurable non-loopback bind address.
    self.port = self.server.add_secure_port(f"127.0.0.1:{port}", certs.server_credentials())
    if not self.port:
      raise RuntimeError("unable to bind simulator endpoint")
    self.endpoint = f"127.0.0.1:{self.port}"

  async def start(self) -> None:
    try:
      await self.simulator.open()
      await self.server.start()
    except BaseException:
      await self.stop()
      raise

  async def stop(self) -> None:
    self.simulator.closing = True
    await self.server.stop(0)
    await self.simulator.stop()
    if self.temporary:
      self.temporary.cleanup()

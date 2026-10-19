"""Optional job lifecycle capability; execution and persistence belong to the device."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Protocol

import grpc

from pylabrobot.rpc.transport import Updates, copy_message, require_device, rpc_errors
from pylabrobot.rpc.v1 import jobs_pb2 as pb
from pylabrobot.rpc.v1 import jobs_pb2_grpc as rpc

SERVICE_NAME = "pylabrobot.rpc.v1.JobService"


class JobBackend(Protocol):
  """Device-owned job operations; accepted execution must outlive the invoking RPC."""

  @property
  def device_id(self) -> str:
    """Return the stable device that owns every exposed job."""
    ...

  async def get_job(self, job_id: str) -> pb.JobStatus:
    """Read current state for an existing job."""
    ...

  async def find_job(self, request_id: str) -> pb.JobRef:
    """Resolve durable request identity without creating or restarting work."""
    ...

  async def start_job(self, job_id: str) -> pb.JobStatus:
    """Accept an explicit start with device-specific ownership and durability rules."""
    ...

  async def cancel_job(self, job_id: str) -> pb.JobStatus:
    """Request cooperative cancellation without releasing hardware ownership early."""
    ...

  def watch_job(self, job_id: str) -> AsyncGenerator[pb.JobStatus, None]:
    """Yield current state followed by updates, ending after durable or failed completion."""
    ...


class JobService(rpc.JobServiceServicer):
  """Map the shared job protocol onto a device-specific executor."""

  def __init__(self, backend: JobBackend) -> None:
    """Bind the executor without allocating an image store or creating any work."""
    self.backend = backend

  async def GetJob(self, request: pb.JobRef, context: grpc.aio.ServicerContext) -> pb.JobStatus:
    """Read a job belonging to this device."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return await self.backend.get_job(request.job_id)

  async def GetJobByRequestId(
    self, request: pb.RequestLookup, context: grpc.aio.ServicerContext
  ) -> pb.JobRef:
    """Recover a previously created request without executing it."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return await self.backend.find_job(request.request_id)

  async def StartJob(self, request: pb.JobRef, context: grpc.aio.ServicerContext) -> pb.JobStatus:
    """Delegate start acceptance; the device owns any accepted execution task."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return await self.backend.start_job(request.job_id)

  async def CancelJob(self, request: pb.JobRef, context: grpc.aio.ServicerContext) -> pb.JobStatus:
    """Delegate cooperative stopping without interpreting device-specific progress."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return await self.backend.cancel_job(request.job_id)

  async def WatchJob(
    self, request: pb.JobRef, context: grpc.aio.ServicerContext
  ) -> AsyncIterator[pb.JobStatus]:
    """Forward snapshots; cancelling this stream does not cancel the remote job."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      async with aclosing(self.backend.watch_job(request.job_id)) as updates:
        async for status in updates:
          yield copy_message(status)
          if status.state == pb.JOB_STATE_FINISHED:
            return


class Job:
  """Client handle for one single-use operation, without image-specific methods."""

  def __init__(self, stub: rpc.JobServiceAsyncStub, ref: pb.JobRef, timeout: float = 20) -> None:
    """Bind a remote reference; no RPC or execution occurs during construction."""
    self.stub = stub
    self.identifier = ref.job_id
    self.device_id = ref.device_id
    self.timeout = timeout

  @property
  def ref(self) -> pb.JobRef:
    """Return a separate device-scoped reference."""
    return pb.JobRef(device_id=self.device_id, job_id=self.identifier)

  async def start(self) -> pb.JobStatus:
    """Explicitly start this job; reconcile a lost response by reading its state."""
    return await self.stub.StartJob(self.ref, timeout=self.timeout)

  async def cancel(self) -> pb.JobStatus:
    """Request stopping; a returned Running status means cleanup is still pending."""
    return await self.stub.CancelJob(self.ref, timeout=self.timeout)

  async def status(self) -> pb.JobStatus:
    """Read state, outcome, progress, and durability without changing execution."""
    return await self.stub.GetJob(self.ref, timeout=self.timeout)

  @asynccontextmanager
  async def updates(self) -> AsyncIterator[Updates[pb.JobStatus]]:
    """Watch coalesced snapshots until completion or client context exit."""
    call = self.stub.WatchJob(self.ref)
    try:
      yield Updates[pb.JobStatus](call)
    finally:
      call.cancel()

  async def wait(self, timeout: float = 30) -> pb.JobStatus:
    """Wait for Finished; a local timeout leaves remote execution running."""

    async def finished() -> pb.JobStatus:
      """Consume snapshots until a terminal one arrives."""
      async with self.updates() as updates:
        while True:
          snapshot = await updates.get()
          if snapshot.state == pb.JOB_STATE_FINISHED:
            return snapshot

    return await asyncio.wait_for(finished(), timeout)


class JobClient:
  """Compose job support onto a connected device that advertises JobService."""

  def __init__(self, channel: grpc.aio.Channel, device_id: str, timeout: float = 20) -> None:
    """Use an application-owned channel without opening another hardware session."""
    self.stub = rpc.JobServiceStub(channel)
    self.device_id = device_id
    self.timeout = timeout

  def bind(self, ref: pb.JobRef) -> Job:
    """Bind a typed operation's returned reference to this device client."""
    require_device(ref.device_id, self.device_id)
    return Job(self.stub, ref, self.timeout)

  async def recover(self, request_id: str) -> Job:
    """Find the same job after reconnecting, without resubmitting its operation."""
    ref = await self.stub.GetJobByRequestId(
      pb.RequestLookup(device_id=self.device_id, request_id=request_id), timeout=self.timeout
    )
    return self.bind(ref)

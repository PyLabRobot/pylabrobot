"""Independent device types and jobs using only the shared RPC package."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from uuid import uuid4

import grpc
import pytest
import pytest_asyncio

from pylabrobot.rpc.device import SERVICE_NAME as DEVICE_SERVICE
from pylabrobot.rpc.device import DeviceClient, DeviceServer
from pylabrobot.rpc.errors import CapacityError, StateError
from pylabrobot.rpc.images import ImageClient
from pylabrobot.rpc.jobs import SERVICE_NAME as JOB_SERVICE
from pylabrobot.rpc.jobs import JobClient, JobService
from pylabrobot.rpc.transport import API_VERSION, Latest, copy_message
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb
from pylabrobot.rpc.v1 import jobs_pb2_grpc as job_rpc


class StatusDevice:
  """An instrument exposing identity/status without jobs, images, or persistence."""

  services = (DEVICE_SERVICE,)

  def __init__(self, device_id: str, device_type: str) -> None:
    """Create one independently revisioned operational snapshot."""
    self.device_id = device_id
    self.device_type = device_type
    self.snapshot = device_pb.DeviceStatus(
      device_id=device_id,
      state=device_pb.OPERATIONAL_STATE_READY,
      instance_id=uuid4().hex,
      revision=1,
      observation_valid=True,
    )
    self.snapshot.observed_at.GetCurrentTime()
    self.updates = Latest(self.snapshot)
    self.watch_closed = asyncio.Event()

  def get_info(self) -> device_pb.DeviceInfo:
    """Advertise only the services this test instrument actually exposes."""
    return device_pb.DeviceInfo(
      device_id=self.device_id,
      display_name=self.device_type,
      device_type=self.device_type,
      api_version=API_VERSION,
      services=self.services,
      instance_id=self.snapshot.instance_id,
    )

  def get_status(self) -> device_pb.DeviceStatus:
    """Return a separate snapshot without touching hardware."""
    return copy_message(self.snapshot)

  async def watch_status(self) -> AsyncGenerator[device_pb.DeviceStatus, None]:
    """Track subscription cleanup when the client closes its watch."""
    try:
      with self.updates.subscribe() as queue:
        while True:
          yield await queue.get()
    finally:
      self.watch_closed.set()


class PumpDevice(StatusDevice):
  """A non-imaging operation provider; its in-memory state is explicitly non-durable."""

  services = (DEVICE_SERVICE, JOB_SERVICE)

  def __init__(self, device_id: str = "pump-01") -> None:
    """Prepare one inert operation through the fake device's local creation API."""
    super().__init__(device_id, "PumpSimulator")
    self.operation = job_pb.JobStatus(
      job=job_pb.JobRef(device_id=device_id, job_id="prime-01"),
      request_id="prime-request",
      kind="Prime",
      state=job_pb.JOB_STATE_READY,
      outcome=job_pb.OUTCOME_PENDING,
      total=3,
      progress_unit="Dispenses",
      phase="Ready",
      revision=1,
    )
    self.operation.created_at.GetCurrentTime()
    self.operation.updated_at.CopyFrom(self.operation.created_at)
    self.job_updates = Latest(self.operation)
    self.finished = asyncio.Event()
    self.task: asyncio.Task[None] | None = None
    self.executions = 0

  def validate_job(self, job_id: str) -> None:
    """Resolve the fixture's single existing request without creating work."""
    if job_id != self.operation.job.job_id:
      raise KeyError(job_id)

  def publish(self) -> None:
    """Publish operation and device state with independent revisions and observation times."""
    self.operation.revision += 1
    self.operation.updated_at.GetCurrentTime()
    self.job_updates.publish(self.operation)
    running = self.operation.state == job_pb.JOB_STATE_RUNNING
    self.snapshot.active_job_id = self.operation.job.job_id if running else ""
    self.snapshot.state = (
      device_pb.OPERATIONAL_STATE_BUSY if running else device_pb.OPERATIONAL_STATE_READY
    )
    self.snapshot.revision += 1
    self.snapshot.observed_at.GetCurrentTime()
    self.updates.publish(self.snapshot)

  async def execute(self) -> None:
    """Wait for simulated device completion independently of any client connection."""
    await self.finished.wait()
    self.operation.state = job_pb.JOB_STATE_FINISHED
    self.operation.outcome = (
      job_pb.OUTCOME_CANCELLED
      if self.operation.cancellation_requested
      else job_pb.OUTCOME_SUCCEEDED
    )
    self.operation.phase = "Finished"
    self.operation.completed = 3 if self.operation.outcome == job_pb.OUTCOME_SUCCEEDED else 0
    self.publish()

  async def get_job(self, job_id: str) -> job_pb.JobStatus:
    """Read non-imaging progress without inventing persistence guarantees."""
    self.validate_job(job_id)
    return copy_message(self.operation)

  async def find_job(self, request_id: str) -> job_pb.JobRef:
    """Resolve the same request while this test provider remains running."""
    if request_id != self.operation.request_id:
      raise KeyError(request_id)
    return copy_message(self.operation.job)

  async def start_job(self, job_id: str) -> job_pb.JobStatus:
    """Reserve the fake device and detach execution from the RPC coroutine."""
    self.validate_job(job_id)
    if self.operation.state == job_pb.JOB_STATE_FINISHED:
      raise StateError("finished jobs cannot restart")
    if self.task is None:
      self.executions += 1
      self.operation.state = job_pb.JOB_STATE_RUNNING
      self.operation.phase = "Priming"
      self.publish()
      self.task = asyncio.create_task(self.execute())
    return copy_message(self.operation)

  async def cancel_job(self, job_id: str) -> job_pb.JobStatus:
    """Keep Running/Stopping until the simulated in-flight operation finishes."""
    self.validate_job(job_id)
    if self.operation.state != job_pb.JOB_STATE_FINISHED:
      self.operation.cancellation_requested = True
      self.operation.phase = "Stopping"
      if self.operation.state == job_pb.JOB_STATE_READY:
        self.operation.state = job_pb.JOB_STATE_FINISHED
        self.operation.outcome = job_pb.OUTCOME_CANCELLED
        self.operation.phase = "Finished"
      self.publish()
    return copy_message(self.operation)

  async def watch_job(self, job_id: str) -> AsyncGenerator[job_pb.JobStatus, None]:
    """Expose current state and bounded updates through the shared job service."""
    self.validate_job(job_id)
    with self.job_updates.subscribe() as queue:
      while True:
        status = await queue.get()
        yield status
        if status.state == job_pb.JOB_STATE_FINISHED:
          return

  async def close(self) -> None:
    """Finish fixture-owned execution before shutting down its event loop."""
    self.finished.set()
    if self.task is not None:
      await self.task


@pytest_asyncio.fixture
async def pump():
  """Run a non-imaging server without registering ImageService."""
  backend = PumpDevice()
  server = DeviceServer(backend)
  job_rpc.add_JobServiceServicer_to_server(JobService(backend), server.server)
  await server.start()
  try:
    yield backend, server
  finally:
    await backend.close()
    await server.stop()


@pytest.mark.asyncio
async def test_status_only_device_has_no_optional_services() -> None:
  """An incubator can appear in a dashboard without a job engine or image service."""
  backend = StatusDevice("incubator-01", "IncubatorSimulator")
  server = DeviceServer(backend)
  await server.start()
  try:
    async with DeviceClient(server.endpoint, backend.device_id) as client:
      info = await client.device_info()
      status = await client.status()
      assert list(info.services) == [DEVICE_SERVICE]
      assert status.instance_id == info.instance_id and status.observation_valid
      assert client.channel is not None
      with pytest.raises(grpc.aio.AioRpcError) as unavailable:
        await JobClient(client.channel, backend.device_id).recover("missing")
      assert unavailable.value.code() == grpc.StatusCode.UNIMPLEMENTED
      with pytest.raises(grpc.aio.AioRpcError) as unavailable:
        await ImageClient(client.channel, backend.device_id).capabilities()
      assert unavailable.value.code() == grpc.StatusCode.UNIMPLEMENTED
  finally:
    await server.stop()


@pytest.mark.asyncio
async def test_independent_device_types_survive_one_disconnect(pump) -> None:
  """Failure of one network endpoint must not affect another device connection."""
  backend, server = pump
  second = StatusDevice("incubator-01", "IncubatorSimulator")
  other = DeviceServer(second)
  await other.start()
  try:
    async with (
      DeviceClient(server.endpoint, backend.device_id) as a,
      DeviceClient(other.endpoint, second.device_id, timeout=0.2) as b,
    ):
      assert (await a.device_info()).device_type != (await b.device_info()).device_type
      await other.stop()
      with pytest.raises(grpc.aio.AioRpcError):
        await b.status()
      assert (await a.status()).state == device_pb.OPERATIONAL_STATE_READY
  finally:
    await other.stop()


@pytest.mark.asyncio
async def test_non_imaging_job_survives_disconnect_and_recovery(pump) -> None:
  """The common job API needs neither Celigo semantics nor image retention."""
  backend, server = pump
  async with DeviceClient(server.endpoint, backend.device_id) as client:
    assert client.channel is not None
    jobs = JobClient(client.channel, backend.device_id)
    job = await jobs.recover("prime-request")
    await job.start()
    await job.start()
    assert (await client.status()).active_job_id == job.identifier
  backend.finished.set()
  await backend.task
  async with DeviceClient(server.endpoint, backend.device_id) as client:
    assert client.channel is not None
    job = await JobClient(client.channel, backend.device_id).recover("prime-request")
    status = await job.wait()
    assert status.outcome == job_pb.OUTCOME_SUCCEEDED
    assert status.completed == 3 and status.progress_unit == "Dispenses"
    assert not status.state_durable and backend.executions == 1
    with pytest.raises(grpc.aio.AioRpcError) as finished:
      await job.start()
    assert finished.value.code() == grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.asyncio
async def test_job_cancellation_waits_for_device_cleanup(pump) -> None:
  """Cancellation semantics remain owned by the device-specific executor."""
  backend, server = pump
  async with DeviceClient(server.endpoint, backend.device_id) as client:
    assert client.channel is not None
    job = await JobClient(client.channel, backend.device_id).recover("prime-request")
    await job.start()
    stopping = await job.cancel()
    assert stopping.state == job_pb.JOB_STATE_RUNNING and stopping.phase == "Stopping"
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_BUSY
    backend.finished.set()
    assert (await job.wait()).outcome == job_pb.OUTCOME_CANCELLED
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_READY


@pytest.mark.asyncio
async def test_foreign_job_rejected_before_execution(pump) -> None:
  """Device scoping prevents a valid job ID from being routed to the wrong instrument."""
  backend, server = pump
  async with DeviceClient(server.endpoint, backend.device_id) as client:
    assert client.channel is not None
    stub = job_rpc.JobServiceStub(client.channel)
    with pytest.raises(grpc.aio.AioRpcError) as wrong:
      await stub.StartJob(job_pb.JobRef(device_id="other-device", job_id="prime-01"))
    assert wrong.value.code() == grpc.StatusCode.NOT_FOUND
    assert backend.executions == 0


@pytest.mark.asyncio
async def test_watch_closure_releases_subscription(pump) -> None:
  """Closing a dashboard watch must release its server-side subscription slot."""
  backend, server = pump
  async with DeviceClient(server.endpoint, backend.device_id) as client:
    async with client.status_updates() as updates:
      assert (await updates.get()).state == device_pb.OPERATIONAL_STATE_READY
      assert len(backend.updates.queues) == 1
    await asyncio.wait_for(backend.watch_closed.wait(), 3)
    assert not backend.updates.queues


@pytest.mark.asyncio
async def test_latest_snapshots_are_bounded_and_independent() -> None:
  """Slow or mutating subscribers cannot affect another subscriber's snapshot."""
  latest = Latest(device_pb.DeviceStatus(revision=1), limit=2)
  with latest.subscribe() as a, latest.subscribe() as b:
    (await a.get()).fault_summary = "changed by subscriber"
    assert (await b.get()).fault_summary == ""
    latest.publish(device_pb.DeviceStatus(revision=2))
    latest.publish(device_pb.DeviceStatus(revision=3))
    assert a.qsize() == b.qsize() == 1
    assert (await a.get()).revision == (await b.get()).revision == 3
    with pytest.raises(CapacityError), latest.subscribe():
      pass
  assert not latest.queues

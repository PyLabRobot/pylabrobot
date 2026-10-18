"""Persistent, hardware-free execution independent of RPC and process lifetimes."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from pylabrobot.revvity.celigo.grpc.model import (
  SimulationSpec,
  Specification,
  make_frame,
)
from pylabrobot.revvity.celigo.grpc.storage import DEFAULT_QUOTA, Store, advance
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.rpc.errors import BusyError, CapacityError, StateError, StorageError
from pylabrobot.rpc.transport import Latest, copy_message
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def owned_operation(operation: Coroutine[object, object, T]) -> T:
  """Keep ownership until an in-flight operation finishes, even on executor cancellation."""
  task = asyncio.create_task(operation)
  try:
    return await asyncio.shield(task)
  except asyncio.CancelledError:
    try:
      await task
    except Exception:
      logger.exception("In-flight operation failed while executor was cancelling")
    raise


class SimulatedJob:
  def __init__(self, service: Simulator, spec: Specification, status: stored_pb.JobStatus):
    self.service = service
    self.spec = spec
    self.status = status
    self.ref = copy_message(status.job)
    self.preview = status.kind == "Preview"
    self.stop_requested = asyncio.Event()
    if status.cancellation_requested:
      self.stop_requested.set()
    self.task: asyncio.Task[None] | None = None
    self.updates: Latest[stored_pb.JobStatus] = Latest(copy_message(status))

  def publish(self, status: stored_pb.JobStatus) -> None:
    self.status = status
    self.updates.publish(status)

  async def start(self) -> stored_pb.JobStatus:
    async with self.service.lock:
      self.service.ensure_available()
      if self.status.state == job_pb.JOB_STATE_RUNNING:
        return copy_message(self.status)
      if self.status.state != job_pb.JOB_STATE_READY or self.stop_requested.is_set():
        raise StateError("finished or cancelling jobs cannot start")
      if self.service.active_job is not None:
        raise BusyError("another job owns the instrument")
      if self.status.configuration_id != self.service.configuration_id:
        raise StateError("job configuration no longer matches the service")
      if self.status.kind != "Initialize" and not self.service.initialized:
        raise StateError("explicit initialization is required before acquisition")
      self.service.validate_start(self)
      status = advance(
        self.status,
        state=job_pb.JOB_STATE_RUNNING,
        phase="Initializing" if self.status.kind == "Initialize" else "Capturing",
      )
      await self.service.io(self.service.store.save, status, "start")
      self.publish(status)
      self.service.active_job = self
      self.service.publish_device()
      self.task = asyncio.create_task(self.run(), name=f"celigo-grpc-{self.ref.job_id}")
      return copy_message(self.status)

  async def cancel(self) -> stored_pb.JobStatus:
    # Signal execution immediately even if a disk commit currently holds the lock.
    if self.status.state != job_pb.JOB_STATE_FINISHED:
      self.stop_requested.set()
    async with self.service.lock:
      if self.status.state != job_pb.JOB_STATE_FINISHED and not self.status.cancellation_requested:
        status = advance(self.status, cancellation_requested=True, phase="Stopping")
        if status.state == job_pb.JOB_STATE_READY:
          status.state, status.outcome, status.phase = (
            job_pb.JOB_STATE_FINISHED,
            job_pb.OUTCOME_CANCELLED,
            "Finished",
          )
        await self.service.io(self.service.store.save, status, "cancel")
        self.publish(status)
      return copy_message(self.status)

  async def list_frames(self, after_sequence: int, limit: int) -> image_pb.FramePage:
    return await self.service.io(
      self.service.store.list_frames, self.ref.job_id, after_sequence, limit
    )

  async def get_frame(self, frame_id: str) -> stored_pb.ImageFrame:
    async with self.service.read_slots:
      return await self.service.io(self.service.store.get_frame, self.ref.job_id, frame_id)

  async def release_images(self) -> stored_pb.JobStatus:
    async with self.service.lock:
      if self.status.state != job_pb.JOB_STATE_FINISHED or not self.status.state_durable:
        raise StateError("only durably finished jobs can release images")
      if not self.status.images_released:
        status = advance(self.status, images_released=True)
        await self.service.io(self.service.store.release_images, status)
        self.publish(status)
        self.service.previews.value = await self.service.io(self.service.store.latest_preview)
      return copy_message(self.status)

  async def run(self) -> None:
    error = ""
    interrupted = False
    try:
      if self.status.kind == "Initialize":
        if not self.stop_requested.is_set():
          await owned_operation(self.service.initialize())
      else:
        for sequence in range(1, self.spec.frame_count + 1):
          if self.stop_requested.is_set():
            break
          if self.spec.interval_ms:
            try:
              await asyncio.wait_for(self.stop_requested.wait(), self.spec.interval_ms / 1000)
              break
            except asyncio.TimeoutError:
              pass
          frame = await owned_operation(self.service.capture(self, sequence))
          if frame is None:
            break
          async with self.service.lock:
            status = advance(self.status, completed=sequence)
            await self.service.io(self.service.store.commit_frame, frame, status)
            self.publish(status)
            if self.preview:
              self.service.previews.publish(
                image_pb.GetFrameRequest(job=self.ref, frame_id=frame.frame_id)
              )
          del frame  # Retention lives in SQLite; memory does not accumulate with scan length.
    except asyncio.CancelledError:
      interrupted = True
      error = "Execution task was interrupted; in-flight operation and cleanup were awaited."
    except Exception as exc:
      logger.exception("Acquisition %s failed", self.ref.job_id)
      error = f"{type(exc).__name__}: {exc}"
    finally:
      try:
        await owned_operation(self.service.finish_operation(self, bool(error)))
      except (Exception, asyncio.CancelledError) as exc:
        logger.exception("Operation cleanup failed for %s", self.ref.job_id)
        error = f"{error}; cleanup failed: {type(exc).__name__}: {exc}".lstrip("; ")
      async with self.service.lock:
        outcome = (
          job_pb.OUTCOME_INTERRUPTED
          if interrupted
          else job_pb.OUTCOME_FAILED
          if error
          else (
            job_pb.OUTCOME_CANCELLED if self.stop_requested.is_set() else job_pb.OUTCOME_SUCCEEDED
          )
        )
        status = advance(
          self.status,
          state=job_pb.JOB_STATE_FINISHED,
          phase="Finished",
          outcome=outcome,
          cancellation_requested=self.stop_requested.is_set(),
          error=error,
        )
        if self.status.kind == "Initialize" and outcome == job_pb.OUTCOME_SUCCEEDED:
          status.completed = 1
        try:
          await self.service.io(self.service.store.save, status, "finish")
        except StorageError as exc:
          # The disk may still contain Running. Recovery will mark it Interrupted.
          status.state_durable = False
          status.outcome = job_pb.OUTCOME_FAILED
          status.error = f"Completion could not be persisted: {exc}"
        self.publish(status)
        if self.status.kind == "Initialize":
          self.service.initialized = status.outcome == job_pb.OUTCOME_SUCCEEDED
        self.service.active_job = None
        self.service.publish_device()


class Simulator:
  """Execution state guarded by a lock; durable mutations outlive their calling RPC."""

  device_type = "CeligoSimulator"
  display_name = "Simulated Celigo"
  supported_operations: tuple[str, ...] = ("Initialize", "SimulatedAcquisition", "SimulatedPreview")

  def __init__(
    self,
    directory: Path,
    device_id: str = "celigo-sim-01",
    storage_quota=DEFAULT_QUOTA,
    max_jobs=1024,
    configuration_id: str = "simulation-v1",
    *,
    decode_spec: Callable[[bytes], Specification] = SimulationSpec.decode,
    spec_format="simulation.v1",
  ):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id):
      raise ValueError("invalid device ID")
    if not 1 <= len(configuration_id) <= 128:
      raise ValueError("invalid configuration ID")
    self.device_id = device_id
    self.configuration_id = configuration_id
    self.store = Store(
      directory,
      device_id,
      storage_quota,
      max_jobs,
      decode_spec=decode_spec,
      spec_format=spec_format,
    )
    self.jobs: dict[str, SimulatedJob] = {}
    self.requests: dict[str, SimulatedJob] = {}
    self.active_job: SimulatedJob | None = None
    self.closing = False
    self.initialized = False
    self.fault = ""
    self.lock = asyncio.Lock()
    self.read_slots = asyncio.Semaphore(2)
    self.commands: set[asyncio.Task] = set()
    self.command_limit = 32
    self.status = device_pb.DeviceStatus(device_id=device_id, instance_id=uuid4().hex)
    self.updates: Latest[device_pb.DeviceStatus] = Latest()
    self.previews: Latest[image_pb.GetFrameRequest] = Latest()

  async def initialize(self) -> None:
    await asyncio.sleep(0)

  def validate_start(self, job: SimulatedJob) -> None:
    """Hardware-specific admission checks, before committing a Running state."""

  async def capture(self, job: SimulatedJob, sequence: int) -> stored_pb.ImageFrame | None:
    if not isinstance(job.spec, SimulationSpec):
      raise TypeError("simulator requires a SimulationSpec")
    return await asyncio.to_thread(make_frame, job.ref, sequence, job.spec, job.preview)

  async def finish_operation(self, job: SimulatedJob, failed: bool) -> None:
    """Hardware adapters finish their cleanup before ownership or status is released."""

  def validate_spec(self, spec: Specification, initialize: bool) -> None:
    if not isinstance(spec, SimulationSpec):
      raise TypeError("simulator requires a SimulationSpec")

  async def open(self) -> None:
    for spec, status in await self.store.open():
      job = SimulatedJob(self, spec, status)
      self.jobs[job.ref.job_id] = job
      self.requests[status.request_id] = job
    self.previews.value = await self.io(self.store.latest_preview)
    self.publish_device()

  def ensure_available(self) -> None:
    if self.closing:
      raise StateError("service is stopping")
    if self.fault:
      raise StorageError("service fault requires recovery: " + self.fault)

  async def io(self, function: Callable[..., T], *args: object) -> T:
    """Run a storage operation and surface durable-write failures as device faults."""
    try:
      return await self.store.call(function, *args)
    except StorageError as exc:
      self.fault = str(exc)
      if self.active_job:
        self.active_job.stop_requested.set()
      self.publish_device()
      raise

  async def command(self, operation: Coroutine[object, object, T]) -> T:
    """A lost response never interrupts the commit/executor bookkeeping boundary."""
    if self.closing or len(self.commands) >= self.command_limit:
      operation.close()
      if self.closing:
        raise StateError("service is stopping")
      raise CapacityError("too many pending durable commands")
    task = asyncio.create_task(operation)
    self.commands.add(task)

    def finished(done):
      self.commands.discard(done)
      if not done.cancelled():
        done.exception()  # Observe failures even when the RPC disconnected.

    task.add_done_callback(finished)
    return await asyncio.shield(task)

  def publish_device(self) -> None:
    if self.fault:
      self.status.state = device_pb.OPERATIONAL_STATE_FAULTED
    elif self.active_job:
      self.status.state = (
        device_pb.OPERATIONAL_STATE_INITIALIZING
        if self.active_job.status.kind == "Initialize"
        else device_pb.OPERATIONAL_STATE_BUSY
      )
    else:
      self.status.state = (
        device_pb.OPERATIONAL_STATE_READY
        if self.initialized
        else device_pb.OPERATIONAL_STATE_UNINITIALIZED
      )
    self.status.fault_summary = self.fault
    self.status.active_job_id = self.active_job.ref.job_id if self.active_job else ""
    self.status.observation_valid = True
    self.status.observed_at.GetCurrentTime()
    self.status.revision += 1
    self.updates.publish(self.status)

  async def create(
    self,
    request_id: str,
    spec: Specification,
    preview=False,
    configuration_id="",
    initialize=False,
  ) -> SimulatedJob:
    async with self.lock:
      self.ensure_available()
      self.validate_spec(spec, initialize)
      if not 1 <= len(request_id) <= 128:
        raise ValueError("request_id must be 1..128 characters")
      reservation = 0 if initialize else spec.reservation()
      kind = "Initialize" if initialize else "Preview" if preview else "Acquisition"
      configuration_id = configuration_id or self.configuration_id
      if preview and spec.frame_count != 1:
        raise ValueError("preview requires exactly one frame")
      if request_id in self.requests:
        job = self.requests[request_id]
        if (job.spec, job.status.kind, job.status.configuration_id) != (
          spec,
          kind,
          configuration_id,
        ):
          raise ValueError("request_id already identifies different arguments")
        return job
      if configuration_id != self.configuration_id:
        raise StateError("requested configuration no longer matches the service")
      status = stored_pb.JobStatus(
        job=job_pb.JobRef(device_id=self.device_id, job_id=uuid4().hex),
        request_id=request_id,
        kind=kind,
        state=job_pb.JOB_STATE_READY,
        outcome=job_pb.OUTCOME_PENDING,
        phase="Ready",
        total=1 if initialize else spec.frame_count,
        progress_unit="Steps" if initialize else "Frames",
        configuration_id=configuration_id,
      )
      status.created_at.GetCurrentTime()
      status = advance(status)
      await self.io(self.store.create, spec, status, reservation)
      job = SimulatedJob(self, spec, status)
      self.jobs[job.ref.job_id] = job
      self.requests[request_id] = job
      return job

  async def stop(self) -> None:
    self.closing = True
    try:
      while self.commands:
        await asyncio.gather(*list(self.commands), return_exceptions=True)
      if self.active_job:
        try:
          await self.active_job.cancel()
        except StorageError:
          logger.exception("Shutdown cancellation could not be persisted")
      await asyncio.gather(*(job.task for job in self.jobs.values() if job.task))
    finally:
      await self.store.close()

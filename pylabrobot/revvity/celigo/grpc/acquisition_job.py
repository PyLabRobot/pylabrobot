"""Celigo jobs that retain instrument ownership until execution and cleanup finish."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import TYPE_CHECKING, TypeVar

from pylabrobot.revvity.celigo.grpc.job_specification import JobSpecification
from pylabrobot.revvity.celigo.grpc.job_store import advance_job_status
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.rpc.errors import BusyError, StateError, StorageError
from pylabrobot.rpc.transport import Latest, copy_message
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

if TYPE_CHECKING:
  from pylabrobot.revvity.celigo.grpc.session import CeligoSession

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def run_owned_operation(operation: Coroutine[object, object, T]) -> T:
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


class AcquisitionJob:
  def __init__(self, session: CeligoSession, spec: JobSpecification, status: stored_pb.JobStatus):
    self.session = session
    self.spec = spec
    self.status = status
    self.ref = copy_message(status.job)
    self.preview = status.kind == "Preview"
    self.stop_requested = asyncio.Event()
    if status.cancellation_requested:
      self.stop_requested.set()
    self.task: asyncio.Task[None] | None = None
    self.updates: Latest[stored_pb.JobStatus] = Latest(copy_message(status))

  def publish_status(self, status: stored_pb.JobStatus) -> None:
    """Replace the job's cached status and notify its subscribers."""
    self.status = status
    self.updates.publish(status)

  async def start(self) -> stored_pb.JobStatus:
    async with self.session.lock:
      self.session.ensure_available()
      if self.status.state == job_pb.JOB_STATE_RUNNING:
        return copy_message(self.status)
      if self.status.state != job_pb.JOB_STATE_READY or self.stop_requested.is_set():
        raise StateError("finished or cancelling jobs cannot start")
      if self.session.active_job is not None or self.session.door_moving:
        raise BusyError("another operation owns the instrument")
      if self.status.configuration_id != self.session.configuration_id:
        raise StateError("job configuration no longer matches the service")
      if self.status.kind != "Initialize" and not self.session.initialized:
        raise StateError("explicit initialization is required before acquisition")
      self.session.validate_job_start(self)
      status = advance_job_status(
        self.status,
        state=job_pb.JOB_STATE_RUNNING,
        phase="Initializing" if self.status.kind == "Initialize" else "Capturing",
      )
      await self.session.run_storage_operation(self.session.store.save_job_status, status, "start")
      self.publish_status(status)
      self.session.active_job = self
      self.session.publish_status()
      self.task = asyncio.create_task(self.run(), name=f"celigo-grpc-{self.ref.job_id}")
      return copy_message(self.status)

  async def cancel(self) -> stored_pb.JobStatus:
    # Signal execution immediately even if a disk commit currently holds the lock.
    if self.status.state != job_pb.JOB_STATE_FINISHED:
      self.stop_requested.set()
    async with self.session.lock:
      if self.status.state != job_pb.JOB_STATE_FINISHED and not self.status.cancellation_requested:
        status = advance_job_status(self.status, cancellation_requested=True, phase="Stopping")
        if status.state == job_pb.JOB_STATE_READY:
          status.state, status.outcome, status.phase = (
            job_pb.JOB_STATE_FINISHED,
            job_pb.OUTCOME_CANCELLED,
            "Finished",
          )
        await self.session.run_storage_operation(
          self.session.store.save_job_status, status, "cancel"
        )
        self.publish_status(status)
      return copy_message(self.status)

  async def list_frames(self, after_sequence: int, limit: int) -> image_pb.FramePage:
    return await self.session.run_storage_operation(
      self.session.store.list_frames, self.ref.job_id, after_sequence, limit
    )

  async def get_frame(self, frame_id: str) -> stored_pb.ImageFrame:
    async with self.session.read_slots:
      return await self.session.run_storage_operation(
        self.session.store.get_frame, self.ref.job_id, frame_id
      )

  async def release_images(self) -> stored_pb.JobStatus:
    async with self.session.lock:
      if self.status.state != job_pb.JOB_STATE_FINISHED or not self.status.state_durable:
        raise StateError("only durably finished jobs can release images")
      if not self.status.images_released:
        status = advance_job_status(self.status, images_released=True)
        await self.session.run_storage_operation(self.session.store.release_images, status)
        self.publish_status(status)
        self.session.previews.value = await self.session.run_storage_operation(
          self.session.store.get_latest_preview
        )
      return copy_message(self.status)

  async def run(self) -> None:
    error = ""
    interrupted = False
    try:
      if self.status.kind == "Initialize":
        if not self.stop_requested.is_set():
          await run_owned_operation(self.session.initialize())
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
          frame = await run_owned_operation(self.session.capture_frame(self, sequence))
          if frame is None:
            break
          async with self.session.lock:
            status = advance_job_status(self.status, completed=sequence)
            await self.session.run_storage_operation(self.session.store.commit_frame, frame, status)
            self.publish_status(status)
            if self.preview:
              self.session.previews.publish(
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
        await run_owned_operation(self.session.finish_operation(self, bool(error)))
      except (Exception, asyncio.CancelledError) as exc:
        logger.exception("Operation cleanup failed for %s", self.ref.job_id)
        error = f"{error}; cleanup failed: {type(exc).__name__}: {exc}".lstrip("; ")
      async with self.session.lock:
        outcome = (
          job_pb.OUTCOME_INTERRUPTED
          if interrupted
          else job_pb.OUTCOME_FAILED
          if error
          else (
            job_pb.OUTCOME_CANCELLED if self.stop_requested.is_set() else job_pb.OUTCOME_SUCCEEDED
          )
        )
        status = advance_job_status(
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
          await self.session.run_storage_operation(
            self.session.store.save_job_status, status, "finish"
          )
        except StorageError as exc:
          # The disk may still contain Running. Recovery will mark it Interrupted.
          status.state_durable = False
          status.outcome = job_pb.OUTCOME_FAILED
          status.error = f"Completion could not be persisted: {exc}"
        self.publish_status(status)
        if self.status.kind == "Initialize":
          self.session.initialized = status.outcome == job_pb.OUTCOME_SUCCEEDED
        self.session.active_job = None
        self.session.publish_status()

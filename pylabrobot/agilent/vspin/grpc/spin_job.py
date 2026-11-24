"""VSpin jobs that retain instrument ownership through motion and cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import TYPE_CHECKING, TypeVar

from pylabrobot.agilent.vspin.grpc.job_specification import JobSpecification
from pylabrobot.agilent.vspin.grpc.job_store import advance_job_status
from pylabrobot.rpc.errors import BusyError, StateError, StorageError
from pylabrobot.rpc.transport import Latest, copy_message
from pylabrobot.rpc.v1 import jobs_pb2 as pb

if TYPE_CHECKING:
  from pylabrobot.agilent.vspin.grpc.session import VSpinSession

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def run_owned_operation(operation: Coroutine[object, object, T]) -> T:
  """Wait for in-flight hardware work even if the executor is cancelled."""
  task = asyncio.create_task(operation)
  try:
    return await asyncio.shield(task)
  except asyncio.CancelledError:
    try:
      await task
    except Exception:
      logger.exception("In-flight VSpin operation failed during executor cancellation")
    raise


class SpinJob:
  """Execute one durable initialization or centrifuge run."""

  def __init__(self, session: VSpinSession, spec: JobSpecification, status: pb.JobStatus) -> None:
    """Bind retained settings and status without starting execution."""
    self.session = session
    self.spec = spec
    self.status = status
    self.ref = copy_message(status.job)
    self.stop_requested = asyncio.Event()
    if status.cancellation_requested:
      self.stop_requested.set()
    self.task: asyncio.Task[None] | None = None
    self.updates: Latest[pb.JobStatus] = Latest(status)

  def publish_status(self, status: pb.JobStatus) -> None:
    """Replace the cached snapshot and notify subscribers."""
    self.status = status
    self.updates.publish(status)

  async def cancel(self) -> pb.JobStatus:
    """Signal cooperative stopping and persist intent while the executor retains ownership."""
    if self.status.state != pb.JOB_STATE_FINISHED:
      self.stop_requested.set()
    async with self.session.lock:
      if self.status.state != pb.JOB_STATE_FINISHED and not self.status.cancellation_requested:
        status = advance_job_status(self.status, cancellation_requested=True, phase="Stopping")
        if status.state == pb.JOB_STATE_READY:
          status.state = pb.JOB_STATE_FINISHED
          status.outcome = pb.OUTCOME_CANCELLED
          status.phase = "Finished"
        await self.session.run_storage_operation(self.session.store.save_job_status, status)
        self.publish_status(status)
      return copy_message(self.status)

  async def run(self) -> None:
    """Finish hardware work and cleanup before committing a terminal outcome."""
    error = ""
    outcome = pb.OUTCOME_SUCCEEDED
    try:
      if not self.stop_requested.is_set():
        if self.spec.spin is None:
          await run_owned_operation(self.session.initialize())
        else:
          await run_owned_operation(self.session.execute_spin(self))
    except asyncio.CancelledError:
      error, outcome = "executor interrupted", pb.OUTCOME_INTERRUPTED
    except Exception as exc:
      error, outcome = str(exc) or type(exc).__name__, pb.OUTCOME_FAILED
    try:
      await run_owned_operation(self.session.finish_operation(self, failed=bool(error)))
    except Exception as exc:
      error, outcome = str(exc) or type(exc).__name__, pb.OUTCOME_FAILED
    async with self.session.lock:
      if not error and self.stop_requested.is_set():
        outcome = pb.OUTCOME_CANCELLED
      status = advance_job_status(
        self.status,
        state=pb.JOB_STATE_FINISHED,
        outcome=outcome,
        phase="Finished",
        error=error,
        completed=1 if outcome == pb.OUTCOME_SUCCEEDED else 0,
        cancellation_requested=self.stop_requested.is_set(),
      )
      try:
        await self.session.run_storage_operation(self.session.store.save_job_status, status)
      except StorageError as exc:
        status.state_durable = False
        status.error = str(exc)
        status.outcome = pb.OUTCOME_FAILED
      self.publish_status(status)
      self.session.active_job = None
      self.session.publish_status()

  async def start(self) -> pb.JobStatus:
    """Commit acceptance before allocating an executor independent of the caller."""
    async with self.session.lock:
      self.session.ensure_available()
      if self.status.state == pb.JOB_STATE_RUNNING:
        return copy_message(self.status)
      if self.status.state != pb.JOB_STATE_READY or self.stop_requested.is_set():
        raise StateError("finished or cancelling jobs cannot start")
      if self.session.active_job is not None or self.session.moving:
        raise BusyError("another operation owns the instrument")
      if self.status.configuration_id != self.session.configuration_id:
        raise StateError("job configuration no longer matches the service")
      if self.spec.spin is None:
        if self.session.driver is not None:
          raise StateError("already initialized; restart the service before reinitializing")
      elif not self.session.initialized:
        raise StateError("explicit initialization is required before spinning")
      status = advance_job_status(
        self.status,
        state=pb.JOB_STATE_RUNNING,
        phase="Initializing" if self.spec.spin is None else "Spinning",
      )
      await self.session.run_storage_operation(self.session.store.save_job_status, status)
      self.publish_status(status)
      self.session.active_job = self
      self.session.publish_status()
      self.task = asyncio.create_task(self.run(), name=f"vspin-grpc-{self.ref.job_id}")
      return copy_message(self.status)

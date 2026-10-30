"""Single-writer SQLite records for VSpin requests, settings, and job transitions."""

from __future__ import annotations

import asyncio
import fcntl
import os
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO, TypeVar

from google.protobuf.message import DecodeError

from pylabrobot.agilent.vspin.grpc.job_specification import JobSpecification
from pylabrobot.rpc.errors import BusyError, CapacityError, DataLossError, StateError, StorageError
from pylabrobot.rpc.transport import copy_message
from pylabrobot.rpc.v1 import jobs_pb2 as pb

T = TypeVar("T")


def advance_job_status(
  status: pb.JobStatus,
  *,
  state: pb.JobState | None = None,
  outcome: pb.Outcome | None = None,
  phase: str | None = None,
  error: str | None = None,
  completed: int | None = None,
  cancellation_requested: bool | None = None,
) -> pb.JobStatus:
  """Construct a separate revision to publish only after its durable commit."""
  result = copy_message(status)
  if state is not None:
    result.state = state
  if outcome is not None:
    result.outcome = outcome
  if phase is not None:
    result.phase = phase
  if error is not None:
    result.error = error
  if completed is not None:
    result.completed = completed
  if cancellation_requested is not None:
    result.cancellation_requested = cancellation_requested
  result.updated_at.GetCurrentTime()
  result.revision += 1
  result.state_durable = True
  return result


class JobStore:
  """Persist bounded job history."""

  def __init__(self, directory: Path, device_id: str, max_jobs: int = 1024) -> None:
    """Configure storage without opening files or starting hardware."""
    if max_jobs < 1:
      raise ValueError("max_jobs must be positive")
    self.directory = directory
    self.device_id = device_id
    self.max_jobs = max_jobs
    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vspin-store")
    self.db: sqlite3.Connection | None = None
    self.lock_file: BinaryIO | None = None
    self.closed = False

  @property
  def connection(self) -> sqlite3.Connection:
    """Require an open connection on the storage worker."""
    if self.db is None:
      raise StorageError("store is not open")
    return self.db

  async def run_in_worker(self, function: Callable[..., T], *args: object) -> T:
    """Keep worker ownership until SQLite finishes, even if the caller is cancelled."""
    if self.closed:
      raise StorageError("store is closed")
    try:
      future = asyncio.get_running_loop().run_in_executor(self.executor, function, *args)
      try:
        return await asyncio.shield(future)
      except asyncio.CancelledError:
        await future
        raise
    except (sqlite3.DatabaseError, OSError) as exc:
      raise StorageError(f"{type(exc).__name__}: {exc}") from exc

  def _open(self) -> list[tuple[JobSpecification, pb.JobStatus]]:
    """Lock, validate, and recover the store without replaying accepted hardware work."""
    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.lock_file = (self.directory / "writer.lock").open("a+b")
    try:
      fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
      raise BusyError("another service owns this data directory") from exc
    self.db = sqlite3.connect(self.directory / "jobs.sqlite3")
    metadata = {"schema_version": "1", "device_id": self.device_id, "spec_format": "vspin.v1"}
    if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():
      if dict(self.db.execute("SELECT key, value FROM metadata")) != metadata:
        raise StateError("store identity or schema mismatch")
    self.db.execute("PRAGMA journal_mode=WAL")
    self.db.execute("PRAGMA synchronous=FULL")
    self.db.executescript("""
      CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        specification BLOB NOT NULL, status BLOB NOT NULL
      );
    """)
    with self.db:
      self.db.executemany("INSERT OR IGNORE INTO metadata VALUES (?, ?)", metadata.items())
    for path in (self.directory, self.directory.parent):
      descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
      try:
        os.fsync(descriptor)
      finally:
        os.close(descriptor)
    result = []
    with self.db:
      for job_id, request_id, specification, record in self.db.execute("SELECT * FROM jobs"):
        try:
          spec = JobSpecification.decode(specification)
          status = pb.JobStatus.FromString(record)
        except (ValueError, TypeError, KeyError, DecodeError) as exc:
          raise DataLossError("invalid retained VSpin job") from exc
        if (status.job.device_id, status.job.job_id, status.request_id, status.kind) != (
          self.device_id,
          job_id,
          request_id,
          spec.kind,
        ):
          raise DataLossError("retained VSpin job identity mismatch")
        if status.state == pb.JOB_STATE_RUNNING:
          status = advance_job_status(
            status,
            state=pb.JOB_STATE_FINISHED,
            outcome=pb.OUTCOME_INTERRUPTED,
            phase="Finished",
            error="service restarted during execution; inspect the instrument",
          )
          self.db.execute(
            "UPDATE jobs SET status=? WHERE job_id=?", (status.SerializeToString(), job_id)
          )
        result.append((spec, status))
    return result

  async def open(self) -> list[tuple[JobSpecification, pb.JobStatus]]:
    """Recover records on the worker, releasing locks if startup fails."""
    try:
      return await self.run_in_worker(self._open)
    except BaseException:
      await self.close()
      raise

  def create_job(self, spec: JobSpecification, status: pb.JobStatus) -> None:
    """Commit inert intent and retain request identity for the store's lifetime."""
    db = self.connection
    with db:
      if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= self.max_jobs:
        raise CapacityError("retained job limit reached")
      db.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?)",
        (
          status.job.job_id,
          status.request_id,
          spec.encode(),
          status.SerializeToString(),
        ),
      )

  def save_job_status(self, status: pb.JobStatus) -> None:
    """Commit an accepted start, cancellation request, or terminal outcome."""
    with self.connection:
      cursor = self.connection.execute(
        "UPDATE jobs SET status=? WHERE job_id=?",
        (
          status.SerializeToString(),
          status.job.job_id,
        ),
      )
      if cursor.rowcount != 1:
        raise DataLossError("job disappeared from the store")

  def _close(self) -> None:
    """Close SQLite on its owning worker and release the writer lock."""
    try:
      if self.db is not None:
        self.db.close()
        self.db = None
    finally:
      if self.lock_file is not None:
        self.lock_file.close()
        self.lock_file = None

  async def close(self) -> None:
    """Finish storage operations before shutting down the worker."""
    if not self.closed:
      try:
        await self.run_in_worker(self._close)
      finally:
        self.closed = True
        self.executor.shutdown(wait=True)

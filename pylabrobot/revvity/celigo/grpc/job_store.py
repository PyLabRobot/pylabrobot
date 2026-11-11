"""Single-writer SQLite job/image store. All I/O runs on one dedicated thread.

Image bytes, metadata, job progress, and the transition record commit together.
The store keeps request IDs after image release so retries cannot repeat motion.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import shutil
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, TypeVar

from pylabrobot.revvity.celigo.grpc.job_specification import JobSpecification
from pylabrobot.revvity.celigo.grpc.limits import MAX_FRAME_BYTES
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.rpc.errors import (
  BusyError,
  CapacityError,
  DataLossError,
  StateError,
  StorageError,
)
from pylabrobot.rpc.transport import copy_message
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

T = TypeVar("T")
SCHEMA_VERSION = "1"
DEFAULT_QUOTA = 10 * 1024 * 1024 * 1024
DISK_HEADROOM = 64 * 1024 * 1024


def advance_job_status(
  status: stored_pb.JobStatus,
  *,
  state: job_pb.JobState | None = None,
  outcome: job_pb.Outcome | None = None,
  phase: str | None = None,
  error: str | None = None,
  completed: int | None = None,
  cancellation_requested: bool | None = None,
  images_released: bool | None = None,
) -> stored_pb.JobStatus:
  """Construct the next immutable, durably publishable status revision."""
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
  if images_released is not None:
    result.images_released = images_released
  result.updated_at.GetCurrentTime()
  result.revision += 1
  result.state_durable = True
  return result


def build_public_job_status(status: stored_pb.JobStatus) -> job_pb.JobStatus:
  """Project durable state onto the common job contract, omitting image retention."""
  result = job_pb.JobStatus.FromString(status.SerializeToString())
  result.DiscardUnknownFields()
  return result


class JobStore:
  def __init__(
    self,
    directory: Path,
    device_id: str,
    quota=DEFAULT_QUOTA,
    max_jobs=1024,
    *,
    decode_spec: Callable[[bytes], JobSpecification],
    spec_format: str,
  ):
    if quota <= 0 or max_jobs <= 0:
      raise ValueError("storage quota and max_jobs must be positive")
    self.directory = directory
    self.device_id = device_id
    self.quota = quota
    self.max_jobs = max_jobs
    self.decode_spec = decode_spec
    self.spec_format = spec_format
    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="celigo-store")
    self.db: sqlite3.Connection | None = None
    self.lock_file: BinaryIO | None = None
    self.closed = False

  @property
  def connection(self) -> sqlite3.Connection:
    if self.db is None:
      raise StorageError("store is not open")
    return self.db

  async def run_in_worker(self, function: Callable[..., T], *args) -> T:
    """Run storage work on the dedicated thread and wait for SQLite use to finish."""
    if self.closed:
      raise StorageError("store is closed")
    try:
      future = asyncio.get_running_loop().run_in_executor(self.executor, function, *args)
      try:
        return await asyncio.shield(future)
      except asyncio.CancelledError:
        await future  # Keep the I/O slot until the worker actually stops using SQLite.
        raise
    except (sqlite3.DatabaseError, OSError) as exc:
      raise StorageError(f"{type(exc).__name__}: {exc}") from exc

  async def open(self) -> list[tuple[JobSpecification, stored_pb.JobStatus]]:
    try:
      return await self.run_in_worker(self._open)
    except BaseException:
      await self.close()
      raise

  def _open(self) -> list[tuple[JobSpecification, stored_pb.JobStatus]]:
    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.lock_file = (self.directory / "writer.lock").open("a+b")
    try:
      fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
      raise BusyError("another service owns this data directory") from exc
    self.db = sqlite3.connect(self.directory / "jobs.sqlite3", isolation_level=None)
    self.db.row_factory = sqlite3.Row
    # Refuse a differently bound/versioned store before changing its schema or mode.
    if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():
      for key, expected in (("schema_version", SCHEMA_VERSION), ("device_id", self.device_id)):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if row is None or row[0] != expected:
          raise StateError(f"store {key} mismatch")
      row = self.db.execute("SELECT value FROM metadata WHERE key='spec_format'").fetchone()
      existing_format = row[0] if row is not None else None
      if existing_format != self.spec_format:
        raise StateError("store specification format mismatch")
    self.db.execute("PRAGMA journal_mode=WAL")
    self.db.execute("PRAGMA synchronous=FULL")
    self.db.execute("PRAGMA foreign_keys=ON")
    self.db.execute("PRAGMA cache_size=-2048")
    self.db.execute("PRAGMA mmap_size=0")
    self.db.executescript("""
      CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        specification BLOB NOT NULL, status BLOB NOT NULL, reservation INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS transitions (
        job_id TEXT NOT NULL REFERENCES jobs(job_id), revision INTEGER NOT NULL,
        status BLOB NOT NULL, PRIMARY KEY(job_id, revision)
      );
      CREATE TABLE IF NOT EXISTS frames (
        job_id TEXT NOT NULL REFERENCES jobs(job_id), frame_id TEXT NOT NULL UNIQUE,
        sequence INTEGER NOT NULL, metadata BLOB NOT NULL, image BLOB,
        byte_length INTEGER NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(job_id, sequence)
      );
    """)
    with self.begin_transaction():
      for key, value in (
        ("schema_version", SCHEMA_VERSION),
        ("device_id", self.device_id),
        ("spec_format", self.spec_format),
      ):
        existing = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if existing is not None and existing[0] != value:
          raise StateError(f"store {key} mismatch")
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
    # Ensure the initial database and WAL directory entries are durable too.
    for directory in (self.directory, self.directory.parent):
      descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
      try:
        os.fsync(descriptor)
      finally:
        os.close(descriptor)
    records = []
    with self.begin_transaction():
      for row in self.db.execute("SELECT * FROM jobs").fetchall():
        spec = self.decode_spec(row["specification"])
        status = stored_pb.JobStatus.FromString(row["status"])
        if status.state == job_pb.JOB_STATE_RUNNING:
          status = advance_job_status(
            status,
            state=job_pb.JOB_STATE_FINISHED,
            outcome=job_pb.OUTCOME_INTERRUPTED,
            phase="Finished",
            error="Service stopped before durable completion; execution was not replayed.",
          )
          self._update_job_status(status, self.get_retained_bytes(status.job.job_id))
        records.append((spec, status))
    return records

  @contextmanager
  def begin_transaction(self):
    """Begin an immediate transaction and commit or roll it back on context exit."""
    db = self.connection
    db.execute("BEGIN IMMEDIATE")
    try:
      yield
      db.execute("COMMIT")
    except BaseException:
      if db.in_transaction:
        db.execute("ROLLBACK")
      raise

  def on_transaction_boundary(self, point: str, status: stored_pb.JobStatus) -> None:
    """Crash-test seam at transaction boundaries; production has no fault hooks."""

  def get_reserved_bytes(self) -> int:
    """Read the total storage reservation across all retained jobs."""
    return int(
      self.connection.execute("SELECT COALESCE(SUM(reservation), 0) FROM jobs").fetchone()[0]
    )

  def get_retained_bytes(self, job_id: str) -> int:
    """Read the size of a job's retained image payloads."""
    return int(
      self.connection.execute(
        "SELECT COALESCE(SUM(byte_length), 0) FROM frames WHERE job_id=? AND image IS NOT NULL",
        (job_id,),
      ).fetchone()[0]
    )

  def check_disk_capacity(self, extra: int = 0) -> None:
    """Admission estimate; actual write errors still require transaction rollback."""
    retained = self.connection.execute(
      "SELECT COALESCE(SUM(byte_length), 0) FROM frames WHERE image IS NOT NULL"
    ).fetchone()[0]
    outstanding = max(0, self.get_reserved_bytes() - retained)
    if shutil.disk_usage(self.directory).free < outstanding + extra + DISK_HEADROOM:
      raise CapacityError("insufficient free disk space for reserved images and SQLite headroom")

  def create_job(
    self, spec: JobSpecification, status: stored_pb.JobStatus, reservation: int
  ) -> None:
    """Reserve storage and persist a job's specification and initial status."""
    with self.begin_transaction():
      if self.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= self.max_jobs:
        raise CapacityError(
          "job record capacity exhausted; request IDs are never silently discarded"
        )
      if self.get_reserved_bytes() + reservation > self.quota:
        raise CapacityError("image storage quota exhausted")
      self.check_disk_capacity(reservation)
      self.connection.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
        (
          status.job.job_id,
          status.request_id,
          spec.to_proto().SerializeToString(),
          status.SerializeToString(),
          reservation,
        ),
      )
      self._record_transition(status)
      self.on_transaction_boundary("create.before_commit", status)
    self.on_transaction_boundary("create.after_commit", status)

  def _record_transition(self, status: stored_pb.JobStatus) -> None:
    """Append a job status revision to its transition history."""
    self.connection.execute(
      "INSERT INTO transitions VALUES (?, ?, ?)",
      (
        status.job.job_id,
        status.revision,
        status.SerializeToString(),
      ),
    )

  def _update_job_status(self, status: stored_pb.JobStatus, reservation: int | None = None) -> None:
    """Replace stored status and record its transition in the current transaction."""
    if reservation is None:
      self.connection.execute(
        "UPDATE jobs SET status=? WHERE job_id=?",
        (
          status.SerializeToString(),
          status.job.job_id,
        ),
      )
    else:
      self.connection.execute(
        "UPDATE jobs SET status=?, reservation=? WHERE job_id=?",
        (
          status.SerializeToString(),
          reservation,
          status.job.job_id,
        ),
      )
    self._record_transition(status)

  def save_job_status(self, status: stored_pb.JobStatus, action: str) -> None:
    """Commit a job transition and release unused reservations when it finishes."""
    with self.begin_transaction():
      reservation = (
        self.get_retained_bytes(status.job.job_id)
        if status.state == job_pb.JOB_STATE_FINISHED
        else None
      )
      if action == "start":
        self.check_disk_capacity()
      self._update_job_status(status, reservation)
      self.on_transaction_boundary(f"{action}.before_commit", status)
    self.on_transaction_boundary(f"{action}.after_commit", status)

  def commit_frame(self, frame: stored_pb.ImageFrame, status: stored_pb.JobStatus) -> None:
    image = frame.image
    if len(image) > MAX_FRAME_BYTES or hashlib.sha256(image).hexdigest() != frame.sha256:
      raise DataLossError("capture image is oversized or failed checksum validation")
    metadata = copy_message(frame)
    metadata.ClearField("image")
    with self.begin_transaction():
      reservation = self.connection.execute(
        "SELECT reservation FROM jobs WHERE job_id=?",
        (status.job.job_id,),
      ).fetchone()[0]
      if self.get_retained_bytes(status.job.job_id) + len(image) > reservation:
        raise CapacityError("encoded image exceeds reserved storage")
      self.check_disk_capacity()
      self.connection.execute(
        "INSERT INTO frames VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
          frame.job.job_id,
          frame.frame_id,
          frame.sequence,
          metadata.SerializeToString(),
          image,
          len(image),
          frame.sha256,
        ),
      )
      self._update_job_status(status)
      self.on_transaction_boundary("frame.before_commit", status)
    self.on_transaction_boundary("frame.after_commit", status)

  def list_frames(self, job_id: str, after_sequence: int, limit: int) -> image_pb.FramePage:
    if not 1 <= limit <= 64:
      raise ValueError("limit must be 1..64")
    rows = self.connection.execute(
      "SELECT frame_id, sequence, byte_length, sha256, image IS NOT NULL AS available "
      "FROM frames WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?",
      (job_id, after_sequence, limit),
    ).fetchall()
    return image_pb.FramePage(frames=[image_pb.FrameInfo(**dict(row)) for row in rows])

  def get_frame(self, job_id: str, frame_id: str) -> stored_pb.ImageFrame:
    row = self.connection.execute(
      "SELECT * FROM frames WHERE job_id=? AND frame_id=?",
      (job_id, frame_id),
    ).fetchone()
    if row is None:
      raise KeyError(frame_id)
    if row["image"] is None:
      raise StateError("image bytes were explicitly released; metadata remains available")
    image = row["image"]
    if len(image) != row["byte_length"] or hashlib.sha256(image).hexdigest() != row["sha256"]:
      raise DataLossError("retained image failed integrity validation")
    frame = stored_pb.ImageFrame.FromString(row["metadata"])
    frame.image = image
    return frame

  def release_images(self, status: stored_pb.JobStatus) -> None:
    with self.begin_transaction():
      self.connection.execute("UPDATE frames SET image=NULL WHERE job_id=?", (status.job.job_id,))
      self._update_job_status(status, 0)
      self.on_transaction_boundary("release.before_commit", status)
    self.on_transaction_boundary("release.after_commit", status)

  def get_latest_preview(self) -> image_pb.GetFrameRequest | None:
    """Find the most recently stored preview whose pixels are still retained."""
    rows = self.connection.execute(
      "SELECT job_id, frame_id, metadata FROM frames WHERE image IS NOT NULL ORDER BY rowid DESC"
    )
    for row in rows:
      metadata = stored_pb.ImageFrame.FromString(row["metadata"])
      if metadata.representation in ("Preview", "Scan"):
        return image_pb.GetFrameRequest(job=metadata.job, frame_id=row["frame_id"])
    return None

  def _close(self) -> None:
    try:
      if self.db is not None:
        self.db.close()
        self.db = None
    finally:
      if self.lock_file is not None:
        self.lock_file.close()
        self.lock_file = None

  async def close(self) -> None:
    if not self.closed:
      try:
        await asyncio.get_running_loop().run_in_executor(self.executor, self._close)
      finally:
        self.closed = True
        self.executor.shutdown(wait=True)

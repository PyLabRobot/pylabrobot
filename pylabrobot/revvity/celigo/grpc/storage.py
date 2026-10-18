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

from pylabrobot.revvity.celigo.grpc.errors import (
  BusyError,
  CapacityError,
  DataLossError,
  StateError,
  StorageError,
)
from pylabrobot.revvity.celigo.grpc.model import (
  MAX_FRAME_BYTES,
  SimulationSpec,
  Specification,
  copy_message,
)
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb

T = TypeVar("T")
SCHEMA_VERSION = "1"
DEFAULT_QUOTA = 256 * 1024 * 1024
DISK_HEADROOM = 64 * 1024 * 1024


def advance(
  status: pb.JobStatus,
  *,
  state: pb.JobState | None = None,
  outcome: pb.Outcome | None = None,
  phase: str | None = None,
  error: str | None = None,
  completed: int | None = None,
  cancellation_requested: bool | None = None,
  images_released: bool | None = None,
) -> pb.JobStatus:
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


class Store:
  def __init__(
    self,
    directory: Path,
    device_id: str,
    quota=DEFAULT_QUOTA,
    max_jobs=1024,
    *,
    decode_spec: Callable[[bytes], Specification] = SimulationSpec.decode,
    spec_format="simulation.v1",
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

  async def call(self, function: Callable[..., T], *args) -> T:
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

  async def open(self) -> list[tuple[Specification, pb.JobStatus]]:
    try:
      return await self.call(self._open)
    except BaseException:
      await self.close()
      raise

  def _open(self) -> list[tuple[Specification, pb.JobStatus]]:
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
      existing_format = row[0] if row is not None else "simulation.v1"
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
    with self.transaction():
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
    with self.transaction():
      for row in self.db.execute("SELECT * FROM jobs").fetchall():
        spec = self.decode_spec(row["specification"])
        status = pb.JobStatus.FromString(row["status"])
        if status.state == pb.JOB_STATE_RUNNING:
          status = advance(
            status,
            state=pb.JOB_STATE_FINISHED,
            outcome=pb.OUTCOME_INTERRUPTED,
            phase="Finished",
            error="Service stopped before durable completion; execution was not replayed.",
          )
          self._update(status, self.retained_bytes(status.job.job_id))
        records.append((spec, status))
    return records

  @contextmanager
  def transaction(self):
    db = self.connection
    db.execute("BEGIN IMMEDIATE")
    try:
      yield
      db.execute("COMMIT")
    except BaseException:
      if db.in_transaction:
        db.execute("ROLLBACK")
      raise

  def checkpoint(self, point: str, status: pb.JobStatus) -> None:
    """Crash-test seam at transaction boundaries; production has no fault hooks."""

  def reserved_bytes(self) -> int:
    return int(
      self.connection.execute("SELECT COALESCE(SUM(reservation), 0) FROM jobs").fetchone()[0]
    )

  def retained_bytes(self, job_id: str) -> int:
    return int(
      self.connection.execute(
        "SELECT COALESCE(SUM(byte_length), 0) FROM frames WHERE job_id=? AND image IS NOT NULL",
        (job_id,),
      ).fetchone()[0]
    )

  def check_disk(self, extra: int = 0) -> None:
    """Admission estimate; actual write errors still require transaction rollback."""
    retained = self.connection.execute(
      "SELECT COALESCE(SUM(byte_length), 0) FROM frames WHERE image IS NOT NULL"
    ).fetchone()[0]
    outstanding = max(0, self.reserved_bytes() - retained)
    if shutil.disk_usage(self.directory).free < outstanding + extra + DISK_HEADROOM:
      raise CapacityError("insufficient free disk space for reserved images and SQLite headroom")

  def create(self, spec: Specification, status: pb.JobStatus, reservation: int) -> None:
    with self.transaction():
      if self.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= self.max_jobs:
        raise CapacityError(
          "job record capacity exhausted; request IDs are never silently discarded"
        )
      if self.reserved_bytes() + reservation > self.quota:
        raise CapacityError("image storage quota exhausted")
      self.check_disk(reservation)
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
      self._journal(status)
      self.checkpoint("create.before_commit", status)
    self.checkpoint("create.after_commit", status)

  def _journal(self, status: pb.JobStatus) -> None:
    self.connection.execute(
      "INSERT INTO transitions VALUES (?, ?, ?)",
      (
        status.job.job_id,
        status.revision,
        status.SerializeToString(),
      ),
    )

  def _update(self, status: pb.JobStatus, reservation: int | None = None) -> None:
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
    self._journal(status)

  def save(self, status: pb.JobStatus, action: str) -> None:
    with self.transaction():
      reservation = (
        self.retained_bytes(status.job.job_id) if status.state == pb.JOB_STATE_FINISHED else None
      )
      if action == "start":
        self.check_disk()
      self._update(status, reservation)
      self.checkpoint(f"{action}.before_commit", status)
    self.checkpoint(f"{action}.after_commit", status)

  def commit_frame(self, frame: pb.ImageFrame, status: pb.JobStatus) -> None:
    image = frame.image
    if len(image) > MAX_FRAME_BYTES or hashlib.sha256(image).hexdigest() != frame.sha256:
      raise DataLossError("capture image is oversized or failed checksum validation")
    metadata = copy_message(frame)
    metadata.ClearField("image")
    with self.transaction():
      reservation = self.connection.execute(
        "SELECT reservation FROM jobs WHERE job_id=?",
        (status.job.job_id,),
      ).fetchone()[0]
      if self.retained_bytes(status.job.job_id) + len(image) > reservation:
        raise CapacityError("encoded image exceeds reserved storage")
      self.check_disk()
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
      self._update(status)
      self.checkpoint("frame.before_commit", status)
    self.checkpoint("frame.after_commit", status)

  def list_frames(self, job_id: str, after_sequence: int, limit: int) -> pb.FramePage:
    if not 1 <= limit <= 64:
      raise ValueError("limit must be 1..64")
    rows = self.connection.execute(
      "SELECT frame_id, sequence, byte_length, sha256, image IS NOT NULL AS available "
      "FROM frames WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?",
      (job_id, after_sequence, limit),
    ).fetchall()
    return pb.FramePage(frames=[pb.FrameInfo(**dict(row)) for row in rows])

  def get_frame(self, job_id: str, frame_id: str) -> pb.ImageFrame:
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
    frame = pb.ImageFrame.FromString(row["metadata"])
    frame.image = image
    return frame

  def release_images(self, status: pb.JobStatus) -> None:
    with self.transaction():
      self.connection.execute("UPDATE frames SET image=NULL WHERE job_id=?", (status.job.job_id,))
      self._update(status, 0)
      self.checkpoint("release.before_commit", status)
    self.checkpoint("release.after_commit", status)

  def latest_preview(self) -> pb.GetFrameRequest | None:
    rows = self.connection.execute(
      "SELECT job_id, frame_id, metadata FROM frames WHERE image IS NOT NULL ORDER BY rowid DESC"
    )
    for row in rows:
      metadata = pb.ImageFrame.FromString(row["metadata"])
      if metadata.representation == "Preview":
        return pb.GetFrameRequest(job=metadata.job, frame_id=row["frame_id"])
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

"""Own a VSpin driver, durable jobs, and device operations."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import re
from collections.abc import AsyncGenerator, Callable, Coroutine
from pathlib import Path
from typing import BinaryIO, Literal, TypeVar
from uuid import uuid4

from pylabrobot.__version__ import __version__
from pylabrobot.agilent.vspin.grpc.job_specification import JobSpecification, Spin
from pylabrobot.agilent.vspin.grpc.job_store import JobStore, advance_job_status
from pylabrobot.agilent.vspin.grpc.spin_job import SpinJob, run_owned_operation
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2 as pb
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.rpc import device, jobs
from pylabrobot.rpc.errors import BusyError, CapacityError, StateError, StorageError
from pylabrobot.rpc.transport import API_VERSION, Latest, copy_message
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

logger = logging.getLogger(__name__)
T = TypeVar("T")
Motion = Literal[
  "OpenDoor",
  "CloseDoor",
  "LockDoor",
  "UnlockDoor",
  "LockBucket",
  "UnlockBucket",
  "GoToBucket1",
  "GoToBucket2",
]


class VSpinSession:
  """Manage one instrument through Python methods shared by the service adapters."""

  supported_operations = (
    "Initialize",
    "Spin",
    "OpenDoor",
    "CloseDoor",
    "LockDoor",
    "UnlockDoor",
    "LockBucket",
    "UnlockBucket",
    "GoToBucket1",
    "GoToBucket2",
  )

  def __init__(
    self,
    directory: Path,
    device_id: str,
    driver_factory: Callable[[], VSpin],
    hardware_lock: Path,
    *,
    connection_id: str = "default",
    max_jobs: int = 1024,
  ) -> None:
    """Configure driver ownership, connection identity, and retained job capacity."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id):
      raise ValueError("invalid device ID")
    self.device_id = device_id
    self.configuration_id = hashlib.sha256(("vspin.v1:" + connection_id).encode()).hexdigest()
    self.driver_factory = driver_factory
    self.driver: VSpin | None = None
    self.hardware_lock = hardware_lock
    self.lock_file: BinaryIO | None = None
    self.store = JobStore(directory, device_id, max_jobs=max_jobs)
    self.jobs: dict[str, SpinJob] = {}
    self.requests: dict[str, SpinJob] = {}
    self.active_job: SpinJob | None = None
    self.moving = False
    self.initialized = False
    self.closing = False
    self.fault = ""
    self.lock = asyncio.Lock()
    self.commands: set[asyncio.Task] = set()
    self.status = device_pb.DeviceStatus(device_id=device_id, instance_id=uuid4().hex)
    self.updates: Latest[device_pb.DeviceStatus] = Latest()

  def publish_status(self) -> None:
    """Record the current operational state and its observation time."""
    if self.fault:
      self.status.state = device_pb.OPERATIONAL_STATE_FAULTED
    elif self.active_job is not None:
      self.status.state = (
        device_pb.OPERATIONAL_STATE_INITIALIZING
        if self.active_job.spec.spin is None
        else device_pb.OPERATIONAL_STATE_BUSY
      )
    elif self.moving:
      self.status.state = device_pb.OPERATIONAL_STATE_BUSY
    else:
      self.status.state = (
        device_pb.OPERATIONAL_STATE_READY
        if self.initialized
        else device_pb.OPERATIONAL_STATE_UNINITIALIZED
      )
    self.status.active_job_id = self.active_job.ref.job_id if self.active_job else ""
    self.status.fault_summary = self.fault
    self.status.observation_valid = True
    self.status.observed_at.GetCurrentTime()
    self.status.revision += 1
    self.updates.publish(self.status)

  def ensure_available(self) -> None:
    """Reject new work during shutdown or after a service fault."""
    if self.closing:
      raise StateError("service is stopping")
    if self.fault:
      raise StateError("service fault requires recovery: " + self.fault)

  async def run_storage_operation(self, function: Callable[..., T], *args: object) -> T:
    """Stop accepting work if a durable record cannot be written."""
    try:
      return await self.store.run_in_worker(function, *args)
    except StorageError as exc:
      self.fault = str(exc)
      if self.active_job is not None:
        self.active_job.stop_requested.set()
      self.publish_status()
      raise

  async def run_command(self, operation: Coroutine[object, object, T]) -> T:
    """Keep accepted motion and bookkeeping alive after an RPC disconnect."""
    if self.closing or len(self.commands) >= 32:
      operation.close()
      if self.closing:
        raise StateError("service is stopping")
      raise CapacityError("too many pending commands")
    task = asyncio.create_task(operation)
    self.commands.add(task)

    def finished(done: asyncio.Task) -> None:
      """Release command capacity and observe errors from disconnected callers."""
      self.commands.discard(done)
      if not done.cancelled():
        done.exception()

    task.add_done_callback(finished)
    return await asyncio.shield(task)

  async def open(self) -> None:
    """Acquire exclusive service ownership and recover jobs without opening hardware."""
    self.hardware_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.lock_file = self.hardware_lock.open("a+b")
    try:
      fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
      self.lock_file.close()
      self.lock_file = None
      raise BusyError("another VSpin service owns the hardware lock") from exc
    for spec, status in await self.store.open():
      job = SpinJob(self, spec, status)
      self.jobs[job.ref.job_id] = job
      self.requests[status.request_id] = job
    self.publish_status()

  async def create_job(
    self,
    request_id: str,
    spec: JobSpecification,
    configuration_id: str,
  ) -> SpinJob:
    """Persist inert settings, reusing a request ID only for identical arguments."""
    async with self.lock:
      self.ensure_available()
      if not 1 <= len(request_id) <= 128:
        raise ValueError("request_id must be 1..128 characters")
      if not configuration_id:
        raise ValueError("configuration_id is required")
      if request_id in self.requests:
        job = self.requests[request_id]
        if (job.spec, job.status.configuration_id) != (spec, configuration_id):
          raise ValueError("request_id already identifies different arguments")
        return job
      if configuration_id != self.configuration_id:
        raise StateError("requested configuration no longer matches the service")
      status = job_pb.JobStatus(
        job=job_pb.JobRef(device_id=self.device_id, job_id=uuid4().hex),
        request_id=request_id,
        kind=spec.kind,
        state=job_pb.JOB_STATE_READY,
        outcome=job_pb.OUTCOME_PENDING,
        phase="Ready",
        total=1,
        progress_unit="Steps" if spec.spin is None else "Spins",
        configuration_id=configuration_id,
      )
      status.created_at.GetCurrentTime()
      status = advance_job_status(status)
      await self.run_storage_operation(self.store.create_job, spec, status)
      job = SpinJob(self, spec, status)
      self.jobs[job.ref.job_id] = job
      self.requests[request_id] = job
      return job

  async def create_initialization(self, request_id: str, configuration_id: str) -> SpinJob:
    """Create an initialization job for explicit setup and homing."""
    return await self.run_command(self.create_job(request_id, JobSpecification(), configuration_id))

  async def create_spin(self, request_id: str, settings: Spin, configuration_id: str) -> SpinJob:
    """Create a spin job without moving the rotor."""
    return await self.run_command(
      self.create_job(request_id, JobSpecification(settings), configuration_id)
    )

  async def initialize(self) -> None:
    """Construct the driver and perform its setup sequence under job ownership."""
    self.driver = self.driver_factory()
    await self.driver.setup()
    self.initialized = True

  async def execute_spin(self, job: SpinJob) -> None:
    """Use the driver's controlled stop when cancellation is requested."""
    driver, settings = self.driver, job.spec.spin
    if driver is None or settings is None:
      raise StateError("spin requires initialized hardware and settings")
    spinning = asyncio.create_task(
      driver.spin(
        g=settings.g,
        duration=settings.duration,
        acceleration=settings.acceleration,
        deceleration=settings.deceleration,
      )
    )
    stopping = asyncio.create_task(job.stop_requested.wait())
    try:
      await asyncio.wait((spinning, stopping), return_when=asyncio.FIRST_COMPLETED)
      while not spinning.done():
        await driver.stop_spin(deceleration=settings.deceleration)
        # The driver's event callbacks can yield before the spin owns its command lock.
        await asyncio.wait((spinning,), timeout=0.05)
      await spinning
    finally:
      stopping.cancel()
      await asyncio.gather(stopping, return_exceptions=True)
      # A stop failure must not release ownership while the spin owner is still cleaning up.
      await asyncio.gather(spinning, return_exceptions=True)

  async def close_driver(self) -> None:
    """Close the transport after the operation owning it has finished."""
    self.initialized = False
    if self.driver is not None:
      await self.driver.stop()
      self.driver = None

  async def finish_operation(self, job: SpinJob, *, failed: bool) -> None:
    """Keep a successfully stopped spin ready, and close failed or cancelled setup sessions."""
    if failed:
      self.fault = self.fault or "Hardware operation failed; inspect the instrument before restart."
    if failed or (job.spec.spin is None and job.stop_requested.is_set()):
      try:
        await self.close_driver()
      except BaseException:
        self.fault = "Hardware cleanup failed; physical state requires inspection."
        raise

  async def _move(self, motion: Motion) -> None:
    """Reserve the instrument until a door, interlock, or bucket movement finishes."""
    async with self.lock:
      self.ensure_available()
      if self.active_job is not None or self.moving:
        raise BusyError("another operation owns the instrument")
      driver = self.driver
      if not self.initialized or driver is None:
        raise StateError("explicit initialization is required before motion")
      if motion in ("GoToBucket1", "GoToBucket2"):
        try:
          driver.bucket_1_remainder
        except RuntimeError as exc:
          raise StateError("bucket calibration is required before positioning") from exc
      operations: dict[Motion, Callable[[], Coroutine[object, object, None]]] = {
        "OpenDoor": driver.open_door,
        "CloseDoor": driver.close_door,
        "LockDoor": driver.lock_door,
        "UnlockDoor": driver.unlock_door,
        "LockBucket": driver.lock_bucket,
        "UnlockBucket": driver.unlock_bucket,
        "GoToBucket1": driver.go_to_bucket1,
        "GoToBucket2": driver.go_to_bucket2,
      }
      operation = operations[motion]
      self.moving = True
      self.publish_status()
    try:
      await operation()
    except BaseException:
      self.fault = "Hardware motion failed; inspect the instrument before restart."
      try:
        await self.close_driver()
      except BaseException:
        self.fault = "Hardware cleanup failed; physical state requires inspection."
        raise
      raise
    finally:
      async with self.lock:
        self.moving = False
        self.publish_status()

  async def open_door(self) -> None:
    """Open the door and wait for its sensor."""
    await self.run_command(self._move("OpenDoor"))

  async def close_door(self) -> None:
    """Close the door and wait for its sensor."""
    await self.run_command(self._move("CloseDoor"))

  async def lock_door(self) -> None:
    """Lock the door and wait for its sensor."""
    await self.run_command(self._move("LockDoor"))

  async def unlock_door(self) -> None:
    """Unlock the door after the driver verifies rotor access."""
    await self.run_command(self._move("UnlockDoor"))

  async def lock_bucket(self) -> None:
    """Lock the bucket and wait for its sensor."""
    await self.run_command(self._move("LockBucket"))

  async def unlock_bucket(self) -> None:
    """Unlock the bucket and wait for its sensor."""
    await self.run_command(self._move("UnlockBucket"))

  async def go_to_bucket1(self) -> None:
    """Present the calibrated first bucket at the loading position."""
    await self.run_command(self._move("GoToBucket1"))

  async def go_to_bucket2(self) -> None:
    """Present the calibrated second bucket at the loading position."""
    await self.run_command(self._move("GoToBucket2"))

  async def stop(self) -> None:
    """Finish accepted commands, decelerate active spins, and close owned resources."""
    self.closing = True
    try:
      while self.commands:
        await asyncio.gather(*list(self.commands), return_exceptions=True)
      if self.active_job is not None:
        try:
          await self.active_job.cancel()
        except StorageError:
          logger.exception("Shutdown cancellation could not be persisted")
      await asyncio.gather(*(job.task for job in self.jobs.values() if job.task is not None))
    finally:
      try:
        await run_owned_operation(self.close_driver())
      finally:
        try:
          await self.store.close()
        finally:
          if self.lock_file is not None:
            self.lock_file.close()
            self.lock_file = None

  def get_device_info(self) -> device_pb.DeviceInfo:
    """Describe instrument identity and the registered service contracts."""
    return device_pb.DeviceInfo(
      device_id=self.device_id,
      display_name="VSpin",
      device_type="VSpin",
      api_version=API_VERSION,
      configuration_id=self.configuration_id,
      supported_operations=self.supported_operations,
      services=[device.SERVICE_NAME, jobs.SERVICE_NAME, "vspin.v1.VSpinService"],
      manufacturer="Agilent",
      model="VSpin",
      implementation_version=__version__,
      instance_id=self.status.instance_id,
    )

  def get_configuration(self) -> pb.VSpinConfiguration:
    """Return spin limits in physical units."""
    return pb.VSpinConfiguration(
      configuration_id=self.configuration_id,
      minimum_relative_centrifugal_force=1,
      maximum_relative_centrifugal_force=1000,
      minimum_duration_seconds=1,
    )

  def get_status(self) -> device_pb.DeviceStatus:
    """Read the last operational snapshot without polling hardware."""
    return copy_message(self.status)

  async def watch_status(self) -> AsyncGenerator[device_pb.DeviceStatus, None]:
    """Yield current state followed by bounded updates."""
    with self.updates.subscribe() as queue:
      while True:
        yield await queue.get()

  def get_spin_settings(self, job_id: str) -> Spin:
    """Retrieve the exact settings retained for a spin job."""
    settings = self.jobs[job_id].spec.spin
    if settings is None:
      raise StateError("job is not a spin")
    return settings

  async def get_job(self, job_id: str) -> job_pb.JobStatus:
    """Read retained state without changing execution."""
    return copy_message(self.jobs[job_id].status)

  async def find_job(self, request_id: str) -> job_pb.JobRef:
    """Recover the original job for a request ID."""
    return copy_message(self.requests[request_id].ref)

  async def start_job(self, job_id: str) -> job_pb.JobStatus:
    """Accept execution independently of the caller's connection."""
    return await self.run_command(self.jobs[job_id].start())

  async def cancel_job(self, job_id: str) -> job_pb.JobStatus:
    """Request stopping while retaining ownership until cleanup finishes."""
    return await self.run_command(self.jobs[job_id].cancel())

  async def watch_job(self, job_id: str) -> AsyncGenerator[job_pb.JobStatus, None]:
    """Yield bounded job snapshots through terminal completion."""
    with self.jobs[job_id].updates.subscribe() as queue:
      while True:
        status = await queue.get()
        yield status
        if status.state == job_pb.JOB_STATE_FINISHED:
          return

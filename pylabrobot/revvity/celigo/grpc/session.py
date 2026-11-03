"""Own a Celigo driver session, durable jobs, and shared device/job/image operations."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import math
import re
import time
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, TypeVar
from uuid import uuid4

from pylabrobot.__version__ import __version__
from pylabrobot.revvity.celigo.celigo import Celigo
from pylabrobot.revvity.celigo.config import CeligoConfig
from pylabrobot.revvity.celigo.coordinates import CoordinateSystems
from pylabrobot.revvity.celigo.grpc.acquisition_job import AcquisitionJob, run_owned_operation
from pylabrobot.revvity.celigo.grpc.image_encoding import (
  build_public_image_frame,
  encode_frame,
  encode_image_geometry,
  encode_scan_frame,
)
from pylabrobot.revvity.celigo.grpc.job_specification import Brightfield, JobSpecification
from pylabrobot.revvity.celigo.grpc.job_store import (
  DEFAULT_QUOTA,
  JobStore,
  advance_job_status,
  build_public_job_status,
)
from pylabrobot.revvity.celigo.grpc.limits import (
  DEFAULT_SCAN_EXPOSURE_MS,
  DEFAULT_SCAN_GAIN,
  MAX_EXPOSURE_MS,
  MAX_FRAME_BYTES,
  MAX_GAIN,
  MAX_PIXELS,
)
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.revvity.celigo.scan import (
  AcquisitionTiming,
  FrameResult,
  PlannedFrame,
  ScanEstimateModel,
  ScanPlan,
  ScanSpec,
  build_scan_plan,
)
from pylabrobot.rpc import device, images, jobs
from pylabrobot.rpc.errors import BusyError, CapacityError, StateError, StorageError
from pylabrobot.rpc.transport import API_VERSION, Latest, copy_message
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

logger = logging.getLogger(__name__)
T = TypeVar("T")


class _ScanCancelled(Exception):
  """End the driver's scan at a frame boundary after cooperative cancellation."""


class CeligoSession:
  """Manage the driver and job lifecycle through ordinary Python methods."""

  device_type = "Celigo"
  display_name = "Celigo"
  supported_operations = ("Initialize", "BrightfieldCapture", "Scan", "OpenDoor", "CloseDoor")

  def __init__(
    self,
    directory: Path,
    device_id: str,
    config: CeligoConfig,
    driver_factory: Callable[[], Celigo],
    hardware_lock: Path,
    *,
    connection_id: str = "default",
    image_storage_bytes: int = DEFAULT_QUOTA,
  ):
    self.config = config
    self.driver_factory = driver_factory
    self.driver: Celigo | None = None
    self.hardware_lock = hardware_lock
    self.lock_file: BinaryIO | None = None
    serialized = json.dumps(
      {"calibration": asdict(config), "connection": connection_id},
      sort_keys=True,
      allow_nan=False,
      separators=(",", ":"),
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id):
      raise ValueError("invalid device ID")
    self.device_id = device_id
    self.configuration_id = hashlib.sha256(serialized.encode()).hexdigest()
    self.store = JobStore(
      directory,
      device_id,
      quota=image_storage_bytes,
      decode_spec=JobSpecification.decode,
      spec_format="celigo.brightfield.v1",
    )
    self.jobs: dict[str, AcquisitionJob] = {}
    self.requests: dict[str, AcquisitionJob] = {}
    self.active_job: AcquisitionJob | None = None
    self.door_moving = False
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
    width, height = config.calibration.image_width_pixels, config.calibration.image_height_pixels
    if not 0 < width * height <= MAX_PIXELS:
      raise ValueError("calibrated camera geometry exceeds the service limit")
    if config.hardware.z_axis is None or "brightfield" not in config.channels:
      raise ValueError("brightfield and a calibrated Z axis are required")
    door_inputs = [
      entry
      for entry in (config.hardware.io.digital_ios if config.hardware.io else [])
      if entry.io_name == "DoorInterlock" and entry.enabled
    ]
    if len(door_inputs) > 1:
      raise ValueError("multiple DoorInterlock inputs are configured")
    self.door_input = door_inputs[0] if door_inputs else None
    if self.door_input and (
      self.door_input.io_type.strip().lower() != "in" or not 0 <= self.door_input.bit_index < 12
    ):
      raise ValueError("DoorInterlock must be a digital input in 0..11")

  def get_configuration(self) -> pb.CeligoConfiguration:
    """Read capture limits and reference positions from the configured calibration."""
    z_axis = self.config.hardware.z_axis
    assert z_axis is not None
    return pb.CeligoConfiguration(
      configuration_id=self.configuration_id,
      width=self.config.calibration.image_width_pixels,
      height=self.config.calibration.image_height_pixels,
      channels=list(self.config.channels),
      calibrated_brightfield_z_mm=self.config.calibration.calibrated_z_position
      + self.config.channels["brightfield"].z_offset_to_brightfield_mm,
      minimum_z_mm=z_axis.min_position,
      maximum_z_mm=z_axis.max_position,
      maximum_exposure_ms=MAX_EXPOSURE_MS,
      maximum_gain=MAX_GAIN,
      default_scan_exposure_ms=DEFAULT_SCAN_EXPOSURE_MS,
      default_scan_gain=DEFAULT_SCAN_GAIN,
      autofocus_methods=[pb.AUTOFOCUS_NONE, pb.AUTOFOCUS_IMAGE],
      auto_exposure_supported=True,
      image_geometry_by_channel={
        channel: encode_image_geometry(self.config, channel) for channel in self.config.channels
      },
      capture_contract="Fixed-Z brightfield captures or inspected scan plans with optional auto-exposure and image autofocus.",
    )

  def build_job_specification(self, capture: Brightfield) -> JobSpecification:
    """Combine capture settings with the configured camera geometry."""
    return JobSpecification(
      capture,
      self.config.calibration.image_width_pixels,
      self.config.calibration.image_height_pixels,
    )

  def build_brightfield_plan(self, capture: Brightfield) -> ScanPlan:
    """Calculate stage and galvo targets for a single brightfield frame."""
    return build_scan_plan(
      self.config,
      ScanSpec.points(
        [(capture.sample_x_mm, capture.sample_y_mm)],
        channel="brightfield",
        exposure_ms=capture.exposure_ms,
        gain=capture.gain,
      ),
    )

  def build_scan_plan(
    self, spec: ScanSpec, estimate_model: ScanEstimateModel | None = None
  ) -> ScanPlan:
    """Resolve camera defaults and compile a complete scan without accessing hardware."""
    captures = tuple(
      replace(
        capture,
        exposure_ms=(
          DEFAULT_SCAN_EXPOSURE_MS if capture.exposure_ms is None else capture.exposure_ms
        ),
        gain=DEFAULT_SCAN_GAIN if capture.gain is None else capture.gain,
      )
      for capture in spec.captures
    )
    z_axis = self.config.hardware.z_axis
    assert z_axis is not None
    for capture in captures:
      assert capture.exposure_ms is not None and capture.gain is not None
      if not 0 < capture.exposure_ms <= MAX_EXPOSURE_MS or not 0 <= capture.gain <= MAX_GAIN:
        raise ValueError("exposure or gain exceeds the scan service limits")
      if capture.channel not in self.config.channels:
        raise ValueError(f"unknown imaging channel: {capture.channel}")
      z = (
        self.config.calibration.calibrated_z_position
        if spec.initial_brightfield_z_mm is None
        else spec.initial_brightfield_z_mm
      ) + self.config.channels[capture.channel].z_offset_to_brightfield_mm
      if not z_axis.min_position <= z <= z_axis.max_position:
        raise ValueError("initial channel focus is outside Z travel")
    return build_scan_plan(
      self.config, replace(spec, captures=captures), estimate_model=estimate_model
    )

  def validate_job_specification(self, spec: JobSpecification, initialize: bool) -> None:
    """Reject settings outside the configured capture contract and calibrated travel."""
    if initialize:
      if spec != JobSpecification():
        raise ValueError("initialization must not contain capture settings")
      return
    configuration = self.get_configuration()
    if (spec.width, spec.height) != (configuration.width, configuration.height):
      raise ValueError("image geometry no longer matches the configuration")
    if spec.scan_plan is not None:
      if spec.capture is not None:
        raise ValueError("a scan job cannot also contain a single capture")
      plan = spec.scan_plan
      if not plan.matches_configuration(self.config):
        raise StateError("scan plan configuration no longer matches the service")
      if self.build_scan_plan(plan.spec, plan.estimate_model) != plan:
        raise ValueError("scan plan differs from the compiled specification")
      return
    capture = spec.capture
    if capture is None:
      raise ValueError("capture settings are required")
    if any(not math.isfinite(value) for value in asdict(capture).values()):
      raise ValueError("capture settings must be finite")
    if not 0 < capture.exposure_ms <= MAX_EXPOSURE_MS or not 0 <= capture.gain <= MAX_GAIN:
      raise ValueError("exposure or gain exceeds the brightfield service limits")
    if not configuration.minimum_z_mm <= capture.z_mm <= configuration.maximum_z_mm:
      raise ValueError("Z is outside calibrated travel")
    self.build_brightfield_plan(
      capture
    )  # Offline validation of stage and galvo targets before persisting intent.

  def validate_job_start(self, job: AcquisitionJob) -> None:
    """Reject reinitialization while a driver session already exists."""
    if job.status.kind == "Initialize" and self.driver is not None:
      raise StateError("already initialized; restart the service before reinitializing")
    if job.spec.scan_plan is not None and not job.spec.scan_plan.matches_configuration(self.config):
      raise StateError("scan plan configuration no longer matches the service")

  def publish_status(self) -> None:
    """Refresh the device's operational snapshot and notify subscribers."""
    if self.fault:
      self.status.state = device_pb.OPERATIONAL_STATE_FAULTED
    elif self.active_job:
      self.status.state = (
        device_pb.OPERATIONAL_STATE_INITIALIZING
        if self.active_job.status.kind == "Initialize"
        else device_pb.OPERATIONAL_STATE_BUSY
      )
    elif self.door_moving:
      self.status.state = device_pb.OPERATIONAL_STATE_BUSY
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

  def ensure_available(self) -> None:
    if self.closing:
      raise StateError("service is stopping")
    if self.fault:
      raise StorageError("service fault requires recovery: " + self.fault)

  async def run_storage_operation(self, function: Callable[..., T], *args: object) -> T:
    """Run a storage operation and surface durable-write failures as device faults."""
    try:
      return await self.store.run_in_worker(function, *args)
    except StorageError as exc:
      self.fault = str(exc)
      if self.active_job:
        self.active_job.stop_requested.set()
      self.publish_status()
      raise

  async def run_command(self, operation: Coroutine[object, object, T]) -> T:
    """Keep accepted motion and durable bookkeeping alive if the caller disconnects."""
    if self.closing or len(self.commands) >= self.command_limit:
      operation.close()
      if self.closing:
        raise StateError("service is stopping")
      raise CapacityError("too many pending commands")
    task = asyncio.create_task(operation)
    self.commands.add(task)

    def finished(done):
      self.commands.discard(done)
      if not done.cancelled():
        done.exception()  # Observe failures even when the RPC disconnected.

    task.add_done_callback(finished)
    return await asyncio.shield(task)

  async def create_job(
    self,
    request_id: str,
    spec: JobSpecification,
    preview=False,
    configuration_id="",
    initialize=False,
  ) -> AcquisitionJob:
    """Persist an inert job, reusing its request ID only when all settings match."""
    async with self.lock:
      self.ensure_available()
      await asyncio.to_thread(self.validate_job_specification, spec, initialize)
      if not 1 <= len(request_id) <= 128:
        raise ValueError("request_id must be 1..128 characters")
      reservation = 0 if initialize else spec.calculate_storage_reservation()
      kind = (
        "Initialize"
        if initialize
        else "Preview"
        if preview
        else "Scan"
        if spec.scan_plan
        else "Acquisition"
      )
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
      status = advance_job_status(status)
      await self.run_storage_operation(self.store.create_job, spec, status, reservation)
      job = AcquisitionJob(self, spec, status)
      self.jobs[job.ref.job_id] = job
      self.requests[request_id] = job
      return job

  async def create_initialization(self, request_id: str, configuration_id: str) -> AcquisitionJob:
    """Persist initialization settings without opening or moving the instrument."""
    if not configuration_id:
      raise ValueError("configuration_id is required")
    return await self.run_command(
      self.create_job(
        request_id,
        JobSpecification(),
        configuration_id=configuration_id,
        initialize=True,
      )
    )

  async def create_brightfield_capture(
    self, request_id: str, settings: Brightfield, configuration_id: str
  ) -> AcquisitionJob:
    """Validate and persist a fixed-focus capture for an explicit job start."""
    if not configuration_id:
      raise ValueError("configuration_id is required")
    return await self.run_command(
      self.create_job(
        request_id,
        self.build_job_specification(settings),
        preview=True,
        configuration_id=configuration_id,
      )
    )

  async def create_scan(
    self, request_id: str, plan: ScanPlan, configuration_id: str
  ) -> AcquisitionJob:
    """Validate and persist an inspected plan for an explicit job start."""
    if not configuration_id:
      raise ValueError("configuration_id is required")
    return await self.run_command(
      self.create_job(
        request_id,
        JobSpecification(
          width=self.config.calibration.image_width_pixels,
          height=self.config.calibration.image_height_pixels,
          scan_plan=plan,
        ),
        configuration_id=configuration_id,
      )
    )

  def get_scan_plan(self, job_id: str) -> ScanPlan:
    """Read the exact persisted plan, including after a service restart."""
    plan = self.jobs[job_id].spec.scan_plan
    if plan is None:
      raise StateError("job does not contain a scan plan")
    return plan

  async def open(self) -> None:
    self.hardware_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.lock_file = self.hardware_lock.open("a+b")
    try:
      fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
      self.lock_file.close()
      self.lock_file = None
      raise BusyError("another Celigo service owns the hardware lock") from exc
    for spec, status in await self.store.open():
      job = AcquisitionJob(self, spec, status)
      self.jobs[job.ref.job_id] = job
      self.requests[status.request_id] = job
    self.previews.value = await self.run_storage_operation(self.store.get_latest_preview)
    self.publish_status()

  async def check_imaging_status(self) -> None:
    """Check controller health and a configured door input before acquisition."""
    driver = self.driver
    if driver is None:
      raise StateError("driver is not initialized")
    status = await driver.request_controller_status()
    logger.info(
      "Imaging controller flags=%#x extended_status=%s generic_interlock_open=%s",
      status.raw_flags,
      status.extended_status,
      status.interlock_open,
    )
    if status.has_controller_fault:
      raise StateError(
        f"controller fault: flags={status.raw_flags:#x}, extended_status={status.extended_status}"
      )
    if self.door_input is None:
      logger.info("Door state unknown: no DoorInterlock input configured")
      return
    raw = await driver.request_digital_input(self.door_input.bit_index)
    door_closed = raw != self.door_input.invert
    if not door_closed:
      raise StateError("configured door interlock is open")

  async def initialize(self) -> None:
    self.driver = self.driver_factory()
    await self.driver.setup()  # PLR opens USB, configures the camera, and homes the imaging axes.
    await self.check_imaging_status()

  async def capture_frame(self, job: AcquisitionJob, sequence: int) -> stored_pb.ImageFrame | None:
    """Acquire and encode one frame, checking cancellation between hardware operations."""
    if job.spec.capture is None:
      raise TypeError("capture requires explicit brightfield settings")
    settings = job.spec.capture
    driver = self.driver
    if driver is None:
      raise StateError("driver is not initialized")
    plan = self.build_brightfield_plan(settings)
    await self.check_imaging_status()
    if job.stop_requested.is_set():
      return None
    stage_move_started = time.monotonic()
    settled = await driver.move_to_scan_block(plan.blocks[0])
    stage_move_elapsed = timedelta(seconds=time.monotonic() - stage_move_started)
    await self.check_imaging_status()
    if job.stop_requested.is_set():
      return None
    acquisition_started_at = time.time()
    acquisition_started = time.monotonic()
    result = await driver.acquire_scan_position(
      position=plan.frames[0].position,
      label=job.ref.job_id,
      channel="brightfield",
      exposure_ms=settings.exposure_ms,
      gain=settings.gain,
      autofocus=None,
      z_mm=settings.z_mm,
      settled_stage_position_mm=settled,
      machine_auto_exposure=settings.machine_auto_exposure,
    )  # Public PLR API finishes illumination cleanup before returning.
    timing = AcquisitionTiming(
      started_at=acquisition_started_at,
      finished_at=time.time(),
      elapsed=timedelta(seconds=time.monotonic() - acquisition_started),
      stage_move_elapsed=stage_move_elapsed,
    )
    if (result.frame.width, result.frame.height) != (job.spec.width, job.spec.height):
      raise ValueError("camera returned dimensions different from calibration")
    return await asyncio.to_thread(
      encode_frame,
      job.ref,
      result,
      settings,
      self.configuration_id,
      encode_image_geometry(self.config, result.channel),
      timing,
    )

  async def execute_scan(self, job: AcquisitionJob) -> None:
    """Execute the stored plan, persisting each frame before allowing more motion."""
    plan = job.spec.scan_plan
    if plan is None or self.driver is None:
      raise StateError("scan execution requires a plan and initialized driver")

    async def check_before_frame(_frame: PlannedFrame) -> None:
      """Check cancellation and controller state before exposing the next frame."""
      if job.stop_requested.is_set():
        raise _ScanCancelled()
      await self.check_imaging_status()

    async def store_frame(result: FrameResult) -> None:
      """Commit pixels and progress together, then honor cooperative cancellation."""
      if (result.frame.width, result.frame.height) != (job.spec.width, job.spec.height):
        raise ValueError("camera returned dimensions different from calibration")
      frame = await asyncio.to_thread(
        encode_scan_frame,
        job.ref,
        result,
        self.configuration_id,
        encode_image_geometry(self.config, result.planned.capture.channel),
      )
      await job.commit_frame(frame)
      if job.stop_requested.is_set():
        raise _ScanCancelled()

    await self.check_imaging_status()
    if job.stop_requested.is_set():
      return
    try:
      await self.driver.execute(
        plan, on_frame=store_frame, retain_frames=False, before_frame=check_before_frame
      )
      if job.status.completed != plan.frame_count:
        raise StateError("scan execution finished before all planned frames were committed")
    except _ScanCancelled:
      pass

  async def close_driver(self) -> None:
    if self.driver is not None:
      await self.driver.stop()
      self.driver = None
    self.initialized = False

  async def _move_door(self, sample_position: tuple[float, float] | None) -> None:
    """Own drawer motion until completion; a missing position selects ejection."""
    async with self.lock:
      self.ensure_available()
      if self.active_job is not None or self.door_moving:
        raise BusyError("another operation owns the instrument")
      driver = self.driver
      if not self.initialized or driver is None:
        raise StateError("explicit initialization is required before drawer motion")
      self.door_moving = True
      self.publish_status()
    try:
      if sample_position is None:
        await driver.open_drawer()
      else:
        await driver.close_drawer_to_sample_mm(*sample_position)
    except BaseException:
      self.fault = "Drawer motion failed; restart the service before further motion."
      try:
        await self.close_driver()
      except BaseException:
        self.fault = "Hardware cleanup failed; physical state requires inspection."
        self.initialized = False
        raise
      raise
    finally:
      async with self.lock:
        self.door_moving = False
        self.publish_status()

  async def open_door(self) -> None:
    """Eject the drawer, retaining ownership if the caller disconnects."""
    await self.run_command(self._move_door(None))

  async def close_door(self, *, sample_x_mm: float, sample_y_mm: float) -> None:
    """Validate sample XY and load the drawer independently of the caller's lifetime."""
    if not all(math.isfinite(value) for value in (sample_x_mm, sample_y_mm)):
      raise ValueError("sample X/Y coordinates must be finite")
    coordinates = CoordinateSystems.from_config(
      self.config.calibration, self.config.hardware_defaults
    )
    x, y = coordinates.sample_mm_to_stage_mm(sample_x_mm, sample_y_mm)
    for axis, target in ((self.config.hardware.x_axis, x), (self.config.hardware.y_axis, y)):
      if axis is None:
        raise StateError("drawer motion requires configured X and Y axes")
      low, high = sorted((axis.min_position, axis.max_position))
      if not math.isfinite(target) or not low <= target <= high:
        raise ValueError("sample X/Y target is outside calibrated travel")
    await self.run_command(self._move_door((sample_x_mm, sample_y_mm)))

  async def finish_operation(self, job: AcquisitionJob, failed: bool) -> None:
    if failed or job.stop_requested.is_set():
      if failed and not self.fault:
        self.fault = "Hardware operation failed; restart the service before further motion."
      try:
        await self.close_driver()
      except BaseException:
        self.fault = "Hardware cleanup failed; physical state requires inspection."
        self.initialized = False
        raise

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
      try:
        await self.store.close()
      finally:
        try:
          await run_owned_operation(self.close_driver())
        finally:
          if self.lock_file is not None:
            self.lock_file.close()
            self.lock_file = None

  def get_device_info(self) -> device_pb.DeviceInfo:
    """Advertise actual registered services independently of image-specific limits."""
    return device_pb.DeviceInfo(
      device_id=self.device_id,
      display_name=self.display_name,
      device_type=self.device_type,
      api_version=API_VERSION,
      configuration_id=self.configuration_id,
      supported_operations=self.supported_operations,
      services=[
        device.SERVICE_NAME,
        jobs.SERVICE_NAME,
        images.SERVICE_NAME,
        "celigo.v1.CeligoService",
      ],
      manufacturer="Revvity",
      model="Celigo",
      implementation_version=__version__,
      instance_id=self.status.instance_id,
    )

  def get_status(self) -> device_pb.DeviceStatus:
    """Read the cached operational snapshot without polling or moving hardware."""
    return copy_message(self.status)

  async def watch_status(self) -> AsyncGenerator[device_pb.DeviceStatus, None]:
    """Publish current operational state followed by bounded updates."""
    with self.updates.subscribe() as queue:
      while True:
        yield await queue.get()

  async def get_job(self, job_id: str) -> job_pb.JobStatus:
    """Read a job through the image-independent public status type."""
    return build_public_job_status(self.jobs[job_id].status)

  async def find_job(self, request_id: str) -> job_pb.JobRef:
    """Recover an existing request without creating or starting work."""
    return copy_message(self.requests[request_id].ref)

  async def start_job(self, job_id: str) -> job_pb.JobStatus:
    """Let durable acceptance and executor bookkeeping finish if the RPC disconnects."""
    return build_public_job_status(await self.run_command(self.jobs[job_id].start()))

  async def cancel_job(self, job_id: str) -> job_pb.JobStatus:
    """Persist stopping intent while the executor retains hardware ownership."""
    return build_public_job_status(await self.run_command(self.jobs[job_id].cancel()))

  async def watch_job(self, job_id: str) -> AsyncGenerator[job_pb.JobStatus, None]:
    """Project bounded stored snapshots into the common job contract."""
    with self.jobs[job_id].updates.subscribe() as queue:
      while True:
        status = await queue.get()
        yield build_public_job_status(status)
        if status.state == job_pb.JOB_STATE_FINISHED:
          return

  def get_image_capabilities(self) -> image_pb.ImageCapabilities:
    """Keep pixel limits and retention capacity in the optional image capability."""
    return image_pb.ImageCapabilities(
      maximum_frame_bytes=MAX_FRAME_BYTES,
      maximum_pixels=MAX_PIXELS,
      retention_bytes=self.store.quota,
      pixel_formats=["Mono8", "Mono16"],
      encodings=["PNG"],
    )

  async def get_image_retention(self, job_id: str) -> image_pb.ImageRetention:
    """Read the durable image-release flag without adding it to common job status."""
    status = self.jobs[job_id].status
    return image_pb.ImageRetention(job=status.job, images_released=status.images_released)

  async def release_images(self, job_id: str) -> image_pb.ImageRetention:
    """Commit explicit image release independently of the initiating RPC lifetime."""
    status = await self.run_command(self.jobs[job_id].release_images())
    return image_pb.ImageRetention(job=status.job, images_released=status.images_released)

  async def list_frames(self, job_id: str, after_sequence: int, limit: int) -> image_pb.FramePage:
    """Read one metadata page from the existing durable store."""
    return await self.jobs[job_id].list_frames(after_sequence, limit)

  async def get_frame(self, job_id: str, frame_id: str) -> image_pb.ImageFrame:
    """Return a shared image envelope after validating the retained pixels."""
    return build_public_image_frame(await self.jobs[job_id].get_frame(frame_id))

  async def watch_previews(self) -> AsyncGenerator[image_pb.ImageFrame, None]:
    """Read complete retained previews; subscribing never starts acquisition."""
    with self.previews.subscribe() as queue:
      while True:
        ref = await queue.get()
        try:
          yield await self.get_frame(ref.job.job_id, ref.frame_id)
        except StateError:
          continue  # Explicit image release can race with a queued preview reference.

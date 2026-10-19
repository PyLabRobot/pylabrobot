"""PLR adapter for explicit initialization and one fixed-focus brightfield capture."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import io
import json
import logging
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from PIL import Image

from pylabrobot.revvity.celigo.celigo import (
  AcquisitionResult,
  AutofocusMethod,
  ControllerStatus,
  IlluminationChannelName,
)
from pylabrobot.revvity.celigo.config import CeligoConfig
from pylabrobot.revvity.celigo.grpc.execution import (
  AcquisitionCore,
  AcquisitionJob,
  owned_operation,
)
from pylabrobot.revvity.celigo.grpc.model import MAX_FRAME_BYTES, MAX_PIXELS, Specification
from pylabrobot.revvity.celigo.grpc.server import ImagingServer, unary
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.revvity.celigo.scan import ScanBlock, ScanPosition, ScanSpec, build_scan_plan
from pylabrobot.rpc.errors import BusyError, StateError
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

MAX_EXPOSURE_MS = 1000.0
MAX_GAIN = 16.0
logger = logging.getLogger(__name__)


class Driver(Protocol):
  async def setup(self) -> None: ...
  async def stop(self) -> None: ...
  async def request_controller_status(self) -> ControllerStatus: ...
  async def request_digital_input(self, bit_index: int) -> bool: ...
  async def move_to_scan_block(self, block: ScanBlock) -> tuple[float, float]: ...
  async def acquire_scan_position(
    self,
    position: ScanPosition,
    label: str,
    channel: IlluminationChannelName,
    exposure_ms: float | None,
    gain: float | None,
    autofocus: AutofocusMethod | None,
    z_mm: float | None,
    settled_stage_position_mm: tuple[float, float],
    machine_auto_exposure: bool = False,
  ) -> AcquisitionResult: ...


@dataclass(frozen=True)
class Brightfield:
  sample_x_mm: float
  sample_y_mm: float
  z_mm: float
  exposure_ms: float
  gain: float

  def to_proto(self) -> pb.BrightfieldSettings:
    return pb.BrightfieldSettings(**asdict(self))

  @classmethod
  def from_proto(cls, value: pb.BrightfieldSettings) -> Brightfield:
    for name in ("sample_x_mm", "sample_y_mm", "z_mm", "exposure_ms", "gain"):
      if not value.HasField(name):
        raise ValueError(f"{name} is required")
    return cls(value.sample_x_mm, value.sample_y_mm, value.z_mm, value.exposure_ms, value.gain)


@dataclass(frozen=True)
class HardwareSpecification:
  capture: Brightfield | None = None
  width: int = 0
  height: int = 0

  @property
  def frame_count(self) -> int:
    return 1

  @property
  def interval_ms(self) -> int:
    return 0

  def reservation(self) -> int:
    raw = self.width * self.height * 2
    return raw + raw // 50 + 65536

  def to_proto(self) -> pb.CeligoJobSpecification:
    value = pb.CeligoJobSpecification(width=self.width, height=self.height)
    if self.capture is not None:
      value.capture.CopyFrom(self.capture.to_proto())
    return value

  @classmethod
  def decode(cls, data: bytes) -> HardwareSpecification:
    value = pb.CeligoJobSpecification.FromString(data)
    capture = Brightfield.from_proto(value.capture) if value.HasField("capture") else None
    return cls(capture, value.width, value.height)


def encode_frame(
  job: job_pb.JobRef,
  result: AcquisitionResult,
  settings: Brightfield,
  configuration_id: str,
) -> stored_pb.ImageFrame:
  """Preserve camera pixels, actual settings, native time, and both coordinate systems."""
  frame = result.frame
  if frame.bit_depth not in (8, 16) or not 0 < frame.pixel_count <= MAX_PIXELS:
    raise ValueError("unsupported camera pixel format or dimensions")
  if len(frame.data) != frame.pixel_count * (frame.bit_depth // 8):
    raise ValueError("camera payload length disagrees with its dimensions")
  output = io.BytesIO()
  Image.fromarray(frame.to_numpy()).save(output, format="PNG", compress_level=1)
  payload = output.getvalue()
  if len(payload) > MAX_FRAME_BYTES:
    raise ValueError("encoded camera frame exceeds message limit")
  image = stored_pb.ImageFrame(
    job=job,
    frame_id=f"{job.job_id}-1",
    sequence=1,
    representation="Preview",
    width=frame.width,
    height=frame.height,
    pixel_format=f"Mono{frame.bit_depth}",
    encoding="PNG",
    image=payload,
    sha256=hashlib.sha256(payload).hexdigest(),
    metadata=pb.AcquisitionMetadata(
      channel=result.channel,
      exposure_ms=frame.exposure_ms,
      gain=frame.gain,
      sample_x_mm=settings.sample_x_mm,
      sample_y_mm=settings.sample_y_mm,
      stage_x_mm=result.x_mm,
      stage_y_mm=result.y_mm,
      z_mm=result.z_mm,
      focus_method="FixedZ",
      configuration_id=configuration_id,
      galvo_x_volts=result.galvo_hardware_voltages[0],
      galvo_y_volts=result.galvo_hardware_voltages[1],
    ),
  )
  seconds = math.floor(frame.captured_at)
  image.captured_at.FromNanoseconds(
    seconds * 1_000_000_000 + round((frame.captured_at - seconds) * 1_000_000_000)
  )
  return image


class CeligoCore(AcquisitionCore):
  """Reuse durable execution while keeping all driver access behind explicit jobs."""

  device_type = "Celigo"
  display_name = "Celigo"
  supported_operations = ("Initialize", "BrightfieldCapture")

  def __init__(
    self,
    directory: Path,
    device_id: str,
    config: CeligoConfig,
    driver_factory: Callable[[], Driver],
    hardware_lock: Path,
    *,
    connection_id: str = "default",
  ):
    self.config = config
    self.driver_factory = driver_factory
    self.driver: Driver | None = None
    self.hardware_lock = hardware_lock
    self.lock_file: BinaryIO | None = None
    serialized = json.dumps(
      {"calibration": asdict(config), "connection": connection_id},
      sort_keys=True,
      allow_nan=False,
      separators=(",", ":"),
    )
    configuration_id = hashlib.sha256(serialized.encode()).hexdigest()
    super().__init__(
      directory,
      device_id,
      configuration_id=configuration_id,
      decode_spec=HardwareSpecification.decode,
      spec_format="celigo.brightfield.v1",
    )
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

  def configuration(self) -> pb.CeligoConfiguration:
    z_axis = self.config.hardware.z_axis
    assert z_axis is not None
    return pb.CeligoConfiguration(
      configuration_id=self.configuration_id,
      width=self.config.calibration.image_width_pixels,
      height=self.config.calibration.image_height_pixels,
      channels=["brightfield"],
      calibrated_brightfield_z_mm=self.config.calibration.calibrated_z_position
      + self.config.channels["brightfield"].z_offset_to_brightfield_mm,
      minimum_z_mm=z_axis.min_position,
      maximum_z_mm=z_axis.max_position,
      maximum_exposure_ms=MAX_EXPOSURE_MS,
      maximum_gain=MAX_GAIN,
      capture_contract="One brightfield frame at explicit sample XY and fixed Z; no autofocus or auto-exposure.",
    )

  def specification(self, capture: Brightfield) -> HardwareSpecification:
    return HardwareSpecification(
      capture,
      self.config.calibration.image_width_pixels,
      self.config.calibration.image_height_pixels,
    )

  def plan(self, capture: Brightfield):
    return build_scan_plan(
      self.config,
      ScanSpec.points(
        [(capture.sample_x_mm, capture.sample_y_mm)],
        channel="brightfield",
        exposure_ms=capture.exposure_ms,
        gain=capture.gain,
      ),
    )

  def validate_spec(self, spec: Specification, initialize: bool) -> None:
    if not isinstance(spec, HardwareSpecification):
      raise TypeError("physical service requires a hardware specification")
    if initialize:
      if spec != HardwareSpecification():
        raise ValueError("initialization must not contain capture settings")
      return
    capture = spec.capture
    if capture is None:
      raise ValueError("capture settings are required")
    if any(not math.isfinite(value) for value in asdict(capture).values()):
      raise ValueError("capture settings must be finite")
    if not 0 < capture.exposure_ms <= MAX_EXPOSURE_MS or not 0 <= capture.gain <= MAX_GAIN:
      raise ValueError("exposure or gain exceeds the brightfield service limits")
    configuration = self.configuration()
    if not configuration.minimum_z_mm <= capture.z_mm <= configuration.maximum_z_mm:
      raise ValueError("Z is outside calibrated travel")
    if (spec.width, spec.height) != (configuration.width, configuration.height):
      raise ValueError("image geometry no longer matches the configuration")
    self.plan(capture)  # Offline validation of stage and galvo targets before persisting intent.

  def validate_start(self, job: AcquisitionJob) -> None:
    if job.status.kind == "Initialize" and self.driver is not None:
      raise StateError("already initialized; restart the service before reinitializing")

  async def open(self) -> None:
    self.hardware_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.lock_file = self.hardware_lock.open("a+b")
    try:
      fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
      self.lock_file.close()
      self.lock_file = None
      raise BusyError("another Celigo service owns the hardware lock") from exc
    await super().open()

  async def check_imaging_status(self) -> None:
    """Check controller health and a configured door input before brightfield work."""
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

  async def capture(self, job: AcquisitionJob, sequence: int) -> stored_pb.ImageFrame | None:
    if not isinstance(job.spec, HardwareSpecification) or job.spec.capture is None:
      raise TypeError("capture requires explicit brightfield settings")
    settings = job.spec.capture
    driver = self.driver
    if driver is None:
      raise StateError("driver is not initialized")
    plan = self.plan(settings)
    await self.check_imaging_status()
    if job.stop_requested.is_set():
      return None
    settled = await driver.move_to_scan_block(plan.blocks[0])
    await self.check_imaging_status()
    if job.stop_requested.is_set():
      return None
    result = await driver.acquire_scan_position(
      position=plan.frames[0].position,
      label=job.ref.job_id,
      channel="brightfield",
      exposure_ms=settings.exposure_ms,
      gain=settings.gain,
      autofocus=None,
      z_mm=settings.z_mm,
      settled_stage_position_mm=settled,
      machine_auto_exposure=False,
    )  # Public PLR API finishes illumination cleanup before returning.
    if (result.frame.width, result.frame.height) != (job.spec.width, job.spec.height):
      raise ValueError("camera returned dimensions different from calibration")
    return await asyncio.to_thread(encode_frame, job.ref, result, settings, self.configuration_id)

  async def close_driver(self) -> None:
    if self.driver is not None:
      await self.driver.stop()
      self.driver = None
    self.initialized = False

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
    try:
      await super().stop()
    finally:
      try:
        await owned_operation(self.close_driver())
      finally:
        if self.lock_file is not None:
          self.lock_file.close()
          self.lock_file = None


class CeligoOperations(rpc.CeligoServiceServicer):
  def __init__(self, core: CeligoCore):
    self.core = core

  def device(self, device_id: str) -> CeligoCore:
    if device_id != self.core.device_id:
      raise KeyError(device_id)
    return self.core

  @unary
  async def GetConfiguration(self, request, context):
    return self.device(request.device_id).configuration()

  @unary
  async def CreateInitialization(self, request, context):
    core = self.device(request.device_id)
    if not request.configuration_id:
      raise ValueError("configuration_id is required")
    job = await core.command(
      core.create(
        request.request_id,
        HardwareSpecification(),
        configuration_id=request.configuration_id,
        initialize=True,
      )
    )
    return job.ref

  @unary
  async def CreateBrightfieldCapture(self, request, context):
    core = self.device(request.device_id)
    if not request.configuration_id or not request.HasField("settings"):
      raise ValueError("configuration_id and settings are required")
    spec = core.specification(Brightfield.from_proto(request.settings))
    job = await core.command(
      core.create(
        request.request_id,
        spec,
        preview=True,
        configuration_id=request.configuration_id,
      )
    )
    return job.ref


class CeligoServer(ImagingServer):
  def __init__(self, core: CeligoCore, *, port=0):
    super().__init__(core=core, port=port)
    rpc.add_CeligoServiceServicer_to_server(CeligoOperations(core), self.server)

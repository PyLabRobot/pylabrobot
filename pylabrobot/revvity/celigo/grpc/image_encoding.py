"""Encode captured Celigo pixels and metadata into retained and public image messages."""

import hashlib
import io
import math

from PIL import Image

from pylabrobot.revvity.celigo.camera import CameraFrame
from pylabrobot.revvity.celigo.celigo import AcquisitionResult
from pylabrobot.revvity.celigo.grpc.job_specification import Brightfield
from pylabrobot.revvity.celigo.grpc.limits import MAX_FRAME_BYTES, MAX_PIXELS
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.revvity.celigo.scan import FrameResult
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


def build_public_image_frame(frame: stored_pb.ImageFrame) -> image_pb.ImageFrame:
  """Wrap complete pixels and explicitly typed Celigo metadata in a shared envelope."""
  result = image_pb.ImageFrame(
    job=frame.job,
    frame_id=frame.frame_id,
    sequence=frame.sequence,
    representation=frame.representation,
    captured_at=frame.captured_at,
    width=frame.width,
    height=frame.height,
    pixel_format=frame.pixel_format,
    encoding=frame.encoding,
    image=frame.image,
    sha256=frame.sha256,
  )
  result.metadata.Pack(frame.metadata)
  return result


def _encode_image(
  job: job_pb.JobRef,
  frame: CameraFrame,
  metadata: pb.AcquisitionMetadata,
  sequence: int,
  representation: str,
) -> stored_pb.ImageFrame:
  """Encode one complete image, preserving its capture time and supplied metadata."""
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
    frame_id=f"{job.job_id}-{sequence}",
    sequence=sequence,
    representation=representation,
    width=frame.width,
    height=frame.height,
    pixel_format=f"Mono{frame.bit_depth}",
    encoding="PNG",
    image=payload,
    sha256=hashlib.sha256(payload).hexdigest(),
    metadata=metadata,
  )
  seconds = math.floor(frame.captured_at)
  image.captured_at.FromNanoseconds(
    seconds * 1_000_000_000 + round((frame.captured_at - seconds) * 1_000_000_000)
  )
  return image


def encode_frame(
  job: job_pb.JobRef,
  result: AcquisitionResult,
  settings: Brightfield,
  configuration_id: str,
) -> stored_pb.ImageFrame:
  """Encode one fixed-focus brightfield capture and its acquisition settings."""
  return _encode_image(
    job,
    result.frame,
    pb.AcquisitionMetadata(
      channel=result.channel,
      exposure_ms=result.frame.exposure_ms,
      gain=result.frame.gain,
      sample_x_mm=settings.sample_x_mm,
      sample_y_mm=settings.sample_y_mm,
      stage_x_mm=result.x_mm,
      stage_y_mm=result.y_mm,
      z_mm=result.z_mm,
      focus_method="FixedZ",
      machine_auto_exposure=settings.machine_auto_exposure,
      configuration_id=configuration_id,
      galvo_x_volts=result.galvo_hardware_voltages[0],
      galvo_y_volts=result.galvo_hardware_voltages[1],
    ),
    sequence=1,
    representation="Preview",
  )


def encode_scan_frame(
  job: job_pb.JobRef, result: FrameResult, configuration_id: str
) -> stored_pb.ImageFrame:
  """Link a captured image to its exact planned frame, block, tile, and channel."""
  planned = result.planned
  metadata = pb.AcquisitionMetadata(
    channel=planned.capture.channel,
    exposure_ms=result.frame.exposure_ms,
    gain=result.frame.gain,
    sample_x_mm=planned.position.sample_x_mm,
    sample_y_mm=planned.position.sample_y_mm,
    stage_x_mm=result.actual_stage_mm[0],
    stage_y_mm=result.actual_stage_mm[1],
    z_mm=result.actual_z_mm,
    focus_method="Image" if result.focus is not None else "FixedZ",
    configuration_id=configuration_id,
    galvo_x_volts=result.galvo_hardware_voltages[0],
    galvo_y_volts=result.galvo_hardware_voltages[1],
    planned_frame_index=planned.index,
    block_index=planned.block.index,
    tile_row=planned.position.tile_row,
    tile_column=planned.position.tile_column,
    label=planned.block.label,
    focus_score=result.focus.score if result.focus is not None else None,
    machine_auto_exposure=planned.capture.machine_auto_exposure,
  )
  return _encode_image(
    job, result.frame, metadata, sequence=planned.index + 1, representation="Scan"
  )

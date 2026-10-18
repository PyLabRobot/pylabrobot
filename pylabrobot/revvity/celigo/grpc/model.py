"""Simulation inputs and deterministic images shared with the OPC UA workload."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from google.protobuf.message import Message
from PIL import Image

from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import storage_pb2 as stored_pb
from pylabrobot.rpc.transport import channel_options
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb

MAX_PIXELS = 4096 * 2048
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_MESSAGE_BYTES = 40 * 1024 * 1024
CHANNEL_OPTIONS = channel_options(MAX_MESSAGE_BYTES)


class Specification(Protocol):
  @property
  def frame_count(self) -> int: ...

  @property
  def interval_ms(self) -> int: ...

  def reservation(self) -> int: ...

  def to_proto(self) -> Message: ...


@dataclass(frozen=True)
class SimulationSpec:
  frame_count: int = 3
  width: int = 2048
  height: int = 2048
  bit_depth: int = 16
  interval_ms: int = 100

  def to_proto(self) -> pb.SimulationSpec:
    return pb.SimulationSpec(
      frame_count=self.frame_count,
      width=self.width,
      height=self.height,
      bit_depth=self.bit_depth,
      interval_ms=self.interval_ms,
    )

  @classmethod
  def from_proto(cls, value: pb.SimulationSpec) -> SimulationSpec:
    return cls(value.frame_count, value.width, value.height, value.bit_depth, value.interval_ms)

  @classmethod
  def decode(cls, value: bytes) -> SimulationSpec:
    return cls.from_proto(pb.SimulationSpec.FromString(value))

  def reservation(self) -> int:
    if not 1 <= self.frame_count <= 64 or not 1 <= self.width <= 4096:
      raise ValueError("invalid frame count or width")
    if not 1 <= self.height <= 4096 or self.width * self.height > MAX_PIXELS:
      raise ValueError("image exceeds the prototype pixel limit")
    if self.bit_depth not in (8, 16) or not 0 <= self.interval_ms <= 10000:
      raise ValueError("invalid bit depth or interval")
    raw_bytes = self.width * self.height * (self.bit_depth // 8)
    return self.frame_count * (raw_bytes + raw_bytes // 50 + 65536)


def make_frame(
  job: job_pb.JobRef, sequence: int, spec: SimulationSpec, preview: bool
) -> stored_pb.ImageFrame:
  """Match the OPC UA prototype's pixels and PNG settings, without importing it."""
  dtype = np.uint16 if spec.bit_depth == 16 else np.uint8
  pixels = np.random.default_rng(sequence).integers(
    0, 2**spec.bit_depth, size=(spec.height, spec.width), dtype=dtype
  )
  output = io.BytesIO()
  Image.fromarray(pixels).save(output, format="PNG", compress_level=1)
  payload = output.getvalue()
  if len(payload) > MAX_FRAME_BYTES:
    raise ValueError("encoded frame exceeds maximum_frame_bytes")
  frame = stored_pb.ImageFrame(
    job=job,
    frame_id=f"{job.job_id}-{sequence}",
    sequence=sequence,
    representation="Preview" if preview else "FullResolution",
    width=spec.width,
    height=spec.height,
    pixel_format=f"Mono{spec.bit_depth}",
    encoding="PNG",
    image=payload,
    sha256=hashlib.sha256(payload).hexdigest(),
    metadata=pb.AcquisitionMetadata(
      channel="brightfield",
      exposure_ms=1,
      gain=1,
      sample_x_mm=float(sequence),
      focus_method="Simulated",
    ),
  )
  frame.captured_at.GetCurrentTime()
  return frame

"""Concrete Celigo job settings and their durable protobuf representation."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb


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
class JobSpecification:
  capture: Brightfield | None = None
  width: int = 0
  height: int = 0

  @property
  def frame_count(self) -> int:
    return 1

  @property
  def interval_ms(self) -> int:
    return 0

  def calculate_storage_reservation(self) -> int:
    """Calculate the byte reservation for pixels and encoding overhead."""
    raw = self.width * self.height * 2
    return raw + raw // 50 + 65536

  def to_proto(self) -> pb.CeligoJobSpecification:
    value = pb.CeligoJobSpecification(width=self.width, height=self.height)
    if self.capture is not None:
      value.capture.CopyFrom(self.capture.to_proto())
    return value

  @classmethod
  def decode(cls, data: bytes) -> JobSpecification:
    value = pb.CeligoJobSpecification.FromString(data)
    capture = Brightfield.from_proto(value.capture) if value.HasField("capture") else None
    return cls(capture, value.width, value.height)

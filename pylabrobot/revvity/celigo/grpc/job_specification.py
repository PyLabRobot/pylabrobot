"""Concrete Celigo job settings and their durable protobuf representation."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pylabrobot.revvity.celigo.grpc.scan_serialization import decode_scan_plan, encode_scan_plan
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.scan import ScanPlan


@dataclass(frozen=True)
class Brightfield:
  sample_x_mm: float
  sample_y_mm: float
  z_mm: float
  exposure_ms: float
  gain: float
  machine_auto_exposure: bool = False

  def to_proto(self) -> pb.BrightfieldSettings:
    return pb.BrightfieldSettings(**asdict(self))

  @classmethod
  def from_proto(cls, value: pb.BrightfieldSettings) -> Brightfield:
    for name in ("sample_x_mm", "sample_y_mm", "z_mm", "exposure_ms", "gain"):
      if not value.HasField(name):
        raise ValueError(f"{name} is required")
    return cls(
      value.sample_x_mm,
      value.sample_y_mm,
      value.z_mm,
      value.exposure_ms,
      value.gain,
      value.machine_auto_exposure,
    )


@dataclass(frozen=True)
class JobSpecification:
  capture: Brightfield | None = None
  width: int = 0
  height: int = 0
  scan_plan: ScanPlan | None = None

  @property
  def frame_count(self) -> int:
    return self.scan_plan.frame_count if self.scan_plan is not None else 1

  @property
  def interval_ms(self) -> int:
    return 0

  def calculate_storage_reservation(self) -> int:
    """Calculate the byte reservation for pixels and encoding overhead."""
    raw = self.width * self.height * 2
    return self.frame_count * (raw + raw // 50 + 65536)

  def to_proto(self) -> pb.CeligoJobSpecification:
    value = pb.CeligoJobSpecification(width=self.width, height=self.height)
    if self.capture is not None:
      value.capture.CopyFrom(self.capture.to_proto())
    if self.scan_plan is not None:
      value.scan_plan.CopyFrom(encode_scan_plan(self.scan_plan))
    return value

  @classmethod
  def decode(cls, data: bytes) -> JobSpecification:
    value = pb.CeligoJobSpecification.FromString(data)
    capture = Brightfield.from_proto(value.capture) if value.HasField("capture") else None
    plan = decode_scan_plan(value.scan_plan) if value.HasField("scan_plan") else None
    if capture is not None and plan is not None:
      raise ValueError("a job cannot contain both a brightfield capture and a scan plan")
    return cls(capture, value.width, value.height, plan)

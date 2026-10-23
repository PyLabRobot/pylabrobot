"""Validated spin settings and their durable representation."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from pylabrobot.agilent.vspin.grpc.v1 import service_pb2 as pb


@dataclass(frozen=True)
class Spin:
  """One spin using the local driver's g-force, seconds, and ramp fractions."""

  g: float = 500
  duration: float = 60
  acceleration: float = 0.8
  deceleration: float = 0.8

  def __post_init__(self) -> None:
    """Reject invalid values before recording intent or touching hardware."""
    if not all(math.isfinite(value) for value in asdict(self).values()):
      raise ValueError("spin settings must be finite")
    if not 1 <= self.g <= 1000:
      raise ValueError("g must be within 1..1000")
    if self.duration < 1:
      raise ValueError("duration must be at least 1 second")
    if not 0 < self.acceleration <= 1 or not 0 < self.deceleration <= 1:
      raise ValueError("acceleration and deceleration must be greater than 0 and at most 1")

  def to_proto(self) -> pb.SpinSettings:
    """Name physical units explicitly on the wire."""
    return pb.SpinSettings(
      relative_centrifugal_force=self.g,
      duration_seconds=self.duration,
      acceleration_fraction=self.acceleration,
      deceleration_fraction=self.deceleration,
    )

  @classmethod
  def from_proto(cls, value: pb.SpinSettings) -> Spin:
    """Require all settings so omitted wire fields cannot silently change a run."""
    if not all(
      value.HasField(field)
      for field in (
        "relative_centrifugal_force",
        "duration_seconds",
        "acceleration_fraction",
        "deceleration_fraction",
      )
    ):
      raise ValueError("all spin settings are required")
    return cls(
      value.relative_centrifugal_force,
      value.duration_seconds,
      value.acceleration_fraction,
      value.deceleration_fraction,
    )


@dataclass(frozen=True)
class JobSpecification:
  """A missing spin denotes initialization; settings are immutable after creation."""

  spin: Spin | None = None

  @property
  def kind(self) -> str:
    """Return the shared job kind for this specification."""
    return "Initialize" if self.spin is None else "Spin"

  def encode(self) -> bytes:
    """Serialize an explicit, versioned record without executable Python objects."""
    return json.dumps(
      {"version": 1, "spin": asdict(self.spin) if self.spin is not None else None},
      sort_keys=True,
      allow_nan=False,
    ).encode()

  @classmethod
  def decode(cls, value: bytes) -> JobSpecification:
    """Validate stored settings before making a retained job available."""
    data = json.loads(value)
    if data["version"] != 1:
      raise ValueError("unsupported VSpin job specification version")
    return cls(Spin(**data["spin"]) if data["spin"] is not None else None)

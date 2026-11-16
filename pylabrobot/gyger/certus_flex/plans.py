"""Explicit well dispense requests and prevalidated CERTUS execution plans."""

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Sequence

from pylabrobot.resources import Plate, Well

from .geometry import PlateRegistration, Position

Order = Literal["given", "row", "column", "snake_row", "snake_column"]


@dataclass(frozen=True)
class DispenseStep:
  """One well/channel request: either µL or seconds, never both.

  Timed requests are intended for commissioning and do not establish delivered
  volume. Zero volume skips a well; durations must be positive.
  """

  well: Well
  channel: int
  volume: Optional[float] = None
  duration: Optional[float] = None
  correction_factor: Optional[float] = None

  def __post_init__(self) -> None:
    """Reject ambiguous requests and nonfinite or negative quantities."""
    if (self.volume is None) == (self.duration is None):
      raise ValueError("Supply exactly one of volume or duration")
    value = self.volume if self.volume is not None else self.duration
    if value is None or isinstance(value, bool) or not math.isfinite(value) or value < 0:
      raise ValueError("Dispense quantity must be finite and nonnegative")
    if self.correction_factor is not None and (
      not math.isfinite(self.correction_factor) or not 0.5 <= self.correction_factor <= 1.5
    ):
      raise ValueError("correction_factor must be between 0.5 and 1.5")
    if self.duration is not None and self.correction_factor not in (None, 1):
      raise ValueError("A correction factor applies only to volume requests")

  @staticmethod
  def gradient(
    wells: Sequence[Well], *, channel: int, start: float, end: float
  ) -> tuple["DispenseStep", ...]:
    """Create a linear volume gradient in the supplied well order, including both endpoints."""
    if not wells or any(not math.isfinite(v) or v < 0 for v in (start, end)):
      raise ValueError("Gradient requires wells and finite, nonnegative endpoints")
    if len(wells) == 1 and start != end:
      raise ValueError("A one-well gradient must have equal endpoints")
    return tuple(
      DispenseStep(well, channel, volume=start + (end - start) * i / max(1, len(wells) - 1))
      for i, well in enumerate(wells)
    )

  @staticmethod
  def from_csv(path: str | Path, plate: Plate) -> tuple["DispenseStep", ...]:
    """Read columns well, channel (zero-based), volume (µL), and optional correction_factor."""
    with Path(path).open(newline="") as handle:
      reader = csv.DictReader(handle)
      if reader.fieldnames is None or not {"well", "channel", "volume"}.issubset(reader.fieldnames):
        raise ValueError("CSV requires well, channel, and volume columns")
      return tuple(
        DispenseStep(
          plate.get_item(row["well"]),
          int(row["channel"]),
          volume=float(row["volume"]),
          correction_factor=float(row["correction_factor"])
          if row.get("correction_factor")
          else None,
        )
        for row in reader
      )


@dataclass(frozen=True)
class PreparedDispense:
  """A resolved valve duration, target position, and optional calibration pressure."""

  step: DispenseStep
  position: Position
  duration: float
  pressure: Optional[float]


@dataclass(frozen=True)
class DispensePlan:
  """Prevalidated groups in execution order; zero-volume steps are omitted.

  Only consecutive compatible requests are grouped; requests are not silently
  reordered to improve parallelism. Each group uses distinct valve outputs at
  the same stage position, with one shared completion barrier.
  """

  registration: PlateRegistration
  groups: tuple[tuple[PreparedDispense, ...], ...]

  @property
  def count(self) -> int:
    """Number of physical valve doses, including repeats."""
    return sum(len(group) for group in self.groups)


def ordered_steps(
  steps: Sequence[DispenseStep], registration: PlateRegistration, order: Order
) -> list[DispenseStep]:
  """Apply stable row/column ordering using PLR well centers."""
  if order == "given":
    return list(steps)
  if order not in ("row", "column", "snake_row", "snake_column"):
    raise ValueError(f"Unknown dispense order {order!r}")
  centers = {
    id(step.well): step.well.get_location_wrt(registration.plate, x="c", y="c") for step in steps
  }
  rows = sorted({-p.y for p in centers.values()})
  cols = sorted({p.x for p in centers.values()})

  def key(step: DispenseStep) -> tuple[float, float]:
    """Order top-to-bottom rows and left-to-right columns, reversing alternate stripes."""
    p = centers[id(step.well)]
    if order in ("row", "snake_row"):
      return (-p.y, -p.x if order == "snake_row" and rows.index(-p.y) % 2 else p.x)
    return (p.x, p.y if order == "snake_column" and cols.index(p.x) % 2 else -p.y)

  return sorted(steps, key=key)

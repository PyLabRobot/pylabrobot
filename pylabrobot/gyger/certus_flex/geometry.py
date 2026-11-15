"""Plate registration and bounded controller coordinates for CERTUS FLEX."""

import math
from dataclasses import dataclass

from pylabrobot.resources import Plate, Well

Position = tuple[float, float, float]


@dataclass(frozen=True)
class MotionBounds:
  """Operator-verified travel envelope in machine mm; never inferred from plate size."""

  minimum: Position
  maximum: Position

  def __post_init__(self) -> None:
    """Require finite, ordered limits for all three axes."""
    if (
      len(self.minimum) != 3
      or len(self.maximum) != 3
      or any(
        not math.isfinite(lo) or not math.isfinite(hi) or lo >= hi
        for lo, hi in zip(self.minimum, self.maximum)
      )
    ):
      raise ValueError("Motion bounds require three finite, increasing axis limits")

  def check(self, position: Position) -> None:
    """Reject a point outside the configured envelope."""
    if len(position) != 3 or any(
      not math.isfinite(value) or not lo <= value <= hi
      for value, lo, hi in zip(position, self.minimum, self.maximum)
    ):
      raise ValueError(f"Position {position} is outside the verified motion bounds")


class PlateRegistration:
  """An unrotated PLR plate anchored to controller A1 and valve offsets.

  Pass this object to each plate operation. ``first_well`` is the controller's
  X/Y well origin before adding valve offsets; its Y is already signed. Height
  is the physical lidless plate height, not well depth. Bounds must describe a
  verified travel envelope; the controller interface does not expose verified
  software-limit paths. Registration does not change controller configuration.
  """

  def _signature(self) -> tuple:
    """Snapshot the plate geometry so editing or replacing wells invalidates registration."""
    rotation = self.plate.get_absolute_rotation()
    return (
      (rotation.x, rotation.y, rotation.z),
      self.plate.get_size_x(),
      self.plate.get_size_y(),
      self.plate.get_size_z(),
      (
        self.first_well,
        self.valve_offsets,
        self.height,
        self.clearance,
        self.bounds,
        self.serial_number,
        self.head,
      ),
      tuple(
        (
          id(well),
          tuple(well.get_location_wrt(self.plate, x="c", y="c")),
          well.get_size_x(),
          well.get_size_y(),
          well.max_volume,
        )
        for well in self.plate.get_all_items()
      ),
    )

  def __init__(
    self,
    plate: Plate,
    *,
    first_well: tuple[float, float],
    valve_offsets: tuple[Position, ...],
    height: float,
    clearance: float,
    bounds: MotionBounds,
    serial_number: str,
    head: str,
  ) -> None:
    """Validate geometry and retain a snapshot of the mounted plate."""
    if not plate.get_all_items() or len(first_well) != 2 or not valve_offsets:
      raise ValueError("Registration requires wells, an XY origin, and valve offsets")
    if any(len(offset) != 3 for offset in valve_offsets):
      raise ValueError("Each valve offset must have three coordinates")
    if any(
      not math.isfinite(v)
      for v in (*first_well, height, clearance, *(v for offset in valve_offsets for v in offset))
    ):
      raise ValueError("Registration coordinates must be finite")
    if height <= 0 or clearance < max(offset[2] + height for offset in valve_offsets):
      raise ValueError("Clearance must be above every dispensing height")
    self.plate = plate
    self.first_well = first_well
    self.valve_offsets = valve_offsets
    self.height = height
    self.clearance = clearance
    self.bounds = bounds
    self.serial_number = serial_number
    self.head = head
    self._registered_signature = self._signature()
    self.validate()

  def validate(self) -> None:
    """Reject a lid, rotation, or geometry changed since registration."""
    signature = self._signature()
    if signature != self._registered_signature or signature[0] != (0, 0, 0):
      raise ValueError("Plate geometry changed or is rotated; register the mounted plate again")
    if self.plate.lid is not None:
      raise ValueError("Remove the plate lid before dispensing")

  def target(self, well: Well, channel: int) -> Position:
    """Map a well center and zero-based channel to a bounded machine position."""
    self.validate()
    if (
      isinstance(channel, bool)
      or not isinstance(channel, int)
      or not 0 <= channel < len(self.valve_offsets)
    ):
      raise ValueError("Invalid channel index")
    if not any(well is item for item in self.plate.get_all_items()):
      raise ValueError("Well is not part of the registered plate")
    reference = self.plate.get_item("A1").get_location_wrt(self.plate, x="c", y="c")
    center = well.get_location_wrt(self.plate, x="c", y="c")
    if not (0 <= center.x <= self.plate.get_size_x() and 0 <= center.y <= self.plate.get_size_y()):
      raise ValueError("Well center is outside the plate footprint")
    offset = self.valve_offsets[channel]
    target = (
      self.first_well[0] + offset[0] + center.x - reference.x,
      self.first_well[1] + offset[1] + center.y - reference.y,
      offset[2] + self.height,
    )
    self.bounds.check(target)
    self.bounds.check((target[0], target[1], self.clearance))
    return target

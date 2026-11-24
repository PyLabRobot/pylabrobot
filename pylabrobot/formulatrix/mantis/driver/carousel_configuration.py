"""The LC3 carousel's geometry and motion settings from its OEM database."""

import contextlib
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .errors import MantisConfigurationError


@dataclass(frozen=True)
class CarouselConfiguration:
  """One LC3 and its fitted carousel; angles in degrees, speeds in degrees/s.

  Accelerations are degrees/s². ``steps_per_revolution`` is the conversion to the controller's
  native position units. ``slot_one_position`` is the calibrated angle for the first chip slot.
  """

  device_id: str
  capacity: int
  steps_per_revolution: float
  slot_one_position: float
  idle_position: float
  preposition: float
  homing_slow_speed: float
  homing_fast_speed: float
  homing_acceleration: float
  default_speed: float
  default_acceleration: float

  def __post_init__(self) -> None:
    """Reject incomplete geometry and non-finite or nonpositive motion rates."""
    if not self.device_id or type(self.capacity) is not int or self.capacity < 1:
      raise MantisConfigurationError("carousel needs a serial number and positive integer capacity")
    positive = (
      self.steps_per_revolution,
      self.homing_slow_speed,
      self.homing_fast_speed,
      self.homing_acceleration,
      self.default_speed,
      self.default_acceleration,
    )
    if any(not math.isfinite(value) or value <= 0 for value in positive):
      raise MantisConfigurationError(
        "carousel conversion and motion rates must be finite and positive"
      )
    if not all(math.isfinite(value) for value in (self.slot_one_position, self.idle_position)):
      raise MantisConfigurationError("carousel positions must be finite")
    if not math.isfinite(self.preposition) or not 0 <= self.preposition < 180:
      raise MantisConfigurationError("carousel preposition must be in [0, 180) degrees")
    if self.homing_slow_speed > self.homing_fast_speed:
      raise MantisConfigurationError("carousel slow homing speed exceeds fast homing speed")

  @classmethod
  def from_oem_database(cls, *, path: str, device_id: str) -> "CarouselConfiguration":
    """Read ``Data/System/data.db`` without modifying it, matching the LC3 serial and carousel.

    Uses the LC3's saved location and fitted carousel ID. Every geometry and motion setting must
    be present; another carousel's calibration and vendor defaults are not substituted.
    """
    try:
      uri = Path(path).resolve().as_uri() + "?mode=ro"
      with contextlib.closing(sqlite3.connect(uri, uri=True)) as database:
        rows = database.execute(
          "SELECT c.Capacity, t.LC3Origin, l.LastLocation "
          "FROM LC3 l JOIN Carousel c ON c.Id = l.LastCarouselId "
          "JOIN CarouselTuningInfo t ON t.LC3SerialNumber = l.SerialNumber "
          "AND t.CarouselId = c.Id AND t.LC3Location = l.LastLocation "
          "WHERE l.SerialNumber = ?",
          (device_id,),
        ).fetchall()
        if len(rows) != 1:
          raise MantisConfigurationError(
            f"{path}: expected one calibrated carousel for {device_id}, found {len(rows)}"
          )
        capacity, origin, location = rows[0]
        settings = dict(database.execute("SELECT Key, Value FROM Config WHERE Key LIKE 'LC3.%'"))
      steps = float(settings["LC3.StepsPerRev"])
      if not math.isfinite(steps) or steps <= 0:
        raise MantisConfigurationError("LC3.StepsPerRev must be finite and positive")
      degrees_per_step = 360.0 / steps
      return cls(
        device_id=device_id,
        capacity=capacity,
        steps_per_revolution=steps,
        slot_one_position=float(origin),
        idle_position=float(settings[f"LC3.{location}.DefaultRotation"]),
        preposition=float(settings["LC3.PrepositionDegree"]),
        homing_slow_speed=float(settings["LC3.HomingVelocity.Low"]) * degrees_per_step,
        homing_fast_speed=float(settings["LC3.HomingVelocity.High"]) * degrees_per_step,
        homing_acceleration=float(settings["LC3.HomingAcceleration"]) * degrees_per_step,
        default_speed=float(settings["LC3.RotateVelocity"]) * degrees_per_step,
        default_acceleration=float(settings["LC3.RotateAcceleration"]) * degrees_per_step,
      )
    except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
      raise MantisConfigurationError(f"{path}: invalid carousel configuration: {error}") from error

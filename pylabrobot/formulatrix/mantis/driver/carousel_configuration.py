"""The LC3 carousel's geometry and motion settings from its OEM database."""

import contextlib
import dataclasses
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .arm_kinematics import MantisArmCoordinate
from .errors import MantisConfigurationError


@dataclass(frozen=True)
class CarouselConfiguration:
  """One LC3 and its fitted carousel; angles in degrees, speeds in degrees/s.

  Accelerations are degrees/s². ``steps_per_revolution`` is the conversion to the controller's
  native position units. ``slot_one_position`` is the calibrated angle for the first chip slot.
  ``rfid_slot_one_position`` brings the first slot's chip over the LC3's RFID reader; None when it
  is not known, and then no tag is read. ``pick_up_path`` is the head's path from the chip
  changer route's end to the slot at the head and back out, measured on the unit, in the arm frame
  as ``approach_paths``; None when not measured, and then no chip is picked up there.
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
  rfid_slot_one_position: Optional[float] = None
  pick_up_path: Optional[List[MantisArmCoordinate]] = None

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
    if self.rfid_slot_one_position is not None and not math.isfinite(self.rfid_slot_one_position):
      raise MantisConfigurationError("carousel RFID position must be finite")
    if self.pick_up_path is not None and not self.pick_up_path:
      raise MantisConfigurationError("carousel pick-up path is empty")
    if not math.isfinite(self.preposition) or not 0 <= self.preposition < 180:
      raise MantisConfigurationError("carousel preposition must be in [0, 180) degrees")
    if self.homing_slow_speed > self.homing_fast_speed:
      raise MantisConfigurationError("carousel slow homing speed exceeds fast homing speed")

  @classmethod
  def from_oem_database(cls, *, path: str, device_id: str) -> "CarouselConfiguration":
    """Read ``Data/System/data.db`` without modifying it, matching the LC3 serial and carousel.

    Uses the LC3's saved location and fitted carousel ID. Every geometry and motion setting must
    be present; another carousel's calibration and vendor defaults are not substituted. The RFID
    reader's angle is the LC3's ``RfidCarousel<capacity>Offset``, None without that column.
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
        columns = {row[1] for row in database.execute("PRAGMA table_info(LC3)")}
        rfid_column = f"RfidCarousel{int(capacity)}Offset"
        rfid_position = None
        if rfid_column in columns:
          (rfid_position,) = database.execute(
            f"SELECT {rfid_column} FROM LC3 WHERE SerialNumber = ?", (device_id,)
          ).fetchone()
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
        rfid_slot_one_position=None if rfid_position is None else float(rfid_position),
      )
    except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
      raise MantisConfigurationError(f"{path}: invalid carousel configuration: {error}") from error

  @classmethod
  def from_block(cls, *, block: Dict[str, Any]) -> "CarouselConfiguration":
    """A declared ``left_carousel`` or ``right_carousel`` block, checked before anything is sent.

    Args:
      block: this class's fields, as ``dataclasses.asdict`` gives them; ``pick_up_path`` a list
        of [x, y, z], None to hold an axis.

    Raises:
      MantisConfigurationError: for a field missing, unknown or out of range.
    """
    try:
      fields = dict(block)
      names = {field.name for field in dataclasses.fields(cls)}
      unknown = set(fields) - names
      if unknown:
        raise ValueError(f"unknown fields {sorted(unknown)}")
      if any(isinstance(value, bool) for value in fields.values()):
        raise ValueError("a carousel value is not a boolean")
      if fields.get("pick_up_path") is not None:
        path = []
        for line in fields["pick_up_path"]:
          x, y, z = (None if value is None else float(value) for value in line)
          path.append(MantisArmCoordinate(x=x, y=y, z=z))
        fields["pick_up_path"] = path
      for name in names - {"device_id", "capacity", "pick_up_path"}:
        if fields.get(name) is not None:
          fields[name] = float(fields[name])
      return cls(**fields)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
      raise MantisConfigurationError(f"malformed carousel block: {error}") from error

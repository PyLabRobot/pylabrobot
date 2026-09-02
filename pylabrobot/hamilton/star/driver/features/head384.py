"""The 384-head: the block of 384 dispensing channels that works a whole plate at once."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from pylabrobot.hamilton.star.driver.features.head import Head, HeadConfiguration

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)


@dataclass
class Head384Configuration(HeadConfiguration):
  """Device facts for the installed 384-head.

  What this head adds to `HeadConfiguration` is what it reports about itself beyond the shared
  flags, and the head type that resolves what a dispensing or squeezer increment is worth: the
  three heads share a piston travel but not a bore, and are geared differently.

  Its drive windows do not move with firmware, so they are plain values rather than properties -
  what varies here is which head is fitted, not the generation.
  """

  module: str = "D0"
  retract_command: str = "JV"
  initialize_command: str = "JI"
  tip_presence_command: str = "QK"
  position_command: str = "QJ"
  y_parameter: str = "yk"
  z_parameter: str = "je"
  z_end_parameter: str = "zg"
  x_offset_parameter: str = "kd"
  head_types: Dict[int, str] = field(
    default_factory=lambda: {
      0: "Low volume head",
      1: "High volume head",
      2: "STP head",  # shifted tip pickup
    }
  )
  drive_parameters: Dict[str, int] = field(
    default_factory=lambda: {"yv": 5, "yr": 3, "zv": 5, "zr": 3}
  )
  # The generation the drive windows below were taken from. A head older than this documents
  # different ones, and nothing here resolves them per generation.
  first_documented_firmware_year: int = 2009

  supports_lld_absolute_threshold_check: Optional[bool] = None

  channel_pitch: float = 4.5
  channel_columns: int = 24
  channel_rows: int = 16

  dispensing_drive_mm_per_increment: float = 0.00063333

  # This head's Y and Z drives count acceleration in thousands of increments per second squared,
  # unlike the positions and speeds they count in single ones, so these are 1000x the position
  # resolutions.
  y_drive_acceleration_mm_per_increment: float = 15.625
  z_drive_acceleration_mm_per_increment: float = 5.0

  y_increment_range: Tuple[int, int] = (7100, 36100)  # type: ignore[assignment]
  y_speed_increment_range: Tuple[int, int] = (50, 20000)  # type: ignore[assignment]
  y_acceleration_increment_range: Tuple[int, int] = (5, 32)  # type: ignore[assignment]
  z_increment_range: Tuple[int, int] = (33200, 67200)  # type: ignore[assignment]
  z_acceleration_increment_range: Tuple[int, int] = (5, 100)

  # What this head's drives start from. Its accelerations are counted in thousands, so those two
  # are written small where the 96-head's are not.
  y_speed_increment_default: int = 20000
  y_acceleration_increment_default: int = 32
  z_acceleration_increment_default: int = 80

  predefined_y_position_origin: int = 22000
  predefined_z_position_origin: int = 35000

  y_drive_current_limit_default: int = 4
  z_drive_current_limit_default: int = 7
  current_limit_range: Tuple[int, int] = (0, 7)

  def _require_head_type(self) -> str:
    """The head type, for the facts only it decides.

    Returns:
      Which head is fitted.

    Raises:
      RuntimeError: If it has not been read, or is one this driver does not know.
    """
    if self.head_type is None or self.head_type == "unknown":
      raise RuntimeError(
        "the 384-head's type is not known, and it is what decides how much a dispensing or "
        "squeezer increment is worth; have you called `star.setup()`?"
      )
    return self.head_type

  @property
  def dispensing_drive_uL_per_increment(self) -> float:
    """What one increment of the dispensing drive holds, in uL.

    The three heads share a piston travel but not a bore, so this is the head type's to decide and
    is not known until the head has said which it is - guessing would mis-volume every aspirate.

    Raises:
      RuntimeError: If the head type has not been read.
    """
    head_type = self._require_head_type()
    if head_type == "Low volume head":
      return 0.000974941
    if head_type == "High volume head":
      return 0.00143754
    return 0.00186531

  @property
  def squeezer_drive_mm_per_increment(self) -> float:
    """How far one increment of the squeezer drive travels, in mm.

    Geared differently on the low volume head, so this is the head type's to decide as the
    dispensing volume above is.

    Raises:
      RuntimeError: If the head type has not been read.
    """
    return 0.00091813 if self._require_head_type() == "Low volume head" else 0.00035866

  # -- windows the dispensing and squeezer drives work in ----------------------------------------

  @property
  def dispensing_drive_range(self) -> Tuple[float, float]:
    """Aspirate/dispense piston volume window (uL); applies to both aspirate and dispense."""
    return (0.0, self.dispensing_drive_increments_to_uL(60950))

  @property
  def dispensing_drive_speed_range(self) -> Tuple[float, float]:
    """Dispensing-drive speed window (uL/s)."""
    # The drive counts its speed in tens of increments per second, so both ends are scaled.
    return (
      self.dispensing_drive_increments_to_uL(5 * 10),
      self.dispensing_drive_increments_to_uL(25000 * 10),
    )

  @property
  def dispensing_drive_speed_default(self) -> float:
    """Dispensing-drive default speed (uL/s)."""
    return self.dispensing_drive_increments_to_uL(50000)

  @property
  def dispensing_drive_acceleration_default(self) -> float:
    """Dispensing-drive default acceleration (uL/s2)."""
    return self.dispensing_drive_increments_to_uL(9000000)

  @property
  def squeezer_drive_speed_default(self) -> float:
    """Squeezer-drive default speed (mm/s); the low volume head runs slower."""
    increments = 16000 if self._require_head_type() == "Low volume head" else 40000
    return self.squeezer_drive_increments_to_mm(increments)

  @property
  def squeezer_drive_acceleration_default(self) -> float:
    """Squeezer-drive default acceleration (mm/s2); the low volume head runs gentler."""
    increments = 100000 if self._require_head_type() == "Low volume head" else 250000
    return self.squeezer_drive_increments_to_mm(increments)


class Head384(Head):
  """The 384-head.

  Reached as `driver.head384`, on a machine that has one. It is addressed as `D0`, but the
  commands that move it go to the master, so this capability speaks to both.
  """

  configuration: Head384Configuration

  def __init__(self, driver: "STARDriver", configuration: Optional[Head384Configuration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the head's device facts. Defaults to `Head384Configuration()`.
    """
    super().__init__(driver, configuration or Head384Configuration())

  # ----------------------------------------
  # Setup
  # ----------------------------------------

  # -- discovery ---------------------------------------------------------------------------------

  def _record_hardware(self, hardware: List[str]) -> None:
    """Record whether this head runs the absolute-threshold cLLD check.

    Index 1 was reserve until 2015, so a head older than that reads back 0 there whether or not it
    would do the check.

    Args:
      hardware: the tokens `request_hardware` read.
    """
    self.configuration.supports_lld_absolute_threshold_check = bool(int(hardware[1]))

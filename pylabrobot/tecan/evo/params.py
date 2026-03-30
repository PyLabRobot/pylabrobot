"""Tecan EVO backend-specific parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams


@dataclass(frozen=True)
class TecanPIPParams(BackendParams):
  """EVO-specific parameters for PIP operations.

  Attributes:
    liquid_detection_proc: Detection procedure for LLD.
        7 = double detection sequential (default).
    liquid_detection_sense: Conductivity setting for LLD.
        1 = high conductivity (default).
    tip_touch: If True, touch vessel wall after dispense to remove droplet.
    tip_touch_offset_y: Y offset for tip touch in mm.
  """

  liquid_detection_proc: Optional[int] = None
  liquid_detection_sense: Optional[int] = None
  tip_touch: bool = False
  tip_touch_offset_y: float = 1.0


@dataclass(frozen=True)
class TecanRoMaParams(BackendParams):
  """EVO-specific parameters for RoMa operations.

  Attributes:
    speed_x: X-axis fast speed in 1/10 mm/s.
    speed_y: Y-axis fast speed in 1/10 mm/s.
    speed_z: Z-axis fast speed in 1/10 mm/s.
    speed_r: R-axis fast speed in 1/10 deg/s.
    accel_y: Y-axis acceleration in 1/10 mm/s^2.
    accel_r: R-axis acceleration in 1/10 deg/s^2.
  """

  speed_x: Optional[int] = None
  speed_y: Optional[int] = None
  speed_z: Optional[int] = None
  speed_r: Optional[int] = None
  accel_y: Optional[int] = None
  accel_r: Optional[int] = None

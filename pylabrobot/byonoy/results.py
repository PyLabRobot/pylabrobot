from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class AbsorbanceResult:
  """Result of an absorbance measurement.

  Attributes:
    data: 2D array indexed [row][col]. ``None`` for unmeasured wells.
    wavelength: Wavelength in nm.
    temperature: Temperature in degrees C, or ``None`` if not available.
    timestamp: When the measurement was taken.
  """

  data: List[List[Optional[float]]]
  wavelength: int
  temperature: Optional[float]
  timestamp: datetime


@dataclass
class LuminescenceResult:
  """Result of a luminescence measurement.

  Attributes:
    data: 2D array indexed [row][col]. ``None`` for unmeasured wells.
    temperature: Temperature in degrees C, or ``None`` if not available.
    timestamp: When the measurement was taken.
  """

  data: List[List[Optional[float]]]
  temperature: Optional[float]
  timestamp: datetime

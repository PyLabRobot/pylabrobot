from __future__ import annotations

import dataclasses
from typing import List, Optional


@dataclasses.dataclass
class AbsorbanceResult:
  """Result of an absorbance measurement.

  Attributes:
    data: 2D array indexed [row][col]. ``None`` for unmeasured wells.
    wavelength: Wavelength in nm.
    temperature: Temperature in °C, or ``None`` if not available.
    timestamp: Unix timestamp of the measurement.
  """

  data: List[List[Optional[float]]]
  wavelength: int
  temperature: Optional[float]
  timestamp: float

from __future__ import annotations

import dataclasses
from typing import List, Optional


@dataclasses.dataclass
class LuminescenceResult:
  """Result of a luminescence measurement.

  Attributes:
    data: 2D array indexed [row][col]. ``None`` for unmeasured wells.
    temperature: Temperature in °C, or ``None`` if not available.
    timestamp: Unix timestamp of the measurement.
  """

  data: List[List[Optional[float]]]
  temperature: Optional[float]
  timestamp: float

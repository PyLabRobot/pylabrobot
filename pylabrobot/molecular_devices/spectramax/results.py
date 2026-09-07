from __future__ import annotations

import dataclasses
from typing import List, Optional


@dataclasses.dataclass
class AbsorbanceResult:
  """Result of an absorbance measurement.

  Attributes:
    data: 2D array indexed [row][col]. ``None`` for unmeasured wells.
    wavelength: Wavelength in nm.
    temperature: Temperature in degrees C, or ``None`` if not available.
    timestamp: Measurement time as reported by the instrument, in seconds.
  """

  data: List[List[Optional[float]]]
  wavelength: int
  temperature: Optional[float]
  timestamp: float


@dataclasses.dataclass
class FluorescenceResult:
  """Result of a fluorescence measurement.

  Attributes:
    data: 2D array indexed [row][col]. ``None`` for unmeasured wells.
    excitation_wavelength: Excitation wavelength in nm.
    emission_wavelength: Emission wavelength in nm.
    temperature: Temperature in degrees C, or ``None`` if not available.
    timestamp: Measurement time as reported by the instrument, in seconds.
  """

  data: List[List[Optional[float]]]
  excitation_wavelength: int
  emission_wavelength: int
  temperature: Optional[float]
  timestamp: float


@dataclasses.dataclass
class LuminescenceResult:
  """Result of a luminescence measurement.

  Attributes:
    data: 2D array indexed [row][col]. ``None`` for unmeasured wells.
    temperature: Temperature in degrees C, or ``None`` if not available.
    timestamp: Measurement time as reported by the instrument, in seconds.
  """

  data: List[List[Optional[float]]]
  temperature: Optional[float]
  timestamp: float

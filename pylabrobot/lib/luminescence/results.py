"""The result of a luminescence measurement, the same for every plate reader."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional


@dataclass
class LuminescenceResult:
  """Luminescence intensities of one emission band, from one read of a plate.

  Attributes:
    data: intensities of the measured wells in the reader's counts, keyed by well identifier
      (``"B3"``), row by row; wells not measured are not in it.
    emission_wavelength: emission wavelength in nm; None if the read selects no wavelength (all
      the light the sample emits).
    emission_bandwidth: emission bandwidth in nm; None as for the wavelength.
    temperature: temperature in degrees C, or ``None`` if the reader did not measure one.
    start_timestamp: when the read started.
    end_timestamp: when the read's data was in.
  """

  data: Dict[str, float]
  emission_wavelength: Optional[float]
  emission_bandwidth: Optional[float]
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime

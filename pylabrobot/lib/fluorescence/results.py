"""The result of a fluorescence measurement, the same for every plate reader."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional


@dataclass
class FluorescenceResult:
  """Fluorescence intensities of one excitation and emission wavelength, from one read of a plate.

  Attributes:
    data: intensities of the measured wells in the reader's counts, keyed by well identifier
      (``"B3"``), row by row; wells not measured are not in it.
    excitation_wavelength: excitation wavelength in nm; through a filter its passband's centre,
      None if the filter has no single known passband.
    excitation_bandwidth: excitation bandwidth in nm; through a filter its passband's width, None
      as for the wavelength.
    emission_wavelength: emission wavelength in nm; through a filter as for excitation.
    emission_bandwidth: emission bandwidth in nm; through a filter as for excitation.
    temperature: temperature in degrees C, or ``None`` if the reader did not measure one.
    start_timestamp: when the read started.
    end_timestamp: when the read's data was in.
  """

  data: Dict[str, float]
  excitation_wavelength: Optional[float]
  excitation_bandwidth: Optional[float]
  emission_wavelength: Optional[float]
  emission_bandwidth: Optional[float]
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime


@dataclass
class FluorescenceSpectrumResult:
  """Fluorescence spectra from one read of a plate: excitation or emission scanned, one fixed.

  Attributes:
    data: each measured well's spectrum, intensity in the reader's counts by the scanned
      wavelength in nm in scan order, keyed by well identifier (``"B3"``), row by row; wells not
      measured are not in it.
    excitation_wavelength: the fixed excitation wavelength in nm; None when excitation is scanned.
    excitation_bandwidth: the excitation bandwidth in nm the read used; None if not known.
    emission_wavelength: the fixed emission wavelength in nm; None when emission is scanned.
    emission_bandwidth: the emission bandwidth in nm the read used; None if not known.
    temperature: temperature in degrees C, or ``None`` if the reader did not measure one.
    start_timestamp: when the read started.
    end_timestamp: when the read's data was in.
  """

  data: Dict[str, Dict[int, float]]
  excitation_wavelength: Optional[float]
  excitation_bandwidth: Optional[float]
  emission_wavelength: Optional[float]
  emission_bandwidth: Optional[float]
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime

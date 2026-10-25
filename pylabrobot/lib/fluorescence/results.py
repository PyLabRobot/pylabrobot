"""The result of a fluorescence measurement, the same for every plate reader."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pylabrobot.lib.results.table import get_base_row, get_spectrum_rows, get_well_rows
from pylabrobot.utils.timing import get_current_cycle


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
    current_cycle: the `run_cycles` cycle the read was made in, as `get_current_cycle` gave it
      when the result was made: `run_start_timestamp`, `cycle`, `due_timestamp`; all None outside
      a run.
  """

  data: Dict[str, float]
  excitation_wavelength: Optional[float]
  excitation_bandwidth: Optional[float]
  emission_wavelength: Optional[float]
  emission_bandwidth: Optional[float]
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime
  current_cycle: Dict[str, Any] = field(default_factory=get_current_cycle, kw_only=True)

  def _get_base_row(self, read: int, **columns: Any) -> Dict[str, Any]:
    """The columns every row of the read shares (`pylabrobot.lib.results.table`)."""
    return get_base_row(
      read,
      "fluorescence",
      self.start_timestamp,
      self.end_timestamp,
      excitation_wavelength=self.excitation_wavelength,
      excitation_bandwidth=self.excitation_bandwidth,
      emission_wavelength=self.emission_wavelength,
      emission_bandwidth=self.emission_bandwidth,
      temperature=self.temperature,
      quantity="counts",
      **self.current_cycle,
      **columns,
    )

  def get_rows(self, read: int) -> List[Dict[str, Any]]:
    """One row per well: its counts (`pylabrobot.lib.results.table`)."""
    return get_well_rows(self._get_base_row(read), self.data)


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
    current_cycle: the `run_cycles` cycle the read was made in, as `get_current_cycle` gave it
      when the result was made: `run_start_timestamp`, `cycle`, `due_timestamp`; all None outside
      a run.
  """

  data: Dict[str, Dict[int, float]]
  excitation_wavelength: Optional[float]
  excitation_bandwidth: Optional[float]
  emission_wavelength: Optional[float]
  emission_bandwidth: Optional[float]
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime
  current_cycle: Dict[str, Any] = field(default_factory=get_current_cycle, kw_only=True)

  def _get_base_row(self, read: int, **columns: Any) -> Dict[str, Any]:
    """The columns every row of the read shares (`pylabrobot.lib.results.table`)."""
    return get_base_row(
      read,
      "fluorescence_spectrum",
      self.start_timestamp,
      self.end_timestamp,
      excitation_wavelength=self.excitation_wavelength,
      excitation_bandwidth=self.excitation_bandwidth,
      emission_wavelength=self.emission_wavelength,
      emission_bandwidth=self.emission_bandwidth,
      temperature=self.temperature,
      quantity="counts",
      **self.current_cycle,
      **columns,
    )

  def get_rows(self, read: int) -> List[Dict[str, Any]]:
    """One row per well and scanned wavelength: its counts (`pylabrobot.lib.results.table`)."""
    scanned = "excitation" if self.excitation_wavelength is None else "emission"
    return get_spectrum_rows(self._get_base_row(read), self.data, f"{scanned}_wavelength")

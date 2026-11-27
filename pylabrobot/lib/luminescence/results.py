"""The result of a luminescence measurement, the same for every plate reader."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pylabrobot.lib.results.table import get_base_row, get_well_rows
from pylabrobot.utils.timing import get_current_cycle


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
    current_cycle: the `run_cycles` cycle the read was made in, as `get_current_cycle` gave it
      when the result was made: `run_start_timestamp`, `cycle`, `due_timestamp`; all None outside
      a run.
  """

  data: Dict[str, float]
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
      "luminescence",
      self.start_timestamp,
      self.end_timestamp,
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

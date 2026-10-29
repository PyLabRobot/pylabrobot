"""The result of an absorbance measurement, the same for every plate reader, and its data placed
on a plate's grid."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pylabrobot.lib.results.table import get_base_row, get_spectrum_rows, get_well_rows
from pylabrobot.resources import Plate
from pylabrobot.utils.timing import get_current_cycle


@dataclass
class AbsorbanceResult:
  """Optical densities of one wavelength, from one read of a plate.

  Attributes:
    data: optical densities of the measured wells, keyed by well identifier (``"B3"``), row by
      row; wells not measured are not in it.
    wavelength: wavelength in nm.
    temperature: temperature in degrees C, or ``None`` if the reader did not measure one.
    start_timestamp: when the read started.
    end_timestamp: when the read's data was in.
    current_cycle: the `run_cycles` cycle the read was made in, as `get_current_cycle` gave it
      when the result was made: `run_start_timestamp`, `cycle`, `due_timestamp`; all None outside
      a run.
  """

  data: Dict[str, float]
  wavelength: int
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime
  current_cycle: Dict[str, Any] = field(default_factory=get_current_cycle, kw_only=True)

  def _get_base_row(self, read: int, **columns: Any) -> Dict[str, Any]:
    """The columns every row of the read shares (`pylabrobot.lib.results.table`)."""
    return get_base_row(
      read,
      "absorbance",
      self.start_timestamp,
      self.end_timestamp,
      wavelength=self.wavelength,
      temperature=self.temperature,
      quantity="optical_density",
      **self.current_cycle,
      **columns,
    )

  def get_rows(self, read: int) -> List[Dict[str, Any]]:
    """One row per well: its optical density (`pylabrobot.lib.results.table`)."""
    return get_well_rows(self._get_base_row(read), self.data)


@dataclass
class AbsorbanceSpectrumResult:
  """Optical density spectra from one read of a plate.

  Attributes:
    data: each measured well's spectrum, optical density by wavelength in nm in scan order, keyed
      by well identifier (``"B3"``), row by row; wells not measured are not in it.
    temperature: temperature in degrees C, or ``None`` if the reader did not measure one.
    start_timestamp: when the read started.
    end_timestamp: when the read's data was in.
    current_cycle: the `run_cycles` cycle the read was made in, as `get_current_cycle` gave it
      when the result was made: `run_start_timestamp`, `cycle`, `due_timestamp`; all None outside
      a run.
  """

  data: Dict[str, Dict[int, float]]
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime
  current_cycle: Dict[str, Any] = field(default_factory=get_current_cycle, kw_only=True)

  def _get_base_row(self, read: int, **columns: Any) -> Dict[str, Any]:
    """The columns every row of the read shares (`pylabrobot.lib.results.table`)."""
    return get_base_row(
      read,
      "absorbance_spectrum",
      self.start_timestamp,
      self.end_timestamp,
      temperature=self.temperature,
      quantity="optical_density",
      **self.current_cycle,
      **columns,
    )

  def get_rows(self, read: int) -> List[Dict[str, Any]]:
    """One row per well and wavelength: its optical density (`pylabrobot.lib.results.table`)."""
    return get_spectrum_rows(self._get_base_row(read), self.data, "wavelength")


def get_grid(plate: Plate, data: Dict[str, float]) -> List[List[Optional[float]]]:
  """`data`, keyed by well identifier, as a [row][column] grid of `plate`; None where no value.

  Raises:
    ValueError: if `data` keys a well `plate` does not have.
  """
  rows = plate.num_items_y
  position = {
    plate.get_child_identifier(well): (index % rows, index // rows)  # items are column by column
    for index, well in enumerate(plate.get_all_items())
  }
  unknown = [identifier for identifier in data if identifier not in position]
  if unknown:
    raise ValueError(f"{plate.name} has no wells {unknown}")
  grid: List[List[Optional[float]]] = [[None] * plate.num_items_x for _ in range(rows)]
  for identifier, value in data.items():
    row, column = position[identifier]
    grid[row][column] = value
  return grid


def get_grid_at(
  plate: Plate, result: AbsorbanceSpectrumResult, wavelength: int
) -> List[List[Optional[float]]]:
  """The optical densities of `result` at `wavelength`, as a [row][column] grid of `plate`; None
  where no value.

  Raises:
    ValueError: if `wavelength` was not scanned, or `result` keys a well `plate` does not have.
  """
  if any(wavelength not in spectrum for spectrum in result.data.values()):
    raise ValueError(f"{wavelength} nm was not scanned")
  return get_grid(plate, {well: spectrum[wavelength] for well, spectrum in result.data.items()})

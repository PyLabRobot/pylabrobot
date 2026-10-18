"""The result of an absorbance measurement, the same for every plate reader, and its data placed
on a plate's grid."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from pylabrobot.resources import Plate


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
  """

  data: Dict[str, float]
  wavelength: int
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime


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

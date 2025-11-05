import time
from typing import Dict, List, Optional, Tuple

from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate, Well


class PlateReaderChatterboxBackend(PlateReaderBackend):
  """An abstract class for a plate reader. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate."""

  def __init__(self):
    self.dummy_luminescence: List[List[Optional[float]]] = [[0.0] * 12] * 8
    self.dummy_absorbance: List[List[Optional[float]]] = [[0.0] * 12] * 8
    self.dummy_fluorescence: List[List[Optional[float]]] = [[0.0] * 12] * 8

  async def setup(self) -> None:
    print("Setting up the plate reader.")

  async def stop(self) -> None:
    print("Stopping the plate reader.")

  async def open(self) -> None:
    print("Opening the plate reader.")

  async def close(self, plate: Optional[Plate]) -> None:
    print(f"Closing the plate reader with plate, {plate}.")

  def _print_plate_reading_wells(self, result: List[List[Optional[float]]]) -> None:
    print("Read the following wells:")

    cell_width = 7
    precision = 3

    def fmt_cell(val: Optional[float]) -> str:
      if val is None:
        return ""  # print empty for None
      return f"{val:.{precision}f}"

    def row_label(r: int) -> str:
      return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[r] if r < 26 else "?"

    num_rows = len(result)
    num_cols = max(len(row) for row in result)

    # Header
    top = " " * (len(row_label(num_cols - 1)) + 1) + "|"
    for c in range(num_cols):
      top += f"{c+1:>{cell_width}}|"
    print(top)

    # Divider
    print("-" * len(top))

    # Rows
    for r in range(num_rows):
      line = f"{row_label(r)} ".rjust(len(row_label(num_cols - 1)) + 1) + "|"
      for c in range(num_cols):
        line += f"{fmt_cell(result[r][c]):>{cell_width}}|"
      print(line)

  def _mask_result(
    self, result: List[List[Optional[float]]], wells: List[Well], plate: Plate
  ) -> List[List[Optional[float]]]:
    masked: List[List[Optional[float]]] = [
      [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
    ]
    for well in wells:
      r, c = well.get_row(), well.get_column()
      if r < plate.num_items_y and c < plate.num_items_x:
        masked[r][c] = result[r][c]
    return masked

  def _format_data(
    self, data: List[List[Optional[float]]], key: Tuple[int, int]
  ) -> List[Dict[Tuple[int, int], Dict]]:
    return [
      {
        key: {
          "data": data,
          "temp": float("nan"),
          "time": time.time(),
        }
      }
    ]

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[Dict[Tuple[int, int], Dict]]:
    print(f"Reading luminescence at focal height {focal_height}.")
    result = self._mask_result(self.dummy_luminescence, wells, plate)
    self._print_plate_reading_wells(result)
    return self._format_data(result, (0, 0))

  async def read_absorbance(
    self, plate: Plate, wells: List[Well], wavelength: int
  ) -> List[Dict[Tuple[int, int], Dict]]:
    print(f"Reading absorbance at wavelength {wavelength}.")
    result = self._mask_result(self.dummy_absorbance, wells, plate)
    self._print_plate_reading_wells(result)
    return self._format_data(result, (wavelength, 0))

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict[Tuple[int, int], Dict]]:
    print(
      f"Reading fluorescence at excitation wavelength {excitation_wavelength}, emission wavelength {emission_wavelength}, and focal height {focal_height}."
    )
    result = self._mask_result(self.dummy_fluorescence, wells, plate)
    self._print_plate_reading_wells(result)
    return self._format_data(result, (excitation_wavelength, emission_wavelength))

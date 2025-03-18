from typing import List, Optional

from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources.plate import Plate


class PlateReaderChatterboxBackend(PlateReaderBackend):
  """An abstract class for a plate reader. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate."""

  def __init__(self):
    self.dummy_luminescence = [[0.0] * 12] * 8
    self.dummy_absorbance = [[0.0] * 12] * 8
    self.dummy_fluorescence = [[0.0] * 12] * 8

  async def setup(self) -> None:
    print("Setting up the plate reader.")

  async def stop(self) -> None:
    print("Stopping the plate reader.")

  async def open(self) -> None:
    print("Opening the plate reader.")

  async def close(self, plate: Optional[Plate]) -> None:
    print(f"Closing the plate reader with plate, {plate}.")

  async def read_luminescence(self, plate: Plate, focal_height: float) -> List[List[float]]:
    print(f"Reading luminescence at focal height {focal_height}.")
    return self.dummy_luminescence

  async def read_absorbance(self, plate: Plate, wavelength: int) -> List[List[float]]:
    print(f"Reading absorbance at wavelength {wavelength}.")
    return self.dummy_absorbance

  async def read_fluorescence(
    self,
    plate: Plate,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    return self.dummy_fluorescence

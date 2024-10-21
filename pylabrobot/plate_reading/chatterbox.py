from typing import List
from pylabrobot.plate_reading.backend import PlateReaderBackend


class PlateReaderChatterboxBackend(PlateReaderBackend):
  """ An abstract class for a plate reader. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate. """

  def __init__(self):
    self.dummy_luminescence = [[0.0]*12]*8
    self.dummy_absorbance = [[0.0]*12]*8
    self.dummy_fluorescence = [[0.0]*12]*8

  async def setup(self) -> None:
    print("Setting up the plate reader.")

  async def stop(self) -> None:
    print("Stopping the plate reader.")

  async def open(self) -> None:
    print("Opening the plate reader.")

  async def close(self) -> None:
    print("Closing the plate reader.")

  async def read_luminescence(self, focal_height: float) -> List[List[float]]:
    print(f"Reading luminescence at focal height {focal_height}.")
    return self.dummy_luminescence

  async def read_absorbance(self, wavelength: int) -> List[List[float]]:
    print(f"Reading absorbance at wavelength {wavelength}.")
    return self.dummy_absorbance

  async def read_fluorescence(
    self,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float
  ) -> List[List[float]]:
    return self.dummy_fluorescence

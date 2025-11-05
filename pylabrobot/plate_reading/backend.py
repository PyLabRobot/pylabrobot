from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Dict, List, Optional, Tuple

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well


class PlateReaderBackend(MachineBackend, metaclass=ABCMeta):
  """An abstract class for a plate reader. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate."""

  @abstractmethod
  async def setup(self) -> None:
    """Set up the plate reader. This should be called before any other methods."""

  @abstractmethod
  async def stop(self) -> None:
    """Close all connections to the plate reader and make sure setup() can be called again."""

  @abstractmethod
  async def open(self) -> None:
    """Open the plate reader. Also known as plate out."""

  @abstractmethod
  async def close(self, plate: Optional[Plate]) -> None:
    """Close the plate reader. Also known as plate in."""

  @abstractmethod
  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[Dict[Tuple[int, int], Dict]]:
    """Read the luminescence from the plate reader.

    Returns:
      A list of dictionaries, one for each timepoint. Each dictionary has a key (0, 0)
      and a value containing the data, temperature, and time.
    """

  @abstractmethod
  async def read_absorbance(
    self, plate: Plate, wells: List[Well], wavelength: int
  ) -> List[Dict[Tuple[int, int], Dict]]:
    """Read the absorbance from the plate reader.

    Returns:
      A list of dictionaries, one for each timepoint. Each dictionary has a key (wavelength, 0)
      and a value containing the data, temperature, and time.
    """

  @abstractmethod
  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict[Tuple[int, int], Dict]]:
    """Read the fluorescence from the plate reader.

    Returns:
      A list of dictionaries, one for each timepoint. Each dictionary has a key
      (excitation_wavelength, emission_wavelength) and a value containing the data, temperature,
      and time.
    """


class ImagerBackend(MachineBackend, metaclass=ABCMeta):
  @abstractmethod
  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
  ) -> ImagingResult:
    """Capture an image of the plate in the specified mode."""


class ImageReaderBackend(PlateReaderBackend, ImagerBackend):
  pass

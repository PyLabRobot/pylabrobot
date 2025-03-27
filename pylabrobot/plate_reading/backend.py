from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  Image,
  ImagingMode,
  Objective,
)
from pylabrobot.resources.plate import Plate


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
  async def read_luminescence(self, plate: Plate, focal_height: float) -> List[List[float]]:
    """Read the luminescence from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""

  @abstractmethod
  async def read_absorbance(self, plate: Plate, wavelength: int) -> List[List[float]]:
    """Read the absorbance from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""

  @abstractmethod
  async def read_fluorescence(
    self,
    plate: Plate,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    """Read the fluorescence from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""


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
  ) -> List[Image]:
    """Capture an image of the plate in the specified mode."""


class ImageReaderBackend(PlateReaderBackend, ImagerBackend):
  pass

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.device import DeviceBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well


class FluorescenceBackend(DeviceBackend, metaclass=ABCMeta):
  """Abstract backend for fluorescence plate reading."""

  @abstractmethod
  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[FluorescenceResult]:
    """Read fluorescence for the given wells.

    Args:
      plate: The plate to read.
      wells: Wells to measure.
      excitation_wavelength: Excitation wavelength in nm.
      emission_wavelength: Emission wavelength in nm.
      focal_height: Focal height in mm.

    Returns:
      A list of :class:`FluorescenceResult` (typically length 1).
    """

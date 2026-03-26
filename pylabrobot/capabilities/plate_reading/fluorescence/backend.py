from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin


class FluorescenceBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for fluorescence plate reading."""

  @abstractmethod
  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
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

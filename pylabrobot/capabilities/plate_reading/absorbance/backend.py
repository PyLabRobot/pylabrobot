from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.capabilities.plate_reading.absorbance.standard import AbsorbanceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin


class AbsorbanceBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for absorbance plate reading."""

  @abstractmethod
  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[AbsorbanceResult]:
    """Read absorbance for the given wells.

    Args:
      plate: The plate to read.
      wells: Wells to measure.
      wavelength: Wavelength in nm.

    Returns:
      A list of :class:`AbsorbanceResult` (typically length 1).
    """

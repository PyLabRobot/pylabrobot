from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin


class LuminescenceBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for luminescence plate reading."""

  @abstractmethod
  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    """Read luminescence for the given wells.

    Args:
      plate: The plate to read.
      wells: Wells to measure.
      focal_height: Focal height in mm.

    Returns:
      A list of :class:`LuminescenceResult` (typically length 1).
    """

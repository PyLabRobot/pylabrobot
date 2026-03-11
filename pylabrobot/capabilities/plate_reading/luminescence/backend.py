from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.device import DeviceBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well


class LuminescenceBackend(DeviceBackend, metaclass=ABCMeta):
  """Abstract backend for luminescence plate reading."""

  @abstractmethod
  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[LuminescenceResult]:
    """Read luminescence for the given wells.

    Args:
      plate: The plate to read.
      wells: Wells to measure.
      focal_height: Focal height in mm.

    Returns:
      A list of :class:`LuminescenceResult` (typically length 1).
    """

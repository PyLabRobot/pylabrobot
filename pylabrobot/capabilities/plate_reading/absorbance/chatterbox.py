import time
from typing import List, Optional

from pylabrobot.capabilities.plate_reading.absorbance.backend import AbsorbanceBackend
from pylabrobot.capabilities.plate_reading.absorbance.standard import AbsorbanceResult
from pylabrobot.capabilities.plate_reading.utils import mask_wells
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well


class AbsorbanceChatterboxBackend(AbsorbanceBackend):
  """Mock absorbance backend for testing."""

  def __init__(self):
    self.dummy_absorbance: List[List[Optional[float]]] = [[0.0] * 12 for _ in range(8)]

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def read_absorbance(
    self, plate: Plate, wells: List[Well], wavelength: int
  ) -> List[AbsorbanceResult]:
    data = mask_wells(self.dummy_absorbance, wells, plate)
    return [
      AbsorbanceResult(
        data=data,
        wavelength=wavelength,
        temperature=None,
        timestamp=time.time(),
      )
    ]

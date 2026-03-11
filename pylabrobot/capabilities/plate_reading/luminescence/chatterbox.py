import time
from typing import List, Optional

from pylabrobot.capabilities.plate_reading.utils import mask_wells
from pylabrobot.capabilities.plate_reading.luminescence.backend import LuminescenceBackend
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well


class LuminescenceChatterboxBackend(LuminescenceBackend):
  """Mock luminescence backend for testing."""

  def __init__(self):
    self.dummy_luminescence: List[List[Optional[float]]] = [[0.0] * 12 for _ in range(8)]

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[LuminescenceResult]:
    data = mask_wells(self.dummy_luminescence, wells, plate)
    return [
      LuminescenceResult(
        data=data,
        temperature=None,
        timestamp=time.time(),
      )
    ]

import time
from typing import List, Optional

from pylabrobot.capabilities.plate_reading.fluorescence.backend import FluorescenceBackend
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.capabilities.plate_reading.utils import mask_wells
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin


class FluorescenceChatterboxBackend(FluorescenceBackend):
  """Mock fluorescence backend for testing."""

  def __init__(self):
    self.dummy_fluorescence: List[List[Optional[float]]] = [[0.0] * 12 for _ in range(8)]

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[FluorescenceResult]:
    data = mask_wells(self.dummy_fluorescence, wells, plate)
    return [
      FluorescenceResult(
        data=data,
        excitation_wavelength=excitation_wavelength,
        emission_wavelength=emission_wavelength,
        temperature=None,
        timestamp=time.time(),
      )
    ]

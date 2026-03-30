from typing import List, Optional

from pylabrobot.capabilities.plate_reading.fluorescence import (
  FluorescenceBackend,
  FluorescenceResult,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .driver import CLARIOstarDriver


class CLARIOstarFluorescenceBackend(FluorescenceBackend):
  """Translates FluorescenceBackend interface into CLARIOstar driver commands."""

  def __init__(self, driver: CLARIOstarDriver):
    self.driver = driver

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[FluorescenceResult]:
    raise NotImplementedError("CLARIOstar fluorescence reading is not implemented yet.")

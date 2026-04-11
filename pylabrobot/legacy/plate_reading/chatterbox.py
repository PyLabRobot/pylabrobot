from typing import Dict, List, Optional

from pylabrobot.capabilities.plate_reading.absorbance.chatterbox import (
  AbsorbanceChatterboxBackend,
)
from pylabrobot.capabilities.plate_reading.fluorescence.chatterbox import (
  FluorescenceChatterboxBackend,
)
from pylabrobot.capabilities.plate_reading.luminescence.chatterbox import (
  LuminescenceChatterboxBackend,
)
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate, Well


class PlateReaderChatterboxBackend(PlateReaderBackend):
  """Chatterbox plate reader backend. Delegates to the new capability chatterbox backends."""

  def __init__(self):
    self._absorbance = AbsorbanceChatterboxBackend()
    self._fluorescence = FluorescenceChatterboxBackend()
    self._luminescence = LuminescenceChatterboxBackend()

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def open(self) -> None:
    pass

  async def close(self, plate: Optional[Plate]) -> None:
    pass

  async def read_absorbance(self, plate: Plate, wells: List[Well], wavelength: int) -> List[Dict]:
    results = await self._absorbance.read_absorbance(
      plate=plate, wells=wells, wavelength=wavelength
    )
    return [
      {
        "wavelength": r.wavelength,
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict]:
    results = await self._fluorescence.read_fluorescence(
      plate=plate,
      wells=wells,
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
    )
    return [
      {
        "ex_wavelength": r.excitation_wavelength,
        "em_wavelength": r.emission_wavelength,
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[Dict]:
    results = await self._luminescence.read_luminescence(
      plate=plate, wells=wells, focal_height=focal_height
    )
    return [
      {
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

"""Legacy. Use pylabrobot.byonoy instead."""

from typing import Dict, List, Optional

from pylabrobot.byonoy import absorbance_96, luminescence_96
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate, Well


class ByonoyAbsorbance96AutomateBackend(PlateReaderBackend):
  """Legacy. Use pylabrobot.byonoy.ByonoyAbsorbance96Backend instead."""

  def __init__(self) -> None:
    self._new = absorbance_96.ByonoyAbsorbance96Backend()

  async def setup(self, verbose: bool = False, **backend_kwargs):
    await self._new.setup(**backend_kwargs)

  async def stop(self) -> None:
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def open(self) -> None:
    raise NotImplementedError(
      "byonoy cannot open by itself. you need to move the top module using a robot arm."
    )

  async def close(self, plate: Optional[Plate]) -> None:
    raise NotImplementedError(
      "byonoy cannot close by itself. you need to move the top module using a robot arm."
    )

  async def read_absorbance(self, plate: Plate, wells: List[Well], wavelength: int) -> List[Dict]:
    results = await self._new.read_absorbance(plate=plate, wells=wells, wavelength=wavelength)
    return [
      {
        "wavelength": r.wavelength,
        "time": r.timestamp,
        "temperature": r.temperature,
        "data": r.data,
      }
      for r in results
    ]

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[Dict]:
    raise NotImplementedError("Absorbance plate reader does not support luminescence reading.")

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict]:
    raise NotImplementedError("Absorbance plate reader does not support fluorescence reading.")


class ByonoyLuminescence96AutomateBackend(PlateReaderBackend):
  """Legacy. Use pylabrobot.byonoy.ByonoyLuminescence96Backend instead."""

  def __init__(self) -> None:
    self._new = luminescence_96.ByonoyLuminescence96Backend()

  async def setup(self) -> None:
    await self._new.setup()

  async def stop(self) -> None:
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def open(self) -> None:
    raise NotImplementedError(
      "byonoy cannot open by itself. you need to move the top module using a robot arm."
    )

  async def close(self, plate: Optional[Plate]) -> None:
    raise NotImplementedError(
      "byonoy cannot close by itself. you need to move the top module using a robot arm."
    )

  async def read_absorbance(self, plate: Plate, wells: List[Well], wavelength: int) -> List[Dict]:
    raise NotImplementedError(
      "Luminescence plate reader does not support absorbance reading. "
      "Use ByonoyAbsorbance96Automate instead."
    )

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float, integration_time: float = 2
  ) -> List[Dict]:
    from pylabrobot.byonoy.luminescence_96 import ByonoyLuminescence96Backend

    params = ByonoyLuminescence96Backend.LuminescenceParams(integration_time=integration_time)
    results = await self._new.read_luminescence(
      plate=plate, wells=wells, focal_height=focal_height, backend_params=params
    )
    return [
      {
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
    raise NotImplementedError("Luminescence plate reader does not support fluorescence reading.")

"""Legacy. Use pylabrobot.bmg_labtech instead."""

import sys
from typing import Dict, List, Optional, Tuple

from pylabrobot.bmg_labtech import clariostar
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


class CLARIOstarBackend(PlateReaderBackend):
  """Legacy. Use pylabrobot.bmg_labtech.CLARIOstarBackend instead."""

  def __init__(self, device_id: Optional[str] = None):
    self._new = clariostar.CLARIOstarBackend(device_id=device_id)

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def open(self):
    await self._new.open()

  async def close(self, plate: Optional[Plate] = None):
    await self._new.close()

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float = 13
  ) -> List[Dict]:
    results = await self._new.read_luminescence(plate=plate, wells=wells, focal_height=focal_height)
    return [
      {
        "data": r.data,
        "temperature": float("nan"),
        "time": r.timestamp,
      }
      for r in results
    ]

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    report: Literal["OD", "transmittance"] = "OD",
  ) -> List[Dict]:
    from pylabrobot.bmg_labtech.clariostar import CLARIOstarBackend

    params = CLARIOstarBackend.AbsorbanceParams(report=report)
    results = await self._new.read_absorbance(
      plate=plate, wells=wells, wavelength=wavelength, backend_params=params
    )
    return [
      {
        "wavelength": r.wavelength,
        "data": r.data,
        "temperature": float("nan"),
        "time": r.timestamp,
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
  ) -> List[Dict[Tuple[int, int], Dict]]:
    raise NotImplementedError("Not implemented yet")


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class CLARIOStar:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`CLARIOStar` is deprecated. Please use `CLARIOStarBackend` instead.")


class CLARIOStarBackend:
  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`CLARIOStarBackend` (capital 'S') is deprecated. Please use `CLARIOstarBackend` instead."
    )

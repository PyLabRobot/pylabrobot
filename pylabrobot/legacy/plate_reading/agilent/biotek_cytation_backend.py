"""Legacy. Use pylabrobot.agilent instead."""

from typing import Optional

from pylabrobot.agilent.biotek import cytation_aravis_driver
from pylabrobot.agilent.biotek.cytation_aravis_driver import CytationImagingConfig
from pylabrobot.legacy.plate_reading.agilent.biotek_backend import BioTekPlateReaderBackend
from pylabrobot.legacy.plate_reading.backend import ImagerBackend
from pylabrobot.legacy.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources import Plate


class CytationBackend(BioTekPlateReaderBackend, ImagerBackend):
  """Legacy. Use pylabrobot.agilent.CytationAravisDriver instead."""

  _new: cytation_aravis_driver.CytationAravisDriver

  def __init__(
    self,
    timeout: float = 20,
    device_id: Optional[str] = None,
    imaging_config: Optional[CytationImagingConfig] = None,
  ) -> None:
    self._new = cytation_aravis_driver.CytationAravisDriver(
      timeout=timeout, device_id=device_id, imaging_config=imaging_config
    )

  @property
  def imaging_config(self):
    return self._new.imaging_config

  @imaging_config.setter
  def imaging_config(self, value):
    self._new.imaging_config = value

  async def setup(self, use_cam: bool = False) -> None:
    from pylabrobot.agilent.biotek.cytation_aravis_driver import CytationAravisDriver

    await self._new.setup(backend_params=CytationAravisDriver.SetupParams(use_cam=use_cam))

  async def stop(self):
    await self._new.stop()

  @property
  def supports_heating(self):
    return True

  @property
  def supports_cooling(self):
    return True

  @property
  def objectives(self):
    return self._new.microscopy_backend.objectives

  @property
  def filters(self):
    return self._new.microscopy_backend.filters

  async def close(self, plate=None, slow=False):
    await self._new.close(plate=plate, slow=slow)

  def start_acquisition(self):
    self._new.microscopy_backend.start_acquisition()

  def stop_acquisition(self):
    self._new.microscopy_backend.stop_acquisition()

  async def led_on(self, intensity=10):
    await self._new.microscopy_backend.led_on(intensity=intensity)

  async def led_off(self):
    await self._new.microscopy_backend.led_off()

  async def set_focus(self, focal_position):
    await self._new.microscopy_backend.set_focus(focal_position)

  async def set_position(self, x, y):
    await self._new.microscopy_backend.set_position(x, y)

  async def set_auto_exposure(self, auto_exposure):
    await self._new.microscopy_backend.set_auto_exposure(auto_exposure)

  async def set_exposure(self, exposure):
    await self._new.microscopy_backend.set_exposure(exposure)

  async def select(self, row, column):
    await self._new.microscopy_backend.select(row, column)

  async def set_gain(self, gain):
    await self._new.microscopy_backend.set_gain(gain)

  async def set_objective(self, objective):
    await self._new.microscopy_backend.set_objective(objective)

  async def set_imaging_mode(self, mode, led_intensity):
    await self._new.microscopy_backend.set_imaging_mode(mode, led_intensity)

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
    **kwargs,
  ) -> ImagingResult:
    from pylabrobot.capabilities.microscopy.standard import ImagingMode as NewImagingMode
    from pylabrobot.capabilities.microscopy.standard import Objective as NewObjective

    new_mode = NewImagingMode[mode.name]
    new_objective = NewObjective[objective.name]

    result = await self._new.microscopy_backend.capture(
      row=row,
      column=column,
      mode=new_mode,
      objective=new_objective,
      exposure_time=exposure_time,
      focal_height=focal_height,
      gain=gain,
      plate=plate,
      **kwargs,
    )
    return ImagingResult(
      images=result.images,
      exposure_time=result.exposure_time,
      focal_height=result.focal_height,
    )

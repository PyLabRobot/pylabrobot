from __future__ import annotations

from typing import Optional

from pylabrobot.agilent.biotek.cytation.base import _CytationBase
from pylabrobot.agilent.biotek.cytation.microscope.microscope import (
  CytationImagingConfig,
  CytationMicroscope,
)


class Cytation5(_CytationBase):
  """Agilent BioTek Cytation 5. Adds a camera, reached through :attr:`microscope`."""

  _model_name = "Agilent BioTek Cytation 5"

  def __init__(
    self,
    name: str,
    camera_serial: Optional[str] = None,
    device_id: Optional[str] = None,
    imaging_config: Optional[CytationImagingConfig] = None,
    use_cam: bool = True,
  ):
    super().__init__(name=name, device_id=device_id)

    self.microscope = CytationMicroscope(
      driver=self,
      camera_serial=camera_serial,
      imaging_config=imaging_config,
      use_cam=use_cam,
    )

  async def setup(self) -> None:
    await super().setup()
    await self.microscope._on_setup()

  async def stop(self) -> None:
    await self.microscope._on_stop()
    await super().stop()

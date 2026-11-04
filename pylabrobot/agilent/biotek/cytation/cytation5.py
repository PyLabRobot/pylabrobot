from __future__ import annotations

from typing import Any, Optional

from pylabrobot.agilent.biotek.cytation.base import _CytationBase
from pylabrobot.agilent.biotek.cytation.microscope.microscope import (
  CytationImagingConfig,
  CytationMicroscope,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding


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

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Enter the base reader before its owned microscope."""
    await super()._enter_lifespan(stack, **kwargs)
    await stack.enter_async_context(self.microscope.lifespan())

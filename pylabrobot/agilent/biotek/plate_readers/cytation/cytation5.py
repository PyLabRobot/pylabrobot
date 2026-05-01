"""Cytation 5 device — plate reader + imager.

Example::

    cytation = Cytation5(name="cytation5", camera_serial="22580842")

    await cytation.setup()
    result = await cytation.microscopy.capture(...)
    await cytation.stop()
"""

from __future__ import annotations

import logging

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence

from .base import _CytationBase

logger = logging.getLogger(__name__)


class Cytation5(_CytationBase):
  """Agilent BioTek Cytation 5 — plate reader + imager.

  Capabilities:
    - absorbance, fluorescence, luminescence (plate reading)
    - microscopy (imaging)
    - temperature (incubation)
    - loading tray
  """

  _model_name = "Agilent BioTek Cytation 5"

  async def setup(self, backend_params: BackendParams | None = None) -> None:
    del backend_params
    await self._setup_base()

    self.absorbance = Absorbance(backend=self.driver)
    self.luminescence = Luminescence(backend=self.driver)
    self.fluorescence = Fluorescence(backend=self.driver)

    self._capabilities = [
      self.absorbance,
      self.luminescence,
      self.fluorescence,
      self.microscopy,
      self.temperature,
      self.loading_tray,
    ]

    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True
    logger.info("Cytation5 setup complete")

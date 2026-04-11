"""Cytation 1 device — imager with temperature control.

Example::

    cytation = Cytation1(name="cytation1", camera_serial="22580842")

    await cytation.setup()
    result = await cytation.microscopy.capture(...)
    await cytation.stop()
"""

from __future__ import annotations

import logging

from .base import _CytationBase

logger = logging.getLogger(__name__)


class Cytation1(_CytationBase):
  """Agilent BioTek Cytation 1 — imager with temperature control.

  Capabilities:
    - microscopy (imaging)
    - temperature (incubation)
    - loading tray
  """

  _model_name = "Agilent BioTek Cytation 1"

  async def setup(self) -> None:
    await self._setup_base()

    self._capabilities = [self.microscopy, self.temperature, self.loading_tray]

    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True
    logger.info("Cytation1 setup complete")

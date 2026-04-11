from __future__ import annotations

import logging
from typing import List, Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.capabilities.plate_reading.fluorescence.standard import FluorescenceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .backend import FluorescenceBackend

logger = logging.getLogger(__name__)


class Fluorescence(Capability):
  """Fluorescence plate reading capability.

  See :doc:`/user_guide/capabilities/fluorescence` for a walkthrough.
  """

  def __init__(self, backend: FluorescenceBackend):
    super().__init__(backend=backend)
    self.backend: FluorescenceBackend = backend

  @need_capability_ready
  async def read(
    self,
    plate: Plate,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    wells: Optional[List[Well]] = None,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[FluorescenceResult]:
    """Read fluorescence from a plate.

    Args:
      plate: The plate to read.
      excitation_wavelength: Excitation wavelength in nm.
      emission_wavelength: Emission wavelength in nm.
      focal_height: Focal height in mm.
      wells: Wells to measure. Defaults to all wells in the plate.
      backend_params: Backend-specific parameters.

    Returns:
      A list of :class:`FluorescenceResult` (typically length 1).
    """
    if wells is None:
      wells = plate.get_all_items()
    return await self.backend.read_fluorescence(
      plate=plate,
      wells=wells,
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
      backend_params=backend_params,
    )

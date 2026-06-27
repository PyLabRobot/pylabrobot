from __future__ import annotations

import logging
from typing import List, Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.capabilities.plate_reading.absorbance.standard import AbsorbanceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .backend import AbsorbanceBackend

logger = logging.getLogger(__name__)


class Absorbance(Capability):
  """Absorbance plate reading capability.

  See :doc:`/user_guide/capabilities/absorbance` for a walkthrough.
  """

  def __init__(self, backend: AbsorbanceBackend):
    super().__init__(backend=backend)
    self.backend: AbsorbanceBackend = backend

  @need_capability_ready
  async def read(
    self,
    plate: Plate,
    wavelength: int,
    wells: Optional[List[Well]] = None,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[AbsorbanceResult]:
    """Read absorbance from a plate.

    Args:
      plate: The plate to read.
      wavelength: Wavelength in nm.
      wells: Wells to measure. Defaults to all wells in the plate.
      backend_params: Backend-specific parameters.

    Returns:
      A list of :class:`AbsorbanceResult` (typically length 1).
    """
    if wells is None:
      wells = plate.get_all_items()
    return await self.backend.read_absorbance(
      plate=plate, wells=wells, wavelength=wavelength, backend_params=backend_params
    )

from __future__ import annotations

import logging
from typing import List, Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.capabilities.plate_reading.luminescence.standard import LuminescenceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .backend import LuminescenceBackend

logger = logging.getLogger(__name__)


class LuminescenceCapability(Capability):
  """Luminescence plate reading capability."""

  def __init__(self, backend: LuminescenceBackend):
    super().__init__(backend=backend)
    self.backend: LuminescenceBackend = backend

  @need_capability_ready
  async def read(
    self,
    plate: Plate,
    focal_height: float,
    wells: Optional[List[Well]] = None,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    """Read luminescence from a plate.

    Args:
      plate: The plate to read.
      focal_height: Focal height in mm.
      wells: Wells to measure. Defaults to all wells in the plate.
      backend_params: Backend-specific parameters.

    Returns:
      A list of :class:`LuminescenceResult` (typically length 1).
    """
    if wells is None:
      wells = plate.get_all_items()
    return await self.backend.read_luminescence(
      plate=plate, wells=wells, focal_height=focal_height, backend_params=backend_params
    )

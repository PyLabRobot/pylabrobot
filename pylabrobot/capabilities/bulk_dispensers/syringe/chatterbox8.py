import logging
from typing import Dict, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Plate

from .backend8 import SyringeDispensingBackend8

logger = logging.getLogger(__name__)


class SyringeDispensingChatterboxBackend8(SyringeDispensingBackend8):
  """Chatterbox backend for device-free testing."""

  async def dispense(
    self,
    plate: Plate,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    logger.info("Dispensing volumes %s to plate '%s'.", volumes, plate.name)

  async def prime(
    self,
    plate: Plate,
    volume: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    logger.info("Priming syringe pump for plate '%s' (volume=%s).", plate.name, volume)

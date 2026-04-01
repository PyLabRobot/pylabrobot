import logging
from typing import Dict, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Plate

from .backend import PeristalticDispensingBackend

logger = logging.getLogger(__name__)


class PeristalticDispensingChatterboxBackend(PeristalticDispensingBackend):
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
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    logger.info("Priming peristaltic lines for plate '%s' (volume=%s, duration=%s).",
                plate.name, volume, duration)

  async def purge(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    logger.info("Purging peristaltic lines for plate '%s' (volume=%s, duration=%s).",
                plate.name, volume, duration)

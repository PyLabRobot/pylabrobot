import logging
from typing import Optional

from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.plate import Plate

from .backend import AutomatedRetrievalBackend

logger = logging.getLogger(__name__)


class AutomatedRetrievalChatterboxBackend(AutomatedRetrievalBackend):
  """Chatterbox backend for device-free testing."""

  async def fetch_plate_to_loading_tray(self, plate: Plate, tray_index: Optional[int] = None):
    logger.info("Fetching plate %s to loading tray %s.", plate.name, tray_index)

  async def store_plate(self, plate: Plate, site: PlateHolder, tray_index: Optional[int] = None):
    logger.info("Storing plate %s at site %s (tray %s).", plate.name, site.name, tray_index)

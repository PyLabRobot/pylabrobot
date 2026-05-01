import logging

from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.plate import Plate

from .backend import AutomatedRetrievalBackend

logger = logging.getLogger(__name__)


class AutomatedRetrievalChatterboxBackend(AutomatedRetrievalBackend):
  """Chatterbox backend for device-free testing."""

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    logger.info("Fetching plate %s to loading tray.", plate.name)

  async def store_plate(self, plate: Plate, site: PlateHolder):
    logger.info("Storing plate %s at site %s.", plate.name, site.name)

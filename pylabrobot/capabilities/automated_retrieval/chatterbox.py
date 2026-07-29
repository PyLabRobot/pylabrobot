import logging

from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource_stack import ResourceStack

from .backend import AutomatedRetrievalBackend, StackerBackend

logger = logging.getLogger(__name__)


class AutomatedRetrievalChatterboxBackend(AutomatedRetrievalBackend):
  """Chatterbox backend for device-free testing."""

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    logger.info("Fetching plate %s to loading tray.", plate.name)

  async def store_plate(self, plate: Plate, site: PlateHolder):
    logger.info("Storing plate %s at site %s.", plate.name, site.name)


class StackerChatterboxBackend(StackerBackend):
  """Chatterbox backend for device-free testing."""

  async def downstack(self, stack: ResourceStack):
    logger.info("Downstacking accessible plate from stack %s.", stack.name)

  async def upstack(self, stack: ResourceStack, plate: Plate):
    logger.info("Upstacking plate %s onto stack %s.", plate.name, stack.name)

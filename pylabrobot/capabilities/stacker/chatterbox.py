import logging

from pylabrobot.resources import Plate
from pylabrobot.resources.resource_stack import ResourceStack

from .backend import StackerBackend

logger = logging.getLogger(__name__)


class StackerChatterboxBackend(StackerBackend):
  """Chatterbox backend for device-free testing."""

  async def downstack(self, stack: ResourceStack):
    logger.info("Downstacking accessible plate from stack %s.", stack.name)

  async def upstack(self, stack: ResourceStack, plate: Plate):
    logger.info("Upstacking plate %s onto stack %s.", plate.name, stack.name)

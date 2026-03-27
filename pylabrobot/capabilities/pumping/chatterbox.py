import logging

from .backend import PumpBackend

logger = logging.getLogger(__name__)


class PumpChatterboxBackend(PumpBackend):
  """Chatterbox backend for device-free testing."""

  async def run_revolutions(self, num_revolutions: float):
    logger.info("Running %s revolutions.", num_revolutions)

  async def run_continuously(self, speed: float):
    logger.info("Running continuously at speed %s.", speed)

  async def halt(self):
    logger.info("Halting the pump.")

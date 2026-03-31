import logging

from .backend import SealerBackend

logger = logging.getLogger(__name__)


class SealerChatterboxBackend(SealerBackend):
  """Chatterbox backend for device-free testing."""

  async def seal(self, temperature: int, duration: float):
    logger.info("Sealing at %s C for %s seconds.", temperature, duration)

  async def open(self):
    logger.info("Opening sealer shuttle.")

  async def close(self):
    logger.info("Closing sealer shuttle.")

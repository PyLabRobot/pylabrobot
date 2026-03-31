import logging

from .backend import ShakerBackend

logger = logging.getLogger(__name__)


class ShakerChatterboxBackend(ShakerBackend):
  """Chatterbox backend for device-free testing."""

  @property
  def supports_locking(self) -> bool:
    return True

  async def start_shaking(self, speed: float):
    logger.info("Starting shaking at %s RPM.", speed)

  async def stop_shaking(self):
    logger.info("Stopping shaking.")

  async def lock_plate(self):
    logger.info("Locking plate.")

  async def unlock_plate(self):
    logger.info("Unlocking plate.")

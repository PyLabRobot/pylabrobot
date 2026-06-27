import asyncio
import logging

from .backend import HasContinuousShaking, ShakerBackend

logger = logging.getLogger(__name__)


class ShakerChatterboxBackend(ShakerBackend, HasContinuousShaking):
  """Chatterbox backend for device-free testing."""

  @property
  def supports_locking(self) -> bool:
    return True

  async def shake(self, speed: float, duration: float, backend_params=None):
    logger.info("Shaking at %s RPM for %s seconds.", speed, duration)
    await self.start_shaking(speed=speed)
    await asyncio.sleep(duration)
    await self.stop_shaking()

  async def start_shaking(self, speed: float):
    logger.info("Starting shaking at %s RPM.", speed)

  async def stop_shaking(self):
    logger.info("Stopping shaking.")

  async def lock_plate(self):
    logger.info("Locking plate.")

  async def unlock_plate(self):
    logger.info("Unlocking plate.")

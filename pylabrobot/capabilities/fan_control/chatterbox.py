import logging

from .backend import FanBackend

logger = logging.getLogger(__name__)


class FanChatterboxBackend(FanBackend):
  """Chatterbox backend for device-free testing."""

  async def turn_on(self, intensity: int) -> None:
    logger.info("Turning fan on at %s%%.", intensity)

  async def turn_off(self) -> None:
    logger.info("Turning fan off.")

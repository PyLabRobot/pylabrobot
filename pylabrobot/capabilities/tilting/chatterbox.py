import logging

from .backend import TilterBackend

logger = logging.getLogger(__name__)


class TilterChatterboxBackend(TilterBackend):
  """Chatterbox backend for device-free testing."""

  async def set_angle(self, angle: float):
    logger.info("Setting tilt angle to %s degrees.", angle)

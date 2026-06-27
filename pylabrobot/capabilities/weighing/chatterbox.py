import logging

from .backend import ScaleBackend

logger = logging.getLogger(__name__)


class ScaleChatterboxBackend(ScaleBackend):
  """Chatterbox backend for device-free testing."""

  def __init__(self):
    self._weight = 0.0

  async def zero(self):
    logger.info("Zeroing scale.")
    self._weight = 0.0

  async def tare(self):
    logger.info("Taring scale.")
    self._weight = 0.0

  async def read_weight(self) -> float:
    return self._weight

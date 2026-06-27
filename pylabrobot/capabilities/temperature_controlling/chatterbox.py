import logging

from .backend import TemperatureControllerBackend

logger = logging.getLogger(__name__)


class TemperatureControllerChatterboxBackend(TemperatureControllerBackend):
  """Chatterbox backend for device-free testing."""

  def __init__(self):
    self._temperature = 22.0

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    logger.info("Setting temperature to %s C.", temperature)
    self._temperature = temperature

  async def request_current_temperature(self) -> float:
    return self._temperature

  async def deactivate(self):
    logger.info("Deactivating temperature controller.")
    self._temperature = 22.0

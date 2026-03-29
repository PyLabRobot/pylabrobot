import logging

from .backend import HumidityControllerBackend

logger = logging.getLogger(__name__)


class HumidityControllerChatterboxBackend(HumidityControllerBackend):
  """Chatterbox backend for device-free testing."""

  def __init__(self):
    self._humidity = 0.5

  @property
  def supports_humidity_control(self) -> bool:
    return True

  async def set_humidity(self, humidity: float):
    logger.info("Setting humidity to %s.", humidity)
    self._humidity = humidity

  async def request_current_humidity(self) -> float:
    return self._humidity

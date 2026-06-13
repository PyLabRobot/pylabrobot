import logging
from typing import Optional

from pylabrobot.capabilities.automated_retrieval.backend import AutomatedRetrievalBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.humidity_controlling.backend import HumidityControllerBackend
from pylabrobot.capabilities.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.device import Driver
from pylabrobot.resources import Plate, PlateHolder

logger = logging.getLogger(__name__)


class TundraStoreChatterboxBackend(
  AutomatedRetrievalBackend,
  TemperatureControllerBackend,
  HumidityControllerBackend,
  Driver,
):
  """Device-free TundraStore backend that logs calls instead of talking to
  hardware. Useful for testing protocols and resource assignment offline."""

  def __init__(self, temperature: float = 4.0, humidity: float = 0.5):
    super().__init__()
    self._temperature = temperature
    self._humidity = humidity

  async def setup(self, backend_params: Optional[BackendParams] = None):
    logger.info("[tundrastore] setup")

  async def stop(self):
    logger.info("[tundrastore] stop")

  async def home(self):
    logger.info("[tundrastore] home")

  async def pick(self, stacker: int, slot: int, nest: int):
    logger.info("[tundrastore] pick stacker=%d slot=%d nest=%d", stacker, slot, nest)

  async def place(self, stacker: int, slot: int, nest: int):
    logger.info("[tundrastore] place stacker=%d slot=%d nest=%d", stacker, slot, nest)

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    logger.info("[tundrastore] fetch plate %s to loading tray", plate.name)

  async def store_plate(self, plate: Plate, site: PlateHolder):
    logger.info("[tundrastore] store plate %s at site %s", plate.name, site.name)

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def request_current_temperature(self) -> float:
    return self._temperature

  async def set_temperature(self, temperature: float):
    logger.info("[tundrastore] set temperature %.1f C", temperature)
    self._temperature = temperature

  async def deactivate(self):
    logger.info("[tundrastore] deactivate temperature control")

  @property
  def supports_humidity_control(self) -> bool:
    return False

  async def request_current_humidity(self) -> float:
    return self._humidity

  async def set_humidity(self, humidity: float):
    raise NotImplementedError("The TundraStore does not support active humidity control.")

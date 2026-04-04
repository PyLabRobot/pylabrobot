import logging
from typing import Optional, cast

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver

try:
  import ot_api

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e

logger = logging.getLogger(__name__)


class OpentronsTemperatureModuleDriver(Driver):
  """Driver for the Opentrons Temperature Module v2 via the Opentrons HTTP API.

  Owns the ot_api dependency check.  There is no persistent connection to manage,
  so ``setup``/``stop`` are lightweight.
  """

  def __init__(self, opentrons_id: str):
    super().__init__()
    self.opentrons_id = opentrons_id

    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )

  async def setup(self, backend_params: Optional[BackendParams] = None):
    pass

  async def stop(self):
    pass

  def serialize(self) -> dict:
    return {**super().serialize(), "opentrons_id": self.opentrons_id}


class OpentronsTemperatureModuleTemperatureBackend(TemperatureControllerBackend):
  """Translates ``TemperatureControllerBackend`` into Opentrons HTTP-API calls."""

  def __init__(self, driver: OpentronsTemperatureModuleDriver):
    self.driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    logger.info("[OT TempModule %s] setting temperature to %.1f C", self.driver.opentrons_id, temperature)
    ot_api.modules.temperature_module_set_temperature(
      celsius=temperature, module_id=self.driver.opentrons_id
    )

  async def deactivate(self):
    logger.info("[OT TempModule %s] deactivating", self.driver.opentrons_id)
    ot_api.modules.temperature_module_deactivate(module_id=self.driver.opentrons_id)

  async def request_current_temperature(self) -> float:
    modules = ot_api.modules.list_connected_modules()
    for module in modules:
      if module["id"] == self.driver.opentrons_id:
        temp = cast(float, module["data"]["currentTemperature"])
        logger.info("[OT TempModule %s] read temperature: actual=%.1f C", self.driver.opentrons_id, temp)
        return temp
    logger.error("[OT TempModule %s] module not found", self.driver.opentrons_id)
    raise RuntimeError(f"Module with id '{self.driver.opentrons_id}' not found")

from typing import cast

from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver

try:
  import ot_api

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


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

  async def setup(self):
    pass

  async def stop(self):
    pass

  def serialize(self) -> dict:
    return {**super().serialize(), "opentrons_id": self.opentrons_id}


class OpentronsTemperatureModuleTemperatureBackend(TemperatureControllerBackend):
  """Translates ``TemperatureControllerBackend`` into Opentrons HTTP-API calls."""

  def __init__(self, driver: OpentronsTemperatureModuleDriver):
    self._driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    ot_api.modules.temperature_module_set_temperature(
      celsius=temperature, module_id=self._driver.opentrons_id
    )

  async def deactivate(self):
    ot_api.modules.temperature_module_deactivate(module_id=self._driver.opentrons_id)

  async def get_current_temperature(self) -> float:
    modules = ot_api.modules.list_connected_modules()
    for module in modules:
      if module["id"] == self._driver.opentrons_id:
        return cast(float, module["data"]["currentTemperature"])
    raise RuntimeError(f"Module with id '{self._driver.opentrons_id}' not found")

import sys
from typing import cast

from pylabrobot.temperature_controlling.backend import TemperatureControllerBackend

PYTHON_VERSION = sys.version_info[:2]

if PYTHON_VERSION <= (3, 10):
  try:
    import ot_api
    USE_OT = True
  except ImportError:
    USE_OT = False
else:
  USE_OT = False


class OpentronsTemperatureModuleBackend(TemperatureControllerBackend):
  """ Opentrons temperature module backend. """

  def __init__(self, opentrons_id: str):
    """ Create a new Opentrons temperature module backend.

    Args:
      opentrons_id: Opentrons ID of the temperature module. Get it from
        `OpentronsTemperatureModuleBackend.list_connected_modules()`.
    """
    self.opentrons_id = opentrons_id

    if not USE_OT:
      raise RuntimeError("Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
                         " Only supported on Python 3.10 and below.")

  async def setup(self):
    await super().setup()

  async def stop(self):
    await self.deactivate()
    await super().stop()

  async def set_temperature(self, temperature: float):
    ot_api.modules.temperature_module_set_temperature(celsius=temperature,
                                                      module_id=self.opentrons_id)

  async def deactivate(self):
    ot_api.modules.temperature_module_deactivate(module_id=self.opentrons_id)

  async def get_current_temperature(self) -> float:
    modules = ot_api.modules.list_connected_modules()
    for module in modules:
      if module["id"] == self.opentrons_id:
        return cast(float, module["data"]["currentTemperature"])
    raise RuntimeError(f"Module with id '{self.opentrons_id}' not found")

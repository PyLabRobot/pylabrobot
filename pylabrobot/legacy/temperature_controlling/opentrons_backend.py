"""Legacy. Use pylabrobot.opentrons.OpentronsTemperatureModuleTemperatureBackend instead."""

from pylabrobot.legacy.temperature_controlling.backend import (
  TemperatureControllerBackend,
)
from pylabrobot.opentrons.temperature_module import (
  OpentronsTemperatureModuleDriver,
)
from pylabrobot.opentrons.temperature_module import (
  OpentronsTemperatureModuleTemperatureBackend as _NewBackend,
)


class OpentronsTemperatureModuleBackend(TemperatureControllerBackend):
  """Legacy. Use pylabrobot.opentrons.OpentronsTemperatureModuleTemperatureBackend instead."""

  @property
  def supports_active_cooling(self) -> bool:
    return self._backend.supports_active_cooling

  def __init__(self, opentrons_id: str):
    self.driver = OpentronsTemperatureModuleDriver(opentrons_id=opentrons_id)
    self._backend = _NewBackend(driver=self.driver)
    self.opentrons_id = opentrons_id

  async def setup(self):
    await self.driver.setup()
    await self._backend._on_setup()

  async def stop(self):
    await self._backend._on_stop()
    await self.driver.stop()

  def serialize(self) -> dict:
    return self.driver.serialize()

  async def set_temperature(self, temperature: float):
    await self._backend.set_temperature(temperature)

  async def deactivate(self):
    await self._backend.deactivate()

  async def get_current_temperature(self) -> float:
    return await self._backend.request_current_temperature()

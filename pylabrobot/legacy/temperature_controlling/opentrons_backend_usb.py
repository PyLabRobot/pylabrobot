"""Legacy. Use pylabrobot.opentrons.OpentronsTemperatureModuleUSBTemperatureBackend instead."""

from pylabrobot.legacy.temperature_controlling.backend import (
  TemperatureControllerBackend,
)
from pylabrobot.opentrons.temperature_module import (
  OpentronsTemperatureModuleUSBDriver,
)
from pylabrobot.opentrons.temperature_module import (
  OpentronsTemperatureModuleUSBTemperatureBackend as _NewBackend,
)


class OpentronsTemperatureModuleUSBBackend(TemperatureControllerBackend):
  """Legacy. Use pylabrobot.opentrons.OpentronsTemperatureModuleUSBTemperatureBackend instead."""

  @property
  def supports_active_cooling(self) -> bool:
    return self._backend.supports_active_cooling

  def __init__(self, port: str):
    self.driver = OpentronsTemperatureModuleUSBDriver(port=port)
    self._backend = _NewBackend(driver=self.driver)
    self.port = port

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

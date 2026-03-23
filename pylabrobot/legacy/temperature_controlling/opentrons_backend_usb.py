"""Legacy. Use pylabrobot.opentrons.OpentronsTemperatureModuleUSBBackend instead."""

from pylabrobot.legacy.temperature_controlling.backend import (
  TemperatureControllerBackend,
)
from pylabrobot.opentrons.temperature_module import (
  OpentronsTemperatureModuleUSBBackend as _NewBackend,
)


class OpentronsTemperatureModuleUSBBackend(TemperatureControllerBackend):
  """Legacy. Use pylabrobot.opentrons.OpentronsTemperatureModuleUSBBackend instead."""

  @property
  def supports_active_cooling(self) -> bool:
    return self._new.supports_active_cooling

  def __init__(self, port: str):
    self._new = _NewBackend(port=port)
    self.port = port

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def set_temperature(self, temperature: float):
    await self._new.set_temperature(temperature)

  async def deactivate(self):
    await self._new.deactivate()

  async def get_current_temperature(self) -> float:
    return await self._new.get_current_temperature()

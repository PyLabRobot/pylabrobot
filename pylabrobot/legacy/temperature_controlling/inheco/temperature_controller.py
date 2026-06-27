from pylabrobot.inheco import cpac
from pylabrobot.legacy.temperature_controlling.backend import TemperatureControllerBackend


class InhecoTemperatureControllerBackend(TemperatureControllerBackend):
  """Legacy. Use pylabrobot.inheco.cpac.InhecoTemperatureControllerBackend instead."""

  def __init__(self, index: int, control_box):
    self._new = cpac.InhecoTemperatureControllerBackend(index=index, control_box=control_box)

  @property
  def index(self) -> int:
    return self._new.index

  @property
  def interface(self):
    return self._new.interface

  @property
  def supports_active_cooling(self) -> bool:
    return self._new.supports_active_cooling

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def set_temperature(self, temperature: float):
    await self._new.set_temperature(temperature)

  async def get_current_temperature(self) -> float:
    return await self._new.request_current_temperature()

  async def deactivate(self):
    await self._new.deactivate()

  async def set_target_temperature(self, temperature: float):
    await self._new.set_target_temperature(temperature)

  async def start_temperature_control(self):
    return await self._new.start_temperature_control()

  async def stop_temperature_control(self):
    return await self._new.stop_temperature_control()

  async def get_device_info(self, info_type: int):
    return await self._new.request_device_info(info_type)

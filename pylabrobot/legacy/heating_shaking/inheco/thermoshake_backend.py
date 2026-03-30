"""Legacy. Use pylabrobot.inheco.thermoshake.InhecoThermoshakeBackend instead."""

from pylabrobot.inheco import thermoshake
from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend


class InhecoThermoshakeBackend(HeaterShakerBackend):
  """Legacy. Use pylabrobot.inheco.InhecoThermoshakeBackend instead."""

  def __init__(self, index: int, control_box):
    self._new = thermoshake.InhecoThermoshakeBackend(index=index, control_box=control_box)

  @property
  def index(self) -> int:
    return self._new.index

  @property
  def interface(self):
    return self._new.interface

  @property
  def supports_active_cooling(self) -> bool:
    return self._new.supports_active_cooling

  @property
  def supports_locking(self) -> bool:
    return self._new.supports_locking

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

  async def start_shaking(self, speed: float, shape: int = 0):
    await self._new.start_shaking(speed=speed, shape=shape)

  async def stop_shaking(self):
    return await self._new.stop_shaking()

  async def set_shaker_speed(self, speed: float):
    return await self._new.set_shaker_speed(speed)

  async def set_shaker_shape(self, shape: int):
    return await self._new.set_shaker_shape(shape)

  async def shake(self, speed: float, shape: int = 0):
    await self._new.shake(speed=speed, shape=shape)

  async def lock_plate(self):
    await self._new.lock_plate()

  async def unlock_plate(self):
    await self._new.unlock_plate()

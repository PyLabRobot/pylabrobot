"""Legacy. Use pylabrobot.qinstruments.BioShakeBackend instead."""

from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.qinstruments import bioshake


class BioShake(HeaterShakerBackend):
  """Legacy. Use pylabrobot.qinstruments.BioShakeBackend instead."""

  def __init__(self, port: str, timeout: int = 60):
    self._new = bioshake.BioShakeBackend(port=port, timeout=timeout)

  @property
  def supports_active_cooling(self) -> bool:
    return self._new.supports_active_cooling

  @property
  def supports_locking(self) -> bool:
    return self._new.supports_locking

  async def setup(self, skip_home: bool = False):
    await self._new.setup(skip_home=skip_home)

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def reset(self):
    await self._new.reset()

  async def home(self):
    await self._new.home()

  async def start_shaking(self, speed: float, acceleration: int = 0):
    await self._new.start_shaking(speed=speed, acceleration=acceleration)

  async def shake(self, speed: float, acceleration: int = 0):
    await self._new.start_shaking(speed=speed, acceleration=acceleration)

  async def stop_shaking(self, deceleration: int = 0):
    await self._new.stop_shaking(deceleration=deceleration)

  async def lock_plate(self):
    await self._new.lock_plate()

  async def unlock_plate(self):
    await self._new.unlock_plate()

  async def set_temperature(self, temperature: float):
    await self._new.set_temperature(temperature)

  async def get_current_temperature(self) -> float:
    return await self._new.get_current_temperature()

  async def deactivate(self):
    await self._new.deactivate()

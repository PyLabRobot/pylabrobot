"""Legacy. Use pylabrobot.qinstruments.BioShakeDriver instead."""

from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.qinstruments.bioshake import (
  BioShakeDriver,
  BioShakeShakerBackend,
  BioShakeTemperatureBackend,
)


class BioShake(HeaterShakerBackend):
  """Legacy. Use pylabrobot.qinstruments.BioShakeDriver instead."""

  def __init__(self, port: str, timeout: int = 60):
    self.driver = BioShakeDriver(port=port, timeout=timeout)
    self._shaker = BioShakeShakerBackend(self.driver)
    self._temp = BioShakeTemperatureBackend(self.driver)

  @property
  def supports_active_cooling(self) -> bool:
    return self._temp.supports_active_cooling

  @property
  def supports_locking(self) -> bool:
    return self._shaker.supports_locking

  async def setup(self, skip_home: bool = False):
    await self.driver.setup(skip_home=skip_home)

  async def stop(self):
    await self.driver.stop()

  def serialize(self) -> dict:
    return self.driver.serialize()

  async def reset(self):
    await self.driver.reset()

  async def home(self):
    await self.driver.home()

  async def start_shaking(self, speed: float, acceleration: int = 0):
    await self._shaker.start_shaking(speed=speed, acceleration=acceleration)

  async def shake(self, speed: float, acceleration: int = 0):
    await self._shaker.start_shaking(speed=speed, acceleration=acceleration)

  async def stop_shaking(self, deceleration: int = 0):
    await self._shaker.stop_shaking(deceleration=deceleration)

  async def lock_plate(self):
    await self._shaker.lock_plate()

  async def unlock_plate(self):
    await self._shaker.unlock_plate()

  async def set_temperature(self, temperature: float):
    await self._temp.set_temperature(temperature)

  async def get_current_temperature(self) -> float:
    return await self._temp.request_current_temperature()

  async def deactivate(self):
    await self._temp.deactivate()

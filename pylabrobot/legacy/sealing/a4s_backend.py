"""Legacy. Use pylabrobot.azenta.A4SDriver / A4SSealerBackend / A4STemperatureBackend instead."""

from pylabrobot.azenta.a4s import A4SDriver, A4SSealerBackend, A4STemperatureBackend
from pylabrobot.legacy.sealing.backend import SealerBackend


class A4SBackend(SealerBackend):
  """Legacy. Use pylabrobot.azenta.A4SDriver / A4SSealerBackend / A4STemperatureBackend instead."""

  def __init__(self, port: str, timeout: int = 20):
    self._driver = A4SDriver(port=port, timeout=timeout)
    self._sealer = A4SSealerBackend(self._driver)
    self._temperature = A4STemperatureBackend(self._driver)

  async def setup(self):
    await self._driver.setup()
    await self._sealer._on_setup()
    await self._temperature._on_setup()

  async def stop(self):
    await self._temperature._on_stop()
    await self._sealer._on_stop()
    await self._driver.stop()

  def serialize(self) -> dict:
    return self._driver.serialize()

  async def seal(self, temperature: int, duration: float):
    await self._sealer.seal(temperature=temperature, duration=duration)

  async def open(self):
    return await self._sealer.open()

  async def close(self):
    return await self._sealer.close()

  async def set_temperature(self, temperature: float):
    await self._temperature.set_temperature(temperature=temperature)

  async def get_temperature(self) -> float:
    return await self._temperature.get_current_temperature()

  async def set_heater(self, on: bool):
    await self._driver.set_heater(on=on)

  async def system_reset(self):
    await self._driver.system_reset()

  async def set_time(self, seconds: float):
    await self._driver.set_time(seconds=seconds)

  async def get_remaining_time(self) -> int:
    return await self._driver.get_remaining_time()

  async def get_status(self):
    return await self._driver.get_status()

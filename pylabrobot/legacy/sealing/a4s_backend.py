"""Legacy. Use pylabrobot.azenta.A4SBackend instead."""

from pylabrobot.azenta import a4s
from pylabrobot.legacy.sealing.backend import SealerBackend


class A4SBackend(SealerBackend):
  """Legacy. Use pylabrobot.azenta.A4SBackend instead."""

  def __init__(self, port: str, timeout: int = 20):
    self._new = a4s.A4SBackend(port=port, timeout=timeout)

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def seal(self, temperature: int, duration: float):
    await self._new.seal(temperature=temperature, duration=duration)

  async def open(self):
    return await self._new.open()

  async def close(self):
    return await self._new.close()

  async def set_temperature(self, temperature: float):
    await self._new.set_temperature(temperature=temperature)

  async def get_temperature(self) -> float:
    return await self._new.get_current_temperature()

  async def set_heater(self, on: bool):
    await self._new.set_heater(on=on)

  async def system_reset(self):
    await self._new.system_reset()

  async def set_time(self, seconds: float):
    await self._new.set_time(seconds=seconds)

  async def get_remaining_time(self) -> int:
    return await self._new.get_remaining_time()

  async def get_status(self):
    return await self._new.get_status()

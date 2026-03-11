"""Legacy. Use pylabrobot.azenta.A4S instead."""

from pylabrobot.legacy.machines import Machine

from .backend import SealerBackend


class Sealer(Machine):
  """Legacy. Use pylabrobot.azenta.A4S instead."""

  def __init__(self, backend: SealerBackend):
    super().__init__(backend=backend)
    self._backend: SealerBackend = backend

  async def seal(self, temperature: int, duration: float):
    return await self._backend.seal(temperature=temperature, duration=duration)

  async def open(self):
    return await self._backend.open()

  async def close(self):
    return await self._backend.close()

  async def set_temperature(self, temperature: float):
    return await self._backend.set_temperature(temperature=temperature)

  async def get_temperature(self) -> float:
    return await self._backend.get_temperature()

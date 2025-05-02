from pylabrobot.machines import Machine

from .backend import SealerBackend


class Sealer(Machine):
  """A microplate sealer"""

  def __init__(self, backend: SealerBackend):
    super().__init__(backend=backend)
    self.backend: SealerBackend = backend  # fix type

  async def seal(self, temperature: int, duration: float):
    return await self.backend.seal(temperature=temperature, duration=duration)

  async def open(self):
    return await self.backend.open()

  async def close(self):
    return await self.backend.close()
  
  async def set_temperature(self, temperature: int):
    """Set the temperature of the sealer in degrees Celsius."""
    return await self.backend.set_temperature(temperature=temperature)

  async def get_temperature(self) -> float:
    """Get the current temperature of the sealer in degrees Celsius."""
    return await self.backend.get_temperature() 

from typing import Optional

from pylabrobot.machines import Machine

from .backend import SealerBackend


class Sealer(Machine):
  """A microplate sealer"""

  def __init__(self, backend: SealerBackend):
    super().__init__(backend=backend)
    self.backend: SealerBackend = backend  # fix type

  async def seal(self, temperature: int, duration: float):
    await self.backend.seal(temperature=temperature, duration=duration)

  async def open_shuttle(self):
    await self.backend.open_shuttle()

  async def close_shuttle(self):
    await self.backend.close_shuttle()

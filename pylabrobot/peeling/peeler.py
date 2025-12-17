from pylabrobot.machines import Machine

from .backend import PeelerBackend


class Peeler(Machine):
  """A microplate peeler"""

  def __init__(self, backend: PeelerBackend):
    super().__init__(backend=backend)
    self.backend: PeelerBackend = backend

  async def peel(self):
    return await self.backend.peel()

  async def restart(self):
    return await self.backend.restart()
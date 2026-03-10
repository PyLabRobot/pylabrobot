from pylabrobot.machines import Machine

from .backend import PeelerBackend


class Peeler(Machine):
  """A microplate peeler"""

  def __init__(self, backend: PeelerBackend):
    super().__init__(backend=backend)
    self.backend: PeelerBackend = backend

  async def peel(self, **backend_kwargs):
    return await self.backend.peel(**backend_kwargs)

  async def restart(self, **backend_kwargs):
    return await self.backend.restart(**backend_kwargs)

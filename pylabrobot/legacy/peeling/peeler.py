"""Legacy. Use pylabrobot.azenta.XPeel instead."""

from pylabrobot.machines import Machine

from .backend import PeelerBackend


class Peeler(Machine):
  """Legacy. Use pylabrobot.azenta.XPeel instead."""

  def __init__(self, backend: PeelerBackend):
    super().__init__(backend=backend)
    self._backend: PeelerBackend = backend

  async def peel(self, **backend_kwargs):
    return await self._backend.peel(**backend_kwargs)

  async def restart(self, **backend_kwargs):
    return await self._backend.restart(**backend_kwargs)

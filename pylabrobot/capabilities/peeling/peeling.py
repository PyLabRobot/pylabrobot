from pylabrobot.capabilities.capability import Capability

from .backend import PeelerBackend


class PeelingCapability(Capability):
  """Peeling capability."""

  def __init__(self, backend: PeelerBackend):
    super().__init__(backend=backend)
    self.backend: PeelerBackend = backend

  async def peel(self, **backend_kwargs):
    """Run an automated de-seal cycle."""
    return await self.backend.peel(**backend_kwargs)

  async def restart(self, **backend_kwargs):
    """Restart the peeler."""
    return await self.backend.restart(**backend_kwargs)

  async def _on_stop(self):
    await super()._on_stop()

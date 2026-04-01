from typing import Optional

from pylabrobot.capabilities.capability import Capability
from pylabrobot.serializer import SerializableMixin

from .backend import PeelerBackend


class Peeler(Capability):
  """Peeling capability.

  See :doc:`/user_guide/capabilities/peeling` for a walkthrough.
  """

  def __init__(self, backend: PeelerBackend):
    super().__init__(backend=backend)
    self.backend: PeelerBackend = backend

  async def peel(self, backend_params: Optional[SerializableMixin] = None):
    """Run an automated de-seal cycle."""
    return await self.backend.peel(backend_params=backend_params)

  async def restart(self, backend_params: Optional[SerializableMixin] = None):
    """Restart the peeler."""
    return await self.backend.restart(backend_params=backend_params)

  async def _on_stop(self):
    await super()._on_stop()

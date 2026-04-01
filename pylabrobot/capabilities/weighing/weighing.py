from pylabrobot.capabilities.capability import Capability

from .backend import ScaleBackend


class Scale(Capability):
  """Weighing capability.

  See :doc:`/user_guide/capabilities/weighing` for a walkthrough.
  """

  def __init__(self, backend: ScaleBackend):
    super().__init__(backend=backend)
    self.backend: ScaleBackend = backend

  async def zero(self):
    await self.backend.zero()

  async def tare(self):
    await self.backend.tare()

  async def read_weight(self) -> float:
    return await self.backend.read_weight()

  async def _on_stop(self):
    await super()._on_stop()

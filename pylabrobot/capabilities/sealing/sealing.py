from pylabrobot.capabilities.capability import Capability

from .backend import SealerBackend


class Sealer(Capability):
  """Sealing capability.

  See :doc:`/user_guide/capabilities/sealing` for a walkthrough.
  """

  def __init__(self, backend: SealerBackend):
    super().__init__(backend=backend)
    self.backend: SealerBackend = backend

  async def seal(self, temperature: int, duration: float):
    await self.backend.seal(temperature=temperature, duration=duration)

  async def open(self):
    await self.backend.open()

  async def close(self):
    await self.backend.close()

  async def _on_stop(self):
    await super()._on_stop()

from pylabrobot.machine import MachineFrontend
from pylabrobot.scales.scale_backend import ScaleBackend


class Scale(MachineFrontend):
  """ Base class for a scale """

  def __init__(self, backend: ScaleBackend):
    super().__init__(backend=backend)
    self.backend: ScaleBackend = backend # fix type

  async def tare(self):
    """ Tare the scale """
    await self.backend.tare()

  async def zero(self):
    """ Zero the scale """
    await self.backend.zero()

  async def get_weight(self) -> float:
    """ Get the weight in grams """
    return await self.backend.get_weight()

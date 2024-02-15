from pylabrobot.machine import Machine
from pylabrobot.scales.scale_backend import ScaleBackend


class Scale(Machine):
  """ Base class for a scale """

  def __init__(self, backend: ScaleBackend, name: str = "scale"):
    super().__init__(
      name=name,
      backend=backend,
      model=backend.__class__.__name__,

      # TODO:
      size_x=0,
      size_y=0,
      size_z=0,
      category="scale",
    )
    self.backend: ScaleBackend = backend # fix type

  async def tare(self, **backend_kwargs):
    """ Tare the scale """
    await self.backend.tare(**backend_kwargs)

  async def zero(self, **backend_kwargs):
    """ Zero the scale """
    await self.backend.zero(**backend_kwargs)

  async def get_weight(self, **backend_kwargs) -> float:
    """ Get the weight in grams """
    return await self.backend.get_weight(**backend_kwargs)

from typing import Optional

from pylabrobot.machine import Machine
from pylabrobot.scales.scale_backend import ScaleBackend


class Scale(Machine):
  """ Base class for a scale """

  def __init__(
      self,
      backend: ScaleBackend,
      name: str,
      size_x: float,
      size_y: float,
      size_z: float,

      cateogry: str = "scale",
      model: Optional[str] = None,
    ):
    super().__init__(
      name=name,
      backend=backend,

      size_x=size_x,
      size_y=size_y,
      size_z=size_z,

      category=cateogry,
      model=model,
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

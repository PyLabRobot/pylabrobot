from typing import Optional

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Resource, Rotation
from pylabrobot.scales.scale_backend import ScaleBackend


class Scale(Resource, Machine):
  """Base class for a scale"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ScaleBackend,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Machine.__init__(self, backend=backend)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )
    self.backend: ScaleBackend = backend  # fix type

  async def tare(self, **backend_kwargs):
    """Tare the scale"""
    await self.backend.tare(**backend_kwargs)

  async def zero(self, **backend_kwargs):
    """Zero the scale"""
    await self.backend.zero(**backend_kwargs)

  async def get_weight(self, **backend_kwargs) -> float:
    """Get the weight in grams"""
    return await self.backend.get_weight(**backend_kwargs)

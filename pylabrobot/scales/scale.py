from typing import Optional
import warnings

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
    warnings.warn(
        "Scale frontend `scale.get_weight is deprecated and will be removed in 2026-06"
        "use `scale.read_weight instead",
        DeprecationWarning,
        stacklevel=2,
      )
    return await self.backend.get_weight(**backend_kwargs)

  async def read_weight(self, **backend_kwargs) -> float:
    """Get the weight in grams"""
    return await self.backend.read_weight(**backend_kwargs)

from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource_holder import ResourceHolder

from .backend import LoadingTrayBackend


class LoadingTray(Capability, ResourceHolder):
  """Loading tray capability that can open/close and hold a resource."""

  def __init__(
    self,
    backend: LoadingTrayBackend,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    child_location: Coordinate = Coordinate.zero(),
    category: str = "loading_tray",
    model: Optional[str] = None,
  ):
    Capability.__init__(self, backend=backend)
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    self.backend: LoadingTrayBackend = backend

  @need_capability_ready
  async def open(self, backend_params: Optional[BackendParams] = None):
    await self.backend.open(backend_params=backend_params)

  @need_capability_ready
  async def close(self, backend_params: Optional[BackendParams] = None):
    await self.backend.close(backend_params=backend_params)

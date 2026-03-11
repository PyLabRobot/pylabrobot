from typing import Optional

from pylabrobot.capabilities.shaking import ShakingCapability
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .backend import ShakerBackend


class Shaker(ResourceHolder, Machine):
  """Legacy. Use a vendor-specific machine with ShakingCapability instead."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ShakerBackend,
    child_location: Coordinate,
    category: str = "shaker",
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
      child_location=child_location,
    )
    Machine.__init__(self, backend=backend)
    self.backend: ShakerBackend = backend
    self._cap = ShakingCapability(backend=backend)

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self._cap._on_setup()

  async def shake(self, speed: float, duration: Optional[float] = None, **backend_kwargs):
    return await self._cap.shake(speed=speed, duration=duration)

  async def stop_shaking(self, **backend_kwargs):
    await self._cap.stop_shaking()

  async def lock_plate(self, **backend_kwargs):
    await self._cap.lock_plate()

  async def unlock_plate(self, **backend_kwargs):
    await self._cap.unlock_plate()

  async def stop(self):
    await self._cap._on_stop()
    await super().stop()

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }

import asyncio
from typing import Optional

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .backend import ShakerBackend


class Shaker(ResourceHolder, Machine):
  """A shaker machine"""

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
    self.backend: ShakerBackend = backend  # fix type

  async def shake(self, speed: float, duration: Optional[float] = None, **backend_kwargs):
    """Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
      duration: Duration of shaking in seconds. If None, shake indefinitely (and return immediately).
    """
    if self.backend.supports_locking:
      await self.backend.lock_plate()
    await self.backend.shake(speed=speed, **backend_kwargs)

    if duration is None:
      return

    await asyncio.sleep(duration)
    await self.backend.stop_shaking()
    if self.backend.supports_locking:
      await self.backend.unlock_plate()

  async def stop_shaking(self, **backend_kwargs):
    await self.backend.stop_shaking(**backend_kwargs)

  async def lock_plate(self, **backend_kwargs):
    await self.backend.lock_plate(**backend_kwargs)

  async def unlock_plate(self, **backend_kwargs):
    await self.backend.unlock_plate(**backend_kwargs)

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }

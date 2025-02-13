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

  async def shake(self, speed: float, duration: Optional[float] = None):
    """Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
      duration: Duration of shaking in seconds. If None, shake indefinitely.
    """
    await self.backend.lock_plate()
    await self.backend.shake(speed=speed)

    if duration is None:
      return

    await asyncio.sleep(duration)
    await self.backend.stop_shaking()
    await self.backend.unlock_plate()

  async def stop_shaking(self):
    await self.backend.stop_shaking()

  async def lock_plate(self):
    await self.backend.lock_plate()

  async def unlock_plate(self):
    await self.backend.unlock_plate()

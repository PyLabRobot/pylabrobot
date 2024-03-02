import asyncio
from typing import Optional

from pylabrobot.machine import Machine

from .backend import ShakerBackend


class Shaker(Machine):
  """ A shaker machine """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ShakerBackend,
    category: str = "shaker",
    model: Optional[str] = None
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      backend=backend,
      category=category,
      model=model
    )
    self.backend: ShakerBackend = backend  # fix type

  async def shake(self, speed: float, duration: Optional[float] = None):
    """ Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
      duration: Duration of shaking in seconds. If None, shake indefinitely.
    """

    await self.backend.shake(speed=speed)

    if duration is None:
      return

    await asyncio.sleep(duration)
    await self.backend.stop_shaking()

  async def stop_shaking(self):
    """ Stop shaking the shaker """

    await self.backend.stop_shaking()

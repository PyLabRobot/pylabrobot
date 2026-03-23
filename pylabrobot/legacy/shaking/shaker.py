from typing import Optional

from pylabrobot.capabilities.shaking import ShakingCapability
from pylabrobot.capabilities.shaking import ShakerBackend as _NewShakerBackend
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .backend import ShakerBackend


class _ShakingAdapter(_NewShakerBackend):
  def __init__(self, legacy: ShakerBackend):
    self._legacy = legacy
  async def setup(self): pass
  async def stop(self): pass
  async def start_shaking(self, speed: float):
    await self._legacy.start_shaking(speed)
  async def stop_shaking(self):
    await self._legacy.stop_shaking()
  @property
  def supports_locking(self) -> bool:
    return self._legacy.supports_locking
  async def lock_plate(self):
    await self._legacy.lock_plate()
  async def unlock_plate(self):
    await self._legacy.unlock_plate()


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
    self._shaking_cap = ShakingCapability(backend=_ShakingAdapter(backend))

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self._shaking_cap._on_setup()

  async def shake(self, speed: float, duration: Optional[float] = None, **backend_kwargs):
    return await self._shaking_cap.shake(speed=speed, duration=duration)

  async def stop_shaking(self, **backend_kwargs):
    await self._shaking_cap.stop_shaking()

  async def lock_plate(self, **backend_kwargs):
    await self._shaking_cap.lock_plate()

  async def unlock_plate(self, **backend_kwargs):
    await self._shaking_cap.unlock_plate()

  async def stop(self):
    await self._shaking_cap._on_stop()
    await super().stop()

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }

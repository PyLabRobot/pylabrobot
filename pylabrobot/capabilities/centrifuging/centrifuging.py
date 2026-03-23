from typing import Optional, Tuple

from pylabrobot.capabilities.capability import Capability
from pylabrobot.resources import ResourceHolder

from .backend import CentrifugeBackend


class CentrifugingCapability(Capability):
  """Centrifuging capability."""

  def __init__(
    self,
    backend: CentrifugeBackend,
    buckets: Tuple[ResourceHolder, ResourceHolder],
  ):
    super().__init__(backend=backend)
    self.backend: CentrifugeBackend = backend
    self._door_open = False
    self._at_bucket: Optional[ResourceHolder] = None
    self.bucket1, self.bucket2 = buckets

  async def open_door(self) -> None:
    await self.backend.open_door()
    self._door_open = True

  async def close_door(self) -> None:
    await self.backend.close_door()
    self._door_open = False

  @property
  def door_open(self) -> bool:
    return self._door_open

  async def lock_door(self) -> None:
    await self.backend.lock_door()

  async def unlock_door(self) -> None:
    await self.backend.unlock_door()

  async def lock_bucket(self) -> None:
    await self.backend.lock_bucket()

  async def unlock_bucket(self) -> None:
    await self.backend.unlock_bucket()

  async def go_to_bucket1(self, **backend_kwargs) -> None:
    await self.backend.go_to_bucket1(**backend_kwargs)
    self._at_bucket = self.bucket1

  async def go_to_bucket2(self, **backend_kwargs) -> None:
    await self.backend.go_to_bucket2(**backend_kwargs)
    self._at_bucket = self.bucket2

  async def spin(self, g: float, duration: float, **backend_kwargs) -> None:
    """Start a spin cycle.

    Args:
      g: The g-force to spin at.
      duration: The duration of the spin in seconds (time at speed).
    """
    await self.backend.spin(g=g, duration=duration, **backend_kwargs)
    self._at_bucket = None

  @property
  def at_bucket(self) -> Optional[ResourceHolder]:
    return self._at_bucket

from typing import Optional, Tuple

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.resources import ResourceHolder
from pylabrobot.serializer import SerializableMixin

from .backend import CentrifugeBackend


class Centrifuge(Capability):
  """Centrifuging capability.

  See :doc:`/user_guide/capabilities/centrifuging` for a walkthrough.
  """

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

  @need_capability_ready
  async def open_door(self) -> None:
    await self.backend.open_door()
    self._door_open = True

  @need_capability_ready
  async def close_door(self) -> None:
    await self.backend.close_door()
    self._door_open = False

  @property
  def door_open(self) -> bool:
    return self._door_open

  @need_capability_ready
  async def lock_door(self) -> None:
    await self.backend.lock_door()

  @need_capability_ready
  async def unlock_door(self) -> None:
    await self.backend.unlock_door()

  @need_capability_ready
  async def lock_bucket(self) -> None:
    await self.backend.lock_bucket()

  @need_capability_ready
  async def unlock_bucket(self) -> None:
    await self.backend.unlock_bucket()

  @need_capability_ready
  async def go_to_bucket1(self) -> None:
    await self.backend.go_to_bucket1()
    self._at_bucket = self.bucket1

  @need_capability_ready
  async def go_to_bucket2(self) -> None:
    await self.backend.go_to_bucket2()
    self._at_bucket = self.bucket2

  @need_capability_ready
  async def spin(
    self,
    g: float,
    duration: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Start a spin cycle.

    Args:
      g: The g-force to spin at.
      duration: The duration of the spin in seconds (time at speed).
      backend_params: Vendor-specific parameters.
    """
    await self.backend.spin(g=g, duration=duration, backend_params=backend_params)
    self._at_bucket = None

  @property
  def at_bucket(self) -> Optional[ResourceHolder]:
    return self._at_bucket

import asyncio
from typing import Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready

from .backend import ShakerBackend


class Shaker(Capability):
  """Shaking capability.

  See :doc:`/user_guide/capabilities/shaking` for a walkthrough.
  """

  def __init__(self, backend: ShakerBackend):
    super().__init__(backend=backend)
    self.backend: ShakerBackend = backend

  @need_capability_ready
  async def shake(self, speed: float, duration: Optional[float] = None):
    """Shake at the given speed.

    Args:
      speed: Speed in RPM.
      duration: Duration in seconds. If None, shake indefinitely (return immediately).
    """
    if self.backend.supports_locking:
      await self.backend.lock_plate()
    await self.backend.start_shaking(speed=speed)

    if duration is None:
      return

    await asyncio.sleep(duration)
    await self.backend.stop_shaking()
    if self.backend.supports_locking:
      await self.backend.unlock_plate()

  @need_capability_ready
  async def stop_shaking(self):
    await self.backend.stop_shaking()

  @need_capability_ready
  async def lock_plate(self):
    await self.backend.lock_plate()

  @need_capability_ready
  async def unlock_plate(self):
    await self.backend.unlock_plate()

  async def _on_stop(self):
    if self._setup_finished:
      await self.backend.stop_shaking()
    await super()._on_stop()

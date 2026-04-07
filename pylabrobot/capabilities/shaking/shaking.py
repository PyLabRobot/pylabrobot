from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready

from .backend import HasContinuousShaking, ShakerBackend


class Shaker(Capability):
  """Shaking capability.

  See :doc:`/user_guide/capabilities/shaking` for a walkthrough.
  """

  def __init__(self, backend: ShakerBackend):
    super().__init__(backend=backend)
    self.backend: ShakerBackend = backend

  @need_capability_ready
  async def shake(
    self,
    speed: float,
    duration: float,
    backend_params: Optional[BackendParams] = None,
  ):
    """Shake at the given speed for the given duration.

    Args:
      speed: Speed in RPM.
      duration: Duration in seconds.
      backend_params: Backend-specific parameters.
    """
    if self.backend.supports_locking:
      await self.backend.lock_plate()
    try:
      await self.backend.shake(speed=speed, duration=duration, backend_params=backend_params)
    finally:
      if self.backend.supports_locking:
        await self.backend.unlock_plate()

  @need_capability_ready
  async def start_shaking(self, speed: float):
    """Start shaking indefinitely.

    Only available if the backend supports continuous shaking
    (implements :class:`~pylabrobot.capabilities.shaking.backend.HasContinuousShaking`).

    Args:
      speed: Speed in RPM.
    """
    if not isinstance(self.backend, HasContinuousShaking):
      raise NotImplementedError(
        f"{type(self.backend).__name__} does not support continuous shaking. "
        "Use shake(speed, duration) instead."
      )
    if self.backend.supports_locking:
      await self.backend.lock_plate()
    await self.backend.start_shaking(speed=speed)

  @need_capability_ready
  async def stop_shaking(self):
    """Stop shaking.

    Only available if the backend supports continuous shaking
    (implements :class:`~pylabrobot.capabilities.shaking.backend.HasContinuousShaking`).
    """
    if not isinstance(self.backend, HasContinuousShaking):
      raise NotImplementedError(
        f"{type(self.backend).__name__} does not support continuous shaking."
      )
    await self.backend.stop_shaking()
    if self.backend.supports_locking:
      await self.backend.unlock_plate()

  @need_capability_ready
  async def lock_plate(self):
    await self.backend.lock_plate()

  @need_capability_ready
  async def unlock_plate(self):
    await self.backend.unlock_plate()

  async def _on_stop(self):
    if self._setup_finished and isinstance(self.backend, HasContinuousShaking):
      await self.backend.stop_shaking()
    await super()._on_stop()

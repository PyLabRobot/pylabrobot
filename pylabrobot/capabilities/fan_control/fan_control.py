import asyncio

from pylabrobot.capabilities.capability import Capability

from .backend import FanBackend


class Fan(Capability):
  """Fan control capability.

  See :doc:`/user_guide/capabilities/fan-control` for a walkthrough.
  """

  def __init__(self, backend: FanBackend):
    super().__init__(backend=backend)
    self.backend: FanBackend = backend

  async def turn_on(self, intensity: int, duration=None):
    """Run the fan.

    Args:
      intensity: integer percent between 0 and 100.
      duration: time to run the fan for in seconds. If None, run until turn_off is called.
    """
    await self.backend.turn_on(intensity=intensity)
    if duration is not None:
      await asyncio.sleep(duration)
      await self.backend.turn_off()

  async def turn_off(self):
    """Turn the fan off."""
    await self.backend.turn_off()

  async def _on_stop(self):
    await self.backend.turn_off()
    await super()._on_stop()

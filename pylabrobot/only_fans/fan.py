import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.machines.machine import Machine

from .backend import FanBackend


class Fan(Machine):
  """
  Front end for Fans.
  """

  def __init__(self, backend: FanBackend):
    super().__init__(backend=backend)
    self.backend: FanBackend = backend  # fix type

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)

    async def cleanup():
      await self.backend.turn_off()

    stack.push_shielded_async_callback(cleanup)

  async def turn_on(self, intensity: int, duration=None):
    """Run the fan

    Args:
      intensity: integer percent between 0 and 100
      duration: time to run the fan for. If None, run until `turn_off` is called.
    """

    await self.backend.turn_on(intensity=intensity)

    if duration is not None:
      await anyio.sleep(duration)
      await self.backend.turn_off()

  async def turn_off(self):
    """Turn the fan off, but do not close the connection."""
    await self.backend.turn_off()

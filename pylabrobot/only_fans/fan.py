import asyncio

from pylabrobot.machines.machine import Machine

from .backend import FanBackend


class Fan(Machine):
  """
  Front end for Fans.
  """

  def __init__(self, backend: FanBackend, name):
    """ Initialize a Fan.

    Args:
      backend: Backend to use.
    """

    super().__init__(
      name=name,
      size_x=1830,
      size_y=900,
      size_z=400,
      backend=backend,
      category="fan",
    )

    self.backend: FanBackend = backend # fix type

  async def stop(self):
    """ Stop the fan and close the connection. """
    await self.backend.turn_off()
    await self.backend.stop()

  async def turn_on(self, intensity: int, duration=None):
    """ Run the fan

    Args:
      intensity: integer percent between 0 and 100
      duration: time to run the fan for. If None, run until `turn_off` is called.
    """

    await self.backend.turn_on(intensity=intensity)

    if duration is not None:
      await asyncio.sleep(duration)
      await self.backend.turn_off()

  async def turn_off(self):
    """ Turn the fan off, but do not close the connection. """
    await self.backend.turn_off()

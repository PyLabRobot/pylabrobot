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

  async def setup(self):
    """ Intialize and set up the fan. """
    await self.backend.setup()

  async def stop(self):
    """ Stop the fan and close the connection. """
    await self.backend.stop()

  async def turn_on_fan(self,speed,duration=None):
    """ Intialize and set up the fan at speed, where speed is an integer percent between 0 and 100
    """

    await self.backend.turn_on_fan(speed)

    if duration is not None:
      await asyncio.sleep(duration)
      await self.backend.stop_fan()

  async def stop_fan(self):
    """ Stop the fan """
    await self.backend.stop_fan()

import asyncio
import time
from typing import Optional

from pylabrobot.machines.machine import Machine

from .backend import ThermocyclerBackend


class Thermocycler(Machine):
  """ Thermocycler. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ThermocyclerBackend,
    category: str = "thermocycler",
    model: Optional[str] = None
  ):
    super().__init__(name, size_x, size_y, size_z, backend, category, model)
    self.backend: ThermocyclerBackend = backend  # fix type
    self.target_temperature: Optional[float] = None

  async def setup(self):
    """ Setup the thermocycler. """
    return await self.backend.setup()

  async def stop(self):
    """ Stop the thermocycler. """
    return await self.backend.stop()

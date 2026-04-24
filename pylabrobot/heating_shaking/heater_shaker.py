from typing import Optional

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.shaking import Shaker
from pylabrobot.temperature_controlling import TemperatureController

from .backend import HeaterShakerBackend


class HeaterShaker(TemperatureController, Shaker):
  """A heating and shaking machine"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: HeaterShakerBackend,
    child_location: Coordinate,
    category: str = "heating_shaking",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      backend=backend,
      child_location=child_location,
      category=category,
      model=model,
    )
    self.backend: HeaterShakerBackend = backend  # fix type

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)

    async def cleanup():
      await self.deactivate()
      await self.stop_shaking()

    stack.push_shielded_async_callback(cleanup)

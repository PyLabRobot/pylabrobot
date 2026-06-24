from typing import Optional

from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.legacy.shaking import Shaker
from pylabrobot.legacy.shaking.shaker import _NewShaker, _ShakingAdapter
from pylabrobot.legacy.temperature_controlling import TemperatureController
from pylabrobot.resources.coordinate import Coordinate

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
    self._shaking_cap = _NewShaker(backend=_ShakingAdapter(backend))

  async def setup(self, **backend_kwargs):
    await Machine.setup(self, **backend_kwargs)
    await self._tc_cap._on_setup()
    await self._shaking_cap._on_setup()

  async def stop(self):
    await self.deactivate()
    await self.stop_shaking()
    await Machine.stop(self)

from typing import Optional
from functools import partial

from pylabrobot.concurrency import attempt_in_order
from pylabrobot.legacy.shaking import Shaker
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
    self._initialize_shaking_state()

  async def _pre_backend_stop(self) -> None:
    """Deactivate temperature, then force stop and unlock scoped shaking."""
    await attempt_in_order(
      self.deactivate, partial(Shaker._pre_backend_stop, self, force_stop=True)
    )

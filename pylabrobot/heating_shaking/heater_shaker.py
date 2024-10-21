from typing import Optional

from pylabrobot.temperature_controlling import TemperatureController
from pylabrobot.shaking import Shaker

from .backend import HeaterShakerBackend


class HeaterShaker(TemperatureController, Shaker):
  """ A heating and shaking machine """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: HeaterShakerBackend,
    category: str = "heating_shaking",
    model: Optional[str] = None
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      backend=backend,
      category=category,
      model=model
    )
    self.backend: HeaterShakerBackend = backend  # fix type

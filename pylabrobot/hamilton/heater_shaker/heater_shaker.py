from typing import Optional

from pylabrobot.capabilities.shaking import ShakingCapability
from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateHolder

from .backend import HamiltonHeaterShakerBackend
from .box import HamiltonHeaterShakerInterface


class HamiltonHeaterShaker(PlateHolder, Device):
  """Hamilton Heater Shaker: combined temperature control and shaking."""

  def __init__(
    self,
    name: str,
    backend: HamiltonHeaterShakerBackend,
    size_x: float = 146.2,
    size_y: float = 103.6,
    size_z: float = 74.11,
    child_location: Coordinate = Coordinate(x=10, y=13, z=74.24),
    pedestal_size_z: float = 0,
    category: str = "heating_shaking",
    model: Optional[str] = None,
  ):
    raise NotImplementedError("HamiltonHeaterShaker resource definition is not verified.")
    PlateHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      pedestal_size_z=pedestal_size_z,
      category=category,
      model=model,
    )
    Device.__init__(self, backend=backend)
    self._backend: HamiltonHeaterShakerBackend = backend
    self.tc = TemperatureControlCapability(backend=backend)
    self.shaker = ShakingCapability(backend=backend)
    self._capabilities = [self.tc, self.shaker]

  def serialize(self) -> dict:
    return {
      **Device.serialize(self),
      **PlateHolder.serialize(self),
    }

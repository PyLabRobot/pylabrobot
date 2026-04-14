from typing import Optional, Union

from pylabrobot.capabilities.shaking import Shaker
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateHolder

from .backend import HamiltonHeaterShakerBackend
from .box import HamiltonHeaterShakerBox

TYPE_CHECKING = False
if TYPE_CHECKING:
  from pylabrobot.hamilton.liquid_handlers.star.driver import STARDriver


class HamiltonHeaterShaker(PlateHolder, Device):
  """Hamilton Heater Shaker: combined temperature control and shaking."""

  def __init__(
    self,
    name: str,
    index: int,
    driver: Union[HamiltonHeaterShakerBox, "STARDriver"],
    size_x: float = 146.2,
    size_y: float = 103.6,
    size_z: float = 74.11,
    child_location: Coordinate = Coordinate(x=9.66, y=9.22, z=74.11),
    pedestal_size_z: float = 0,
    category: str = "heating_shaking",
    model: Optional[str] = None,
  ):
    backend = HamiltonHeaterShakerBackend(driver=driver, index=index)
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
    Device.__init__(self, driver=driver)
    self.tc = TemperatureController(backend=backend)
    self.shaker = Shaker(backend=backend)
    self._capabilities = [self.tc, self.shaker]

  def serialize(self) -> dict:
    return {
      **Device.serialize(self),
      **PlateHolder.serialize(self),
    }

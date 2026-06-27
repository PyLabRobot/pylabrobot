from typing import Optional

from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateHolder

from .backend import HamiltonHeaterCoolerDriver, HamiltonHeaterCoolerTemperatureBackend


class HamiltonHeaterCooler(PlateHolder, Device):
  """Hamilton Heater Cooler (HHC): Peltier-based temperature controller.

  Connects to a STAR liquid handler via TCC RS-232 port.
  Temperature range: 0 to 110 °C with active cooling.

  Hamilton cat. no.: 6601900-01
  """

  def __init__(
    self,
    name: str,
    device_number: int = 1,
    size_x: float = 145.5,
    size_y: float = 104.0,
    size_z: float = 67.8,
    child_location: Coordinate = Coordinate(x=11.5, y=8.0, z=67.8),
    pedestal_size_z: float = 0,
    category: str = "temperature_controller",
    model: Optional[str] = None,
  ):
    driver = HamiltonHeaterCoolerDriver(device_number=device_number)
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
    self.driver: HamiltonHeaterCoolerDriver = driver
    self.tc = TemperatureController(backend=HamiltonHeaterCoolerTemperatureBackend(driver))
    self._capabilities = [self.tc]

  def serialize(self) -> dict:
    return {
      **Device.serialize(self),
      **PlateHolder.serialize(self),
    }

from typing import Optional

from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .absorbance_backend import CLARIOstarAbsorbanceBackend
from .driver import CLARIOstarDriver
from .fluorescence_backend import CLARIOstarFluorescenceBackend
from .luminescence_backend import CLARIOstarLuminescenceBackend


class CLARIOstar(Resource, Device):
  """BMG Labtech CLARIOstar plate reader."""

  def __init__(
    self,
    name: str,
    device_id: Optional[str] = None,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    driver = CLARIOstarDriver(device_id=device_id)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="BMG CLARIOstar",
    )
    Device.__init__(self, driver=driver)
    self.driver: CLARIOstarDriver = driver
    self.absorbance = Absorbance(backend=CLARIOstarAbsorbanceBackend(driver))
    self.luminescence = Luminescence(backend=CLARIOstarLuminescenceBackend(driver))
    self.fluorescence = Fluorescence(backend=CLARIOstarFluorescenceBackend(driver))
    self._capabilities = [self.absorbance, self.luminescence, self.fluorescence]

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,  # TODO: measure
      size_y=85.48,  # TODO: measure
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),  # TODO: measure
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self) -> None:
    """Open the plate tray."""
    await self.driver.open()

  async def close(self) -> None:
    """Close the plate tray."""
    await self.driver.close()

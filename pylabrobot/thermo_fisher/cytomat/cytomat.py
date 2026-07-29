from typing import List, Optional

from pylabrobot.capabilities.automated_retrieval import NoFreeSiteError, RandomAccessRetrieval
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.humidity_controlling import HumidityController
from pylabrobot.capabilities.shaking import Shaker
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import (
  Coordinate,
  PlateCarrier,
  PlateHolder,
  Resource,
  Rotation,
)

from .backend import CytomatBackend
from .constants import CytomatType

__all__ = ["Cytomat", "NoFreeSiteError"]


class Cytomat(Resource, Device):
  _racks: List[PlateCarrier]
  driver: CytomatBackend
  loading_tray: PlateHolder
  retrieval: RandomAccessRetrieval
  tc: TemperatureController
  humidity: HumidityController
  shaker: Shaker

  def __init__(
    self,
    name: str,
    driver: CytomatBackend,
    racks: List[PlateCarrier],
    loading_tray_location: Coordinate,
    size_x: float = 0,
    size_y: float = 0,
    size_z: float = 0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    raise NotImplementedError("Cytomat resource definition is not verified.")
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )
    Device.__init__(self, driver=driver)
    self.driver: CytomatBackend = driver

    self.loading_tray = PlateHolder(
      name=f"{name}_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)

    self._racks = racks
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

    self.retrieval = RandomAccessRetrieval(
      backend=driver, racks=self._racks, loading_tray=self.loading_tray
    )
    self.tc = TemperatureController(backend=driver)
    self.humidity = HumidityController(backend=driver)

    caps = [self.tc, self.humidity, self.retrieval]

    if driver.model != CytomatType.C5C:
      self.shaker = Shaker(backend=driver)
      caps.append(self.shaker)

    self._capabilities = caps

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  async def setup(self, backend_params: Optional[BackendParams] = None, **backend_kwargs):
    await super().setup(backend_params=backend_params)
    await self.driver.set_racks(self._racks)

  def serialize(self):
    from pylabrobot.serializer import serialize

    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "racks": [rack.serialize() for rack in self._racks],
      "loading_tray_location": serialize(self.loading_tray.location),
    }

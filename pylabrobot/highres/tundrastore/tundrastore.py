from typing import List, Optional

from pylabrobot.capabilities.automated_retrieval import AutomatedRetrieval
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.humidity_controlling import HumidityController
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import (
  Coordinate,
  PlateCarrier,
  PlateHolder,
  Rotation,
)
from pylabrobot.resources.resource import Resource

from .backend import TundraStoreBackend


class TundraStore(Resource, Device):
  """HighRes Biosolutions TundraStore refrigerated plate store.

  Each rack is a *stacker* (a vertical column of plate slots); plates enter and
  leave through one of the device's *nests* (transfer stations). The store has
  two nests, exposed as the loading trays of the :class:`AutomatedRetrieval`
  capability (:attr:`retrieval`). Storage bookkeeping and the fetch/store
  operations live on the capability; address a particular nest with its
  ``tray_index`` (0-based, defaulting to the first nest).
  """

  def __init__(
    self,
    name: str,
    driver: TundraStoreBackend,
    racks: List[PlateCarrier],
    nest_locations: List[Coordinate],
    size_x: float = 0,
    size_y: float = 0,
    size_z: float = 0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = "plate_store",
    model: Optional[str] = "TundraStore",
  ):
    """
    Args:
      racks: Storage racks; rack *i* maps to device stacker ``i + 1``.
      nest_locations: One :class:`Coordinate` per transfer nest (the device has
        two). ``nest_locations[i]`` is the location of nest/tray ``i``.
    """
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
    self.driver: TundraStoreBackend = driver

    self.nests: List[PlateHolder] = []
    for i, location in enumerate(nest_locations):
      nest = PlateHolder(
        name=f"{name}_nest_{i + 1}", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
      )
      self.assign_child_resource(nest, location=location)
      self.nests.append(nest)

    self._racks = racks
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

    self.retrieval = AutomatedRetrieval(
      backend=driver, racks=self._racks, loading_trays=self.nests
    )
    self.tc = TemperatureController(backend=driver)
    self.humidity = HumidityController(backend=driver)
    self._capabilities = [self.tc, self.humidity, self.retrieval]

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  async def setup(self, backend_params: Optional[BackendParams] = None):
    await super().setup(backend_params=backend_params)
    await self.driver.set_racks(self._racks)

  def serialize(self) -> dict:
    from pylabrobot.serializer import serialize

    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "racks": [rack.serialize() for rack in self._racks],
      "nest_locations": [serialize(nest.location) for nest in self.nests],
    }

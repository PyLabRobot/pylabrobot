import random
from typing import List, Literal, Optional, Union, cast

from pylabrobot.capabilities.automated_retrieval import AutomatedRetrieval
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.humidity_controlling import HumidityController
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import (
  Coordinate,
  Plate,
  PlateCarrier,
  PlateHolder,
  ResourceNotFoundError,
  Rotation,
)
from pylabrobot.resources.resource import Resource

from .backend import TundraStoreBackend


class NoFreeSiteError(Exception):
  pass


class TundraStore(Resource, Device):
  """HighRes Biosolutions TundraStore refrigerated plate store.

  Each rack is a *stacker* (a vertical column of plate slots); plates enter and
  leave through a *nest* (transfer station). The store has two nests, but
  mapping PyLabRobot's single-loading-tray :class:`AutomatedRetrieval` model
  onto two nests is still an open design decision, so for now this frontend
  drives a single configurable nest (see ``loading_tray_nest`` on the backend).
  """

  def __init__(
    self,
    name: str,
    driver: TundraStoreBackend,
    racks: List[PlateCarrier],
    loading_tray_location: Coordinate,
    size_x: float = 0,
    size_y: float = 0,
    size_z: float = 0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = "plate_store",
    model: Optional[str] = "TundraStore",
  ):
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

    self.loading_tray = PlateHolder(
      name=f"{name}_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)

    self._racks = racks
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

    self.retrieval = AutomatedRetrieval(backend=driver)
    self.tc = TemperatureController(backend=driver)
    self.humidity = HumidityController(backend=driver)
    self._capabilities = [self.tc, self.humidity, self.retrieval]

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  async def setup(self, backend_params: Optional[BackendParams] = None):
    await super().setup(backend_params=backend_params)
    await self.driver.set_racks(self._racks)

  def get_num_free_sites(self) -> int:
    return sum(len(rack.get_free_sites()) for rack in self._racks)

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    for rack in self._racks:
      for site in rack.sites.values():
        if site.resource is not None and site.resource.name == plate_name:
          return site
    raise ResourceNotFoundError(f"Plate {plate_name} not found in '{self.name}'")

  def _available_sites(self, plate: Plate) -> List[PlateHolder]:
    def height(p: Plate) -> float:
      return p.get_size_z() + (3 if p.has_lid() else 0)

    available = [
      site
      for rack in self._racks
      for site in rack.get_free_sites()
      if site.get_size_z() >= height(plate)
    ]
    if not available:
      raise NoFreeSiteError(f"No free site in '{self.name}' for plate '{plate.name}'")
    return sorted(available, key=lambda s: s.get_size_z())

  async def fetch_plate_to_loading_tray(self, plate_name: str) -> Plate:
    """Retrieve a stored plate and place it on the loading-tray nest."""
    site = self.get_site_by_plate_name(plate_name)
    plate = cast(Plate, site.resource)
    await self.retrieval.fetch_plate_to_loading_tray(plate)
    plate.unassign()
    self.loading_tray.assign_child_resource(plate)
    return plate

  async def take_in_plate(self, site: Union[PlateHolder, Literal["random", "smallest"]]):
    """Store the plate currently on the loading-tray nest into a stacker slot."""
    plate = cast(Plate, self.loading_tray.resource)
    if plate is None:
      raise ResourceNotFoundError(f"No plate on the loading tray of '{self.name}'")

    target: PlateHolder
    if site == "smallest":
      target = self._available_sites(plate)[0]
    elif site == "random":
      target = random.choice(self._available_sites(plate))
    elif isinstance(site, PlateHolder):
      if site not in self._available_sites(plate):
        raise ValueError(f"Site {site.name} is not available for plate {plate.name}")
      target = site
    else:
      raise ValueError(f"Invalid site: {site}")

    await self.retrieval.store_plate(plate, target)
    plate.unassign()
    target.assign_child_resource(plate)

  def serialize(self) -> dict:
    from pylabrobot.serializer import serialize

    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "racks": [rack.serialize() for rack in self._racks],
      "loading_tray_location": serialize(self.loading_tray.location),
    }

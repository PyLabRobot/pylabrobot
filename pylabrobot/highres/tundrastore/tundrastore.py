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
  leave through one of the device's *nests* (transfer stations). The store has
  two nests, modeled here as two loading trays — :attr:`nests` ``[0]`` and
  ``[1]`` — addressed by the ``tray_index`` argument of the :class:`AutomatedRetrieval`
  capability (0-based). ``tray_index=None`` uses :attr:`default_tray`.
  """

  def __init__(
    self,
    name: str,
    driver: TundraStoreBackend,
    racks: List[PlateCarrier],
    nest_locations: List[Coordinate],
    default_tray: int = 0,
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
      default_tray: 0-based nest used when a ``tray_index`` argument is omitted.
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
    self.default_tray = default_tray

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

    self.retrieval = AutomatedRetrieval(backend=driver)
    self.tc = TemperatureController(backend=driver)
    self.humidity = HumidityController(backend=driver)
    self._capabilities = [self.tc, self.humidity, self.retrieval]

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  def _tray_index(self, tray_index: Optional[int]) -> int:
    idx = self.default_tray if tray_index is None else tray_index
    if not 0 <= idx < len(self.nests):
      raise ValueError(f"'{self.name}' has trays 0..{len(self.nests) - 1}; got tray_index={idx}.")
    return idx

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

  async def fetch_plate_to_nest(self, plate_name: str, tray_index: Optional[int] = None) -> Plate:
    """Retrieve a stored plate and place it on a nest (default :attr:`default_tray`)."""
    idx = self._tray_index(tray_index)
    site = self.get_site_by_plate_name(plate_name)
    plate = cast(Plate, site.resource)
    await self.retrieval.fetch_plate_to_loading_tray(plate, tray_index=idx)
    plate.unassign()
    self.nests[idx].assign_child_resource(plate)
    return plate

  async def take_in_plate(
    self,
    site: Union[PlateHolder, Literal["random", "smallest"]],
    tray_index: Optional[int] = None,
  ):
    """Store the plate currently on a nest into a stacker slot.

    Args:
      site: Destination slot, or ``"smallest"`` / ``"random"`` to auto-select.
      tray_index: Which nest the plate is on (default :attr:`default_tray`).
    """
    idx = self._tray_index(tray_index)
    plate = cast(Plate, self.nests[idx].resource)
    if plate is None:
      raise ResourceNotFoundError(f"No plate on nest {idx} of '{self.name}'")

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

    await self.retrieval.store_plate(plate, target, tray_index=idx)
    plate.unassign()
    target.assign_child_resource(plate)

  def serialize(self) -> dict:
    from pylabrobot.serializer import serialize

    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "racks": [rack.serialize() for rack in self._racks],
      "nest_locations": [serialize(nest.location) for nest in self.nests],
      "default_tray": self.default_tray,
    }

import random
from typing import List, Literal, Optional, Union, cast

from pylabrobot.capabilities.automated_retrieval import AutomatedRetrievalCapability
from pylabrobot.capabilities.humidity_controlling import HumidityControlCapability
from pylabrobot.capabilities.shaking import ShakingCapability
from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.device import Device
from pylabrobot.resources import (
  Coordinate,
  Plate,
  PlateCarrier,
  PlateHolder,
  Resource,
  ResourceNotFoundError,
  Rotation,
)

from .backend import CytomatBackend
from .constants import CytomatType


class NoFreeSiteError(Exception):
  pass


class Cytomat(Resource, Device):
  def __init__(
    self,
    name: str,
    backend: CytomatBackend,
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
    Device.__init__(self, backend=backend)
    self._backend: CytomatBackend = backend

    self.loading_tray = PlateHolder(
      name=f"{name}_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)

    self._racks = racks
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

    self.retrieval = AutomatedRetrievalCapability(backend=backend)
    self.tc = TemperatureControlCapability(backend=backend)
    self.humidity = HumidityControlCapability(backend=backend)

    caps = [self.tc, self.humidity, self.retrieval]

    if backend.model != CytomatType.C5C:
      self.shaker = ShakingCapability(backend=backend)
      caps.append(self.shaker)

    self._capabilities = caps

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  async def setup(self, **backend_kwargs):
    await super().setup()
    await self._backend.set_racks(self._racks)

  def get_num_free_sites(self) -> int:
    return sum(len(rack.get_free_sites()) for rack in self._racks)

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    for rack in self._racks:
      for site in rack.sites.values():
        if site.resource is not None and site.resource.name == plate_name:
          return site
    raise ResourceNotFoundError(f"Plate {plate_name} not found in '{self.name}'")

  async def fetch_plate_to_loading_tray(self, plate_name: str) -> Plate:
    """Fetch a plate from storage and put it on the loading tray."""
    site = self.get_site_by_plate_name(plate_name)
    plate = site.resource
    assert plate is not None
    await self.retrieval.fetch_plate_to_loading_tray(plate)
    plate.unassign()
    self.loading_tray.assign_child_resource(plate)
    return plate

  def _find_available_sites_sorted(self, plate: Plate) -> List[PlateHolder]:
    def _plate_height(p: Plate):
      if p.has_lid():
        return p.get_size_z() + 3
      return p.get_size_z()

    available = [
      site
      for rack in self._racks
      for site in rack.get_free_sites()
      if site.get_size_z() >= _plate_height(plate)
    ]
    if len(available) == 0:
      raise NoFreeSiteError(
        f"No free site found in '{self.name}' for plate '{plate.name}'"
      )
    return sorted(available, key=lambda site: site.get_size_z())

  def find_smallest_site_for_plate(self, plate: Plate) -> PlateHolder:
    return self._find_available_sites_sorted(plate)[0]

  def find_random_site(self, plate: Plate) -> PlateHolder:
    return random.choice(self._find_available_sites_sorted(plate))

  async def take_in_plate(self, site: Union[PlateHolder, Literal["random", "smallest"]]):
    """Take a plate from the loading tray and put it in storage."""
    plate = cast(Plate, self.loading_tray.resource)
    if plate is None:
      raise ResourceNotFoundError(f"No plate on the loading tray of '{self.name}'")

    if site == "random":
      site = self.find_random_site(plate)
    elif site == "smallest":
      site = self.find_smallest_site_for_plate(plate)
    elif isinstance(site, PlateHolder):
      if site not in self._find_available_sites_sorted(plate):
        raise ValueError(f"Site {site.name} is not available for plate {plate.name}")
    else:
      raise ValueError(f"Invalid site: {site}")
    await self.retrieval.store_plate(plate, site)
    plate.unassign()
    site.assign_child_resource(plate)

  def summary(self) -> str:
    def create_pretty_table(header, *columns) -> str:
      col_widths = [
        max(len(str(item)) for item in [header[i]] + list(columns[i])) for i in range(len(header))
      ]

      def format_row(row, border="|") -> str:
        return (
          f"{border} "
          + " | ".join(f"{str(row[i]).ljust(col_widths[i])}" for i in range(len(row)))
          + f" {border}"
        )

      def separator_line(cross: str = "+", line: str = "-") -> str:
        return cross + cross.join(line * (width + 2) for width in col_widths) + cross

      table = []
      table.append(separator_line())
      table.append(format_row(header))
      table.append(separator_line())
      for row in zip(*columns):
        table.append(format_row(row))
      table.append(separator_line())
      return "\n".join(table)

    header = [f"Rack {i}" for i in range(len(self._racks))]
    sites = [
      [site.resource.name if site.resource else "<empty>" for site in reversed(rack.sites.values())]
      for rack in self._racks
    ]
    return create_pretty_table(header, *sites)

  def serialize(self):
    from pylabrobot.serializer import serialize
    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "racks": [rack.serialize() for rack in self._racks],
      "loading_tray_location": serialize(self.loading_tray.location),
    }

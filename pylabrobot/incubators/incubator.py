import random
from typing import List, Literal, Optional, Union, cast

from pylabrobot.machines import Machine
from pylabrobot.resources import (
  Coordinate,
  Plate,
  PlateCarrier,
  PlateHolder,
  Resource,
  ResourceNotFoundError,
  Rotation,
)
from pylabrobot.serializer import deserialize, serialize

from .backend import IncubatorBackend


class NoFreeSiteError(Exception):
  pass


class Incubator(Machine, Resource):
  def __init__(
    self,
    backend: IncubatorBackend,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    racks: List[PlateCarrier],
    loading_tray_location: Coordinate,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Machine.__init__(self, backend=backend)
    self.backend: IncubatorBackend = backend  # fix type
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
    self.loading_tray = PlateHolder(
      name=self.name + "_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)

    self._racks = racks
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  async def setup(self, **backend_kwargs):
    await super().setup()
    await self.backend.set_racks(self._racks)

  def get_num_free_sites(self) -> int:
    return sum(len(rack.get_free_sites()) for rack in self._racks)

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    for rack in self._racks:
      for site in rack.sites.values():
        if site.resource is not None and site.resource.name == plate_name:
          return site
    raise ResourceNotFoundError(f"Plate {plate_name} not found in incubator '{self.name}'")

  async def fetch_plate_to_loading_tray(self, plate_name: str) -> Plate:
    """Fetch a plate from the incubator and put it on the loading tray."""

    site = self.get_site_by_plate_name(plate_name)
    plate = site.resource
    assert plate is not None
    await self.backend.fetch_plate_to_loading_tray(plate)
    plate.unassign()
    self.loading_tray.assign_child_resource(plate)
    return plate

  def _find_available_sites_sorted(self, plate: Plate) -> List[PlateHolder]:
    def _plate_height(p: Plate) -> float:
        return p.get_size_z() + 3 if p.has_lid() else p.get_size_z()

    required = _plate_height(plate)
    extra_clearance = 50.0 # measured for 21 plate 23mm pitch racks, but bigger on all other rack models, so safe to use

    available: List[PlateHolder] = []
    for rack in self._racks:
      all_sites = [c for c in rack.children if isinstance(c, PlateHolder)]
      free_sites = [site for site in all_sites if site.resource is None]
      top_idx = len(all_sites) - 1

      for idx, site in enumerate(all_sites):
          if site not in free_sites:
              continue
          pitch = site.get_size_z()
          if pitch >= required or (idx == top_idx and pitch + extra_clearance >= required):
              available.append(site)

    if not available:
        raise NoFreeSiteError(
            f"No free site found in incubator '{self.name}' for plate '{plate.name}'"
        )

    return sorted(available, key=lambda s: s.get_size_z())



  def find_smallest_site_for_plate(self, plate: Plate) -> PlateHolder:
    return self._find_available_sites_sorted(plate)[0]

  def find_random_site(self, plate: Plate) -> PlateHolder:
    return random.choice(self._find_available_sites_sorted(plate))

  async def take_in_plate(self, site: Union[PlateHolder, Literal["random", "smallest"]]):
    """Take a plate from the loading tray and put it in the incubator."""

    plate = cast(Plate, self.loading_tray.resource)
    if plate is None:
      raise ResourceNotFoundError(f"No plate on the loading tray of incubator '{self.name}'")

    if site == "random":
      site = self.find_random_site(plate)
    elif site == "smallest":
      site = self.find_smallest_site_for_plate(plate)
    elif isinstance(site, PlateHolder):
      if site not in self._find_available_sites_sorted(plate):
        raise ValueError(f"Site {site.name} is not available for plate {plate.name}")
    else:
      raise ValueError(f"Invalid site: {site}")
    await self.backend.take_in_plate(plate, site)
    plate.unassign()
    site.assign_child_resource(plate)

  async def set_temperature(self, temperature: float):
    """Set the temperature of the incubator in degrees Celsius."""
    return await self.backend.set_temperature(temperature)

  async def get_temperature(self) -> float:
    return await self.backend.get_temperature()

  async def open_door(self):
    return await self.backend.open_door()

  async def close_door(self):
    return await self.backend.close_door()

  async def start_shaking(self, frequency: float = 1.0):
    await self.backend.start_shaking(frequency=frequency)

  async def stop_shaking(self):
    await self.backend.stop_shaking()

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
      table.append(separator_line())  # Top border
      table.append(format_row(header))
      table.append(separator_line())  # Header separator
      for row in zip(*columns):
        table.append(format_row(row))
      table.append(separator_line())  # Bottom border
      return "\n".join(table)

    header = [f"Rack {i}" for i in range(len(self._racks))]
    sites = [
      [site.resource.name if site.resource else "<empty>" for site in reversed(rack.sites.values())]
      for rack in self._racks
    ]
    return create_pretty_table(header, *sites)

  def serialize(self):
    return {
      **Machine.serialize(self),
      **Resource.serialize(self),
      "backend": self.backend.serialize(),
      "racks": [rack.serialize() for rack in self._racks],
      "loading_tray_location": serialize(self.loading_tray.location),
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False):
    backend = IncubatorBackend.deserialize(data.pop("backend"))
    return cls(
      backend=backend,
      name=data["name"],
      size_x=data["size_x"],
      size_y=data["size_y"],
      size_z=data["size_z"],
      racks=[PlateCarrier.deserialize(rack) for rack in data["racks"]],
      loading_tray_location=cast(Coordinate, deserialize(data["loading_tray_location"])),
      rotation=Rotation.deserialize(data["rotation"]),
      category=data["category"],
      model=data["model"],
    )

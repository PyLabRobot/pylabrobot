from typing import Dict, Optional, cast

from pylabrobot.machines import Machine
from pylabrobot.resources import (
  Plate,
  PlateHolder,
  Resource,
  ResourceHolder,
  ResourceNotFoundError,
  Rotation,
)
from pylabrobot.resources.coordinate import Coordinate

from .backend import IncubatorBackend
from .rack import Rack


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
    self.loading_tray = ResourceHolder(
      name=self.name + "_tray", size_x=127.76, size_y=85.48, size_z=0
    )

    def c(i):
      ph = PlateHolder(
        name="rack1_site" + str(i), size_x=127.76, size_y=85.48, size_z=60, pedestal_size_z=0
      )
      ph.location = Coordinate.zero()
      return ph

    self.racks = [
      Rack(
        name="rack1",
        size_x=127.76,
        size_y=85.48,
        size_z=1000,
        sites={i: c(i) for i in range(1, 10)},
        index=1,
      ),
      Rack(
        name="rack2",
        size_x=127.76,
        size_y=85.48,
        size_z=1000,
        sites={i: c(i) for i in range(1, 10)},
        index=2,
      ),
    ]
    # TODO: racks should be children of self.

  def get_num_free_sites(self) -> int:
    return sum(len(rack.get_free_sites()) for rack in self.racks)

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    for rack in self.racks:
      for site in rack.sites.values():
        if site.resource is not None and site.resource.name == plate_name:
          return site
    raise ResourceNotFoundError(f"Plate {plate_name} not found in incubator '{self.name}'")

  async def fetch_plate_to_loading_tray(self, plate_name: str):
    """Fetch a plate from the incubator and put it on the loading tray."""

    site = self.get_site_by_plate_name(plate_name)
    await self.backend.fetch_plate_to_loading_tray(site.resource)

    self.loading_tray.assign_child_resource(site.resource)

  def find_first_site_for_plate(self, plate: Plate) -> PlateHolder:
    for rack in self.racks:
      free_sites = rack.get_free_sites()
      # TODO: check if the plate fits in the site
      if len(free_sites) > 0:
        return free_sites[0]
    raise NoFreeSiteError(f"No free site found in incubator '{self.name}'")

  def find_smallest_site_for_plate(self, plate: Plate) -> PlateHolder:
    def _plate_height(p: Plate):
      if p.has_lid():
        # TODO: we can use plr nesting height
        # lid.location.z + lid.get_anchor(z="t").z
        return p.get_size_z() + 3

      return p.get_size_z()

    filtered_sorted_racks = sorted(
      (rack for rack in self.racks if _plate_height(plate) < rack.pitch()),
      key=lambda rack: rack.pitch(),
    )
    if len(filtered_sorted_racks) == 0:
      raise NoFreeSiteError(f"No available site for plate with pitch {plate.get_size_z()}")

    for rack in filtered_sorted_racks:
      free_sites = rack.get_free_sites()
      if len(free_sites) > 0:
        return free_sites[0]

    raise ValueError(f"No available site for plate with pitch {plate.get_size_z()}")

  async def take_in_plate(self):  # site
    """Take a plate from the loading tray and put it in the incubator.

    Args:
      site: PlateHolder, or `"first"`, or `"random"`, or `"smallest"`.
    """

    plate = cast(Plate, self.loading_tray.resource)
    if plate is None:
      raise ResourceNotFoundError(f"No plate on the loading tray of incubator '{self.name}'")

    site = self.find_first_site_for_plate(plate=plate)

    await self.backend.take_in_plate(plate, site)

    site.assign_child_resource(plate)

  async def set_temperature(self, temperature: float):
    """Set the temperature of the incubator in degrees Celsius."""
    return await self.backend.set_temperature(temperature)

  async def get_temperature(self) -> float:
    return await self.backend.get_temperature()

  def summary(self) -> str:
    def create_pretty_table(header, *columns):
      col_widths = [
        max(len(str(item)) for item in [header[i]] + list(columns[i])) for i in range(len(header))
      ]

      def format_row(row, border="|"):
        return (
          f"{border} "
          + " | ".join(f"{str(row[i]).ljust(col_widths[i])}" for i in range(len(row)))
          + f" {border}"
        )

      def separator_line(cross="+", line="-"):
        return cross + cross.join(line * (width + 2) for width in col_widths) + cross

      table = []
      table.append(separator_line())  # Top border
      table.append(format_row(header))
      table.append(separator_line())  # Header separator
      for row in zip(*columns):
        table.append(format_row(row))
      table.append(separator_line())  # Bottom border
      return "\n".join(table)

    header = [f"Rack {rack.index}" for rack in self.racks]
    sites = [
      [site.resource.name if site.resource else "empty" for site in rack.sites.values()]
      for rack in self.racks
    ]
    return create_pretty_table(header, *sites)

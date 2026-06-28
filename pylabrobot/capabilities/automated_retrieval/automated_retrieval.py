import random
from typing import List, Literal, Optional, Union, cast

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.resources import (
  Plate,
  PlateCarrier,
  PlateHolder,
  ResourceNotFoundError,
)

from .backend import AutomatedRetrievalBackend


class NoFreeSiteError(Exception):
  pass


class AutomatedRetrieval(Capability):
  """Automated plate retrieval/storage capability.

  Owns the storage racks and the loading tray(s), and implements the site
  bookkeeping (free-site counting, lookup and selection) shared by all
  automated storage systems so devices composing this capability do not have to
  reimplement it.

  Most devices have a single loading tray and pass it as a one-element list.
  Devices with several transfer nests (e.g. the TundraStore) pass one
  :class:`PlateHolder` per nest and address them by ``tray_index`` (0-based,
  defaulting to the first tray).

  See :doc:`/user_guide/capabilities/automated-retrieval` for a walkthrough.
  """

  def __init__(
    self,
    backend: AutomatedRetrievalBackend,
    racks: Optional[List[PlateCarrier]] = None,
    loading_trays: Optional[List[PlateHolder]] = None,
  ):
    super().__init__(backend=backend)
    self.backend: AutomatedRetrievalBackend = backend
    self._racks: List[PlateCarrier] = racks if racks is not None else []
    self.loading_trays: List[PlateHolder] = loading_trays if loading_trays is not None else []

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  def _loading_tray(self, tray_index: int) -> PlateHolder:
    if not self.loading_trays:
      raise RuntimeError("No loading tray configured for this automated retrieval.")
    if not 0 <= tray_index < len(self.loading_trays):
      raise ValueError(
        f"tray_index {tray_index} out of range; this device has "
        f"{len(self.loading_trays)} loading tray(s)."
      )
    return self.loading_trays[tray_index]

  # -- site bookkeeping --

  def get_num_free_sites(self) -> int:
    return sum(len(rack.get_free_sites()) for rack in self._racks)

  def get_site_by_plate_name(self, plate_name: str) -> PlateHolder:
    for rack in self._racks:
      for site in rack.sites.values():
        if site.resource is not None and site.resource.name == plate_name:
          return site
    raise ResourceNotFoundError(f"Plate {plate_name} not found in automated storage")

  def _find_available_sites_sorted(self, plate: Plate) -> List[PlateHolder]:
    """Find all sites that are free and fit the plate, sorted by size."""

    def _plate_height(p: Plate):
      if p.has_lid():
        # TODO: we can use plr nesting height
        # lid.location.z + lid.get_anchor(z="t").z
        return p.get_size_z() + 3
      return p.get_size_z()

    available = [
      site
      for rack in self._racks
      for site in rack.get_free_sites()
      if site.get_size_z() >= _plate_height(plate)
    ]
    if len(available) == 0:
      raise NoFreeSiteError(f"No free site found for plate '{plate.name}'")
    return sorted(available, key=lambda site: site.get_size_z())

  def find_smallest_site_for_plate(self, plate: Plate) -> PlateHolder:
    return self._find_available_sites_sorted(plate)[0]

  def find_random_site(self, plate: Plate) -> PlateHolder:
    return random.choice(self._find_available_sites_sorted(plate))

  # -- storage operations --

  @need_capability_ready
  async def fetch_plate_to_loading_tray(self, plate_name: str, tray_index: int = 0) -> Plate:
    """Retrieve the named plate from storage onto loading tray ``tray_index``."""
    tray = self._loading_tray(tray_index)
    site = self.get_site_by_plate_name(plate_name)
    plate = cast(Plate, site.resource)
    await self.backend.fetch_plate_to_loading_tray(plate, tray_index=tray_index)
    plate.unassign()
    tray.assign_child_resource(plate)
    return plate

  @need_capability_ready
  async def take_in_plate(
    self,
    site: Union[PlateHolder, Literal["random", "smallest"]] = "smallest",
    tray_index: int = 0,
  ):
    """Take the plate from loading tray ``tray_index`` and store it into storage.

    `site` may be an explicit free `PlateHolder`, or `"smallest"`/`"random"` to
    let the capability pick a fitting free site.
    """
    tray = self._loading_tray(tray_index)
    plate = cast(Optional[Plate], tray.resource)
    if plate is None:
      raise ResourceNotFoundError("No plate on the loading tray.")

    if site == "random":
      site = self.find_random_site(plate)
    elif site == "smallest":
      site = self.find_smallest_site_for_plate(plate)
    elif isinstance(site, PlateHolder):
      if site not in self._find_available_sites_sorted(plate):
        raise ValueError(f"Site {site.name} is not available for plate {plate.name}")
    else:
      raise ValueError(f"Invalid site: {site}")

    await self.backend.store_plate(plate, site, tray_index=tray_index)
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

  async def _on_stop(self):
    await super()._on_stop()

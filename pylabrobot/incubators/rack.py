from typing import Dict, Optional

from pylabrobot.resources.carrier import PlateCarrier, PlateHolder


class Rack(PlateCarrier):
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    index: int,
    sites: Optional[Dict[int, PlateHolder]] = None,
    category="rack",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      sites=sites,
      category=category,
      model=model,
    )
    self._index = index

  @property
  def index(self) -> int:
    return self._index

  def get_free_sites(self):
    return [site for site in self.sites.values() if site.resource is None]

  def pitch(self):  # TODO: rename this
    site_heights = set(site.get_size_z() for site in self.sites.values())
    if len(site_heights) != 1:
      raise ValueError("All sites in a rack must have the same height")
    return site_heights.pop()

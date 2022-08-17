from __future__ import annotations

import logging
from typing import List, Optional

from .coordinate import Coordinate
from .resource import Resource


logger = logging.getLogger(__name__)


class Carrier(Resource):
  """ Abstract base resource for carriers.

  It is recommended to always use a resource carrier to store resources, because this ensures the
  location of the resources can be calculated precisely.

  It is important to use the `__getitem__` and `__setitem__` methods to access the resources,
  because this ensures that the location of the resources is updated to be within the carrier and
  that the appropriate callbacks are called.

  Examples:
    Creating a `TipCarrier` and assigning one set of tips at location 0 (the bottom):

    >>> tip_car = TIP_CAR_480_A00(name='tip carrier')
    >>> tip_car[0] = STF_L(name='tips_1')

    Getting the tips:

    >>> tip_car[0]

    STF_L(name='tips_1')

    Deleting the tips:

    >>> del tip_car[0]

    Alternative way to delete the tips:

    >>> tip_car[0] = None

  Attributes:
    capacity: The maximum number of items that can be stored in this carrier.
  """

  class CarrierSite(Resource):
    """ A single site within a carrier. """
    def __init__(self, size_x, size_y, size_z, location, parent, spot):
      super().__init__(name=f"carrier-{parent.name}-spot-{spot}", size_x=size_x, size_y=size_y,
        size_z=size_z, location=Coordinate(0, 0, 0), category="carrier_site")
      self.location: Coordinate = location
      self.resource: Resource = None
      self.parent: Carrier = parent
      self.spot: int = spot

    def assign_child_resource(self, resource, **kwargs):
      self.resource = resource
      return super().assign_child_resource(resource, **kwargs)

    def unassign_child_resource(self, resource):
      self.resource = None
      return super().unassign_child_resource(resource)

    def serialize(self):
      return dict(
        spot=self.spot,
        resource=self.resource.serialize() if self.resource is not None else None,
        **super().serialize()
      )

    def __eq__(self, other):
      return super().__eq__(other) and self.spot == other.spot and self.resource == other.resource

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    location: Coordinate,
    sites: List[Coordinate],
    site_size_x: float,
    site_size_y: float,
    category: Optional[str] = "carrier"):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z,
      location=location, category=category)
    self.capacity = len(sites)

    self.sites: List[Carrier.CarrierSite] = []
    for i in range(self.capacity):
      site = Carrier.CarrierSite(
        size_x=site_size_x, size_y=site_size_y, size_z=0,
        location=sites[i] + Coordinate(0, 0, 0),
        parent=self, spot=i)
      self.sites.append(site)
      super().assign_child_resource(site)

  def assign_child_resource(self, resource, spot: int, **kwargs):
    """ Assign a resource to this carrier.
    Also see :meth:`~Resource.assign_child_resource`

    Args:
      resource: The resource to assign.
      spot: The index of the site to assign the resource to.

    Raises:
      ValueError: If the resource is already assigned to this carrier.
    """

    # super().assign_child_resource(resource)
    if spot < 0 or spot >= self.capacity:
      raise IndexError(f"Invalid spot {spot}")
    if self.sites[spot].resource is not None:
      raise ValueError(f"spot {spot} already has a resource")

    resource.location = Coordinate(0, 0, 0) # directly in carrier site
    self.sites[spot].assign_child_resource(resource)

  def unassign_child_resource(self, resource):
    """ Unassign a resource from this carrier, checked by name.
    Also see :meth:`~Resource.assign_child_resource`

    Args:
      resource: The resource to unassign.

    Raises:
      ValueError: If the resource is not assigned to this carrier.
    """

    self.sites[resource.parent.spot].unassign_child_resource(resource)

  def __getitem__(self, idx) -> Carrier.CarrierSite:
    """ Get a site by index. """
    if not 0 <= idx < self.capacity:
      raise IndexError(f"Invalid index {idx}")
    return self.sites[idx]

  def __setitem__(self, idx, resource: Optional[Resource]):
    """ Assign a resource to this carrier. See :meth:`~Carrier.assign_child_resource` """
    if resource is None:
      if self[idx].resource is not None:
        self.unassign_child_resource(self[idx].resource)
    else:
      self.assign_child_resource(resource, spot=idx)

  def __delitem__(self, idx):
    """ Unassign a resource from this carrier. See :meth:`~Carrier.unassign_child_resource` """
    self.unassign_child_resource(self[idx].resource)

  def get_resources(self) -> List[Resource]:
    """ Get all resources, using self.__getitem__ (so that the location is within this carrier). """
    return [site.resource for site in self.sites if site.resource is not None]

  def get_sites(self) -> List[Carrier.CarrierSite]:
    """ Get all sites. """
    return self.sites

  def __eq__(self, other):
    return super().__eq__(other) and self.sites == other.sites

class TipCarrier(Carrier):
  """ Base class for tip carriers. """
  def __init__(self, name: str, size_x, size_y, size_z,
    location: Coordinate, sites: List[Coordinate], site_size_x, site_size_y):
    super().__init__(name, size_x, size_y, size_z, location,
      sites, site_size_x, site_size_y, category='tip_carrier')

class PlateCarrier(Carrier):
  """ Base class for plate carriers. """
  def __init__(self, name: str, size_x, size_y, size_z,
    location: Coordinate, sites: List[Coordinate], site_size_x, site_size_y):
    super().__init__(name, size_x, size_y, size_z, location,
      sites, site_size_x, site_size_y, category='plate_carrier')

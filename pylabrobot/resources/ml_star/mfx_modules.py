""" ML Star MFX modules (including machine defintions placed on a MFX carrier) """

from __future__ import annotations

import logging
from typing import List, Optional, Union

from pylabrobot.resources.resource import Resource
from pylabrobot.resources.carrier import Coordinate, create_homogeneous_carrier_sites

logger = logging.getLogger("pylabrobot")


class MFXSite(Resource):
  """ A single site within a MFX module. """

  def __init__(self, name: str, size_x: float, size_y: float, size_z: float, spot: int,
    category: str = "mfx_site", model: Optional[str] = None):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    self.resource: Optional[Resource] = None
    self.spot: int = spot

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True
  ):
    self.resource = resource
    return super().assign_child_resource(resource, location=location)

  def unassign_child_resource(self, resource):
    self.resource = None
    return super().unassign_child_resource(resource)

  def serialize(self):
    return {
      "spot": self.spot,
      **super().serialize()
    }

  def __eq__(self, other):
    return super().__eq__(other) and self.spot == other.spot and self.resource == other.resource

def create_mfx_module_sites(
  locations: List[Coordinate],
  site_size_x: List[Union[float, int]],
  site_size_y: List[Union[float, int]]) -> List[MFXSite]:
  """ Create a list of MFX module sites with the given sizes. """

  sites = []
  for spot, (l, x, y) in enumerate(zip(locations, site_size_x, site_size_y)):
    site = MFXSite(
      name = f"mfx-module-site-{spot}",
      size_x=x, size_y=y, size_z=0, spot=spot)
    site.location = l
    sites.append(site)
  return sites


def create_homogeneous_mfx_module_sites(
  locations: List[Coordinate],
  site_size_x: float,
  site_size_y: float) -> List[MFXSite]:
  """ Create a list of MFX module sites with the same size. """

  n = len(locations)
  return create_mfx_module_sites(locations, [site_size_x] * n, [site_size_y] * n)

# Define base resource
class MFXModule(Resource):
  """ Abstract base resource for MFX modules to be placed on a MFX carrier (landscape/portrait, 4/5 positions).

  Examples:
    Creating a `TipCarrier` and assigning one set of tips at location 0 (the bottom):

    >>> tip_car = TIP_CAR_480_A00(name='tip carrier')
    >>> tip_car[0] = STF_L(name='tips_1')


  Attributes:
    capacity: The maximum number of items that can be stored in this carrier.
  """

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    sites: Optional[List[MFXSite]] = None,
    category: Optional[str] = "carrier",
    model: Optional[str] = None):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)

    sites = sites if sites is not None else []

    self.sites: List[MFXSite] = []
    for site in sites:
      site.name = f"mfx-{self.name}-spot-{site.spot}"
      self.assign_child_resource(site, location=site.location)

  @property
  def capacity(self):
    return len(self.sites)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True
  ):
    """ Assign a resource to this carrier.

    For a MFX Module, the only valid resource is a :class:`MFXSite`.

    Also see :meth:`~Resource.assign_child_resource`.

    Raises:
      TypeError: If the resource is not a :class:`MFXSite`.
    """

    if not isinstance(resource, MFXSite):
      raise TypeError(f"Invalid resource {resource}")
    self.sites.append(resource)
    super().assign_child_resource(resource, location=location)

  def assign_resource_to_site(self, resource: Resource, spot: int):
    if spot < 0 or spot >= self.capacity:
      raise IndexError(f"Invalid spot {spot}")
    if self.sites[spot].resource is not None:
      raise ValueError(f"spot {spot} already has a resource")

    self.sites[spot].assign_child_resource(resource, location=Coordinate.zero())

  def unassign_child_resource(self, resource):
    """ Unassign a resource from this carrier, checked by name.
    Also see :meth:`~Resource.assign_child_resource`

    Args:
      resource: The resource to unassign.

    Raises:
      ValueError: If the resource is not assigned to this carrier.
    """

    self.sites[resource.parent.spot].unassign_child_resource(resource)

  def __getitem__(self, idx: int) -> MFXSite:
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
      self.assign_resource_to_site(resource, spot=idx)

  def __delitem__(self, idx):
    """ Unassign a resource from this carrier. See :meth:`~Carrier.unassign_child_resource` """
    self.unassign_child_resource(self[idx].resource)

  def get_resources(self) -> List[Resource]:
    """ Get all resources, using self.__getitem__ (so that the location is within this carrier). """
    return [site.resource for site in self.sites if site.resource is not None]

  def get_sites(self) -> List[MFXSite]:
    """ Get all sites. """
    return self.sites

  def __eq__(self, other):
    return super().__eq__(other) and self.sites == other.sites


# MFX module library (enables correct z-height offsets for each module)


def MFX_TIP_module(name: str) -> MFXModule:
  """ Hamilton cat. no.: 188160
  Module to position a high-, standard-, low volume or 5ml tip rack (but not a 384 tip rack).
  """
  return MFXModule(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=18.195,
    sites=create_homogeneous_mfx_module_sites([
        Coordinate(6.2, 10.0, 114.95-18.195),
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="MFX_TIP_module"
  )


def MFX_DWP_module(name: str) -> MFXModule:
  """ Hamilton cat. no.: 188042
  Module to position a Deep Well Plate / tube racks (MATRIX or MICRONICS) / NUNC reagent trough.
  """
  return MFXModule(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=18.195,
    sites=create_homogeneous_mfx_module_sites([
        Coordinate(4.0, 4.5, 178.73-18.195-100), # probe height - carrier_height - deck_height
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="MFX_DWP_module"
  )
from __future__ import annotations

import logging
from typing import Generic, List, Optional, Type, TypeVar, Union

from .coordinate import Coordinate
from .plate import Plate
from .resource import Resource
from .plate_adapter import PlateAdapter

logger = logging.getLogger("pylabrobot")


class CarrierSite(Resource):
  """ A single site within a carrier. """

  def __init__(self, name: str, size_x: float, size_y: float, size_z: float,
    category: str = "carrier_site", model: Optional[str] = None):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z,
      category=category, model=model)
    self.resource: Optional[Resource] = None

  def assign_child_resource(
    self,
    resource: Resource,
    location: Coordinate = Coordinate.zero(),
    reassign: bool = True
  ):
    self.resource = resource
    return super().assign_child_resource(resource, location=location)

  def unassign_child_resource(self, resource):
    self.resource = None
    return super().unassign_child_resource(resource)

  def __eq__(self, other):
    return super().__eq__(other) and self.resource == other.resource


S = TypeVar("S", bound=Resource)


class Carrier(Resource, Generic[S]):
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

  def __init__(
    self,
    name: str,
    size_x: float, size_y: float, size_z: float,
    sites: Optional[List[S]] = None,
    category: Optional[str] = "carrier",
    model: Optional[str] = None):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)

    sites = sites or []

    self.sites: List[CarrierSite] = []
    for spot, site in enumerate(sites):
      site.name = f"carrier-{self.name}-spot-{spot}"
      if site.location is None:
        raise ValueError(f"site {site} has no location")
      self.assign_child_resource(site, location=site.location)

  @property
  def capacity(self):
    return len(self.sites)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Coordinate,
    reassign: bool = True
  ):
    """ Assign a resource to this carrier.

    For a carrier, the only valid resource is a :class:`CarrierSite`.

    Also see :meth:`~Resource.assign_child_resource`.

    Raises:
      TypeError: If the resource is not a :class:`CarrierSite`.
    """

    if not isinstance(resource, CarrierSite):
      raise TypeError(f"Invalid resource {resource}")
    self.sites.append(resource)
    super().assign_child_resource(resource, location=location)

  def assign_resource_to_site(self, resource: Resource, spot: int):
    if spot < 0 or spot >= self.capacity:
      raise IndexError(f"Invalid spot {spot}")
    if self.sites[spot].resource is not None:
      raise ValueError(f"spot {spot} already has a resource")

    if not resource.rotation.y == resource.rotation.x == 0:
      raise ValueError("Resource rotation must be 0 around the x and y axis")
    if not resource.rotation.z % 90 == 0:
      raise ValueError("Resource rotation must be a multiple of 90 degrees on the z axis")
    location = {
      0.0: Coordinate(x=0, y=0, z=0),
      90.0: Coordinate(x=resource.get_size_x(), y=0, z=0),
      180.0: Coordinate(x=resource.get_size_x(), y=resource.get_size_y(), z=0),
      270.0: Coordinate(x=0, y=resource.get_size_y(), z=0),
    }[resource.rotation.z % 360]
    self.sites[spot].assign_child_resource(resource, location=location)

  def unassign_child_resource(self, resource):
    """ Unassign a resource from this carrier, checked by name.
    Also see :meth:`~Resource.assign_child_resource`

    Args:
      resource: The resource to unassign.

    Raises:
      ValueError: If the resource is not assigned to this carrier.
    """

    if not isinstance(resource.parent, CarrierSite) or not resource.parent.parent == self:
      raise ValueError(f"Resource {resource} is not assigned to this carrier")
    resource.unassign()

  def __getitem__(self, idx: int) -> CarrierSite:
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

  def get_sites(self) -> List[CarrierSite]:
    """ Get all sites. """
    return self.sites

  def __eq__(self, other):
    return super().__eq__(other) and self.sites == other.sites


class TipCarrier(Carrier):
  r""" Base class for tip carriers.
  Name prefix: 'TIP\_'
  """
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[List[CarrierSite]] = None,
    category="tip_carrier",
    model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z,
      sites,category=category, model=model)


class PlateCarrierSite(CarrierSite):
  """ A single site within a plate carrier. """
  def __init__(self, name: str, size_x: float, size_y: float, size_z: float,
               pedestal_size_z: float = None,  # type: ignore
               category="plate_carrier_site", model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z, category=category, model=model)
    if pedestal_size_z is None:
      raise ValueError("pedestal_size_z must be provided. See "
                       "https://docs.pylabrobot.org/plate_carriers.html#pedestal_size_z for more "
                       "information.")

    self.pedestal_size_z = pedestal_size_z
    self.resource: Optional[Plate] = None  # fix type
    # TODO: add self.pedestal_2D_offset if necessary in the future

  def assign_child_resource(self, resource: Resource, location: Coordinate = Coordinate.zero(),
                            reassign: bool = True):
    if not isinstance(resource, (Plate, PlateAdapter)):
      raise TypeError("PlateCarrierSite can only store Plate or PlateAdapter resources," + \
                      f" not {type(resource)}")

    if isinstance(resource, Plate):
      # Sanity check for equal well clearances / dz
      well_dz = {well.location.z for well in resource.get_all_children()}
      assert len(well_dz) == 1, "All wells must have the same dz"
      well_dz = well_dz.pop()
      # Plate "sinking" logic based on well dz to pedestal relationship
      pedestal_size_z = abs(self.pedestal_size_z)
      z_sinking_depth = min(pedestal_size_z, well_dz)
      location = Coordinate(location.x, location.y, location.z - z_sinking_depth)

    return super().assign_child_resource(resource, location, reassign)

  def serialize(self) -> dict:
    return { **super().serialize(), "pedestal_size_z": self.pedestal_size_z, }


class PlateCarrier(Carrier):
  """ Base class for plate carriers.
  Name prefix: 'PLT\_'
  """
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[List[PlateCarrierSite]] = None,
    category="plate_carrier",
    model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z,
      sites,category=category, model=model)


class MFXCarrier(Carrier):
  r""" Base class for multiflex carriers (i.e. carriers with mixed-use and/or specialized sites).
  Name prefix: 'MFX\_'
  """
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[List[CarrierSite]] = None,
    category="mfx_carrier",
    model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z,
      sites,category=category, model=model)



class TubeCarrier(Carrier):
  r""" Base class for tube/sample carriers.
  Name prefix: 'SMP\_'
  """
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[List[CarrierSite]] = None,
    category="tube_carrier",
    model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z,
      sites,category=category, model=model)


class TroughCarrier(Carrier):
  r""" Base class for reagent/trough carriers.
  Name prefix: 'RGT\_'
  """
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[List[CarrierSite]] = None,
    category="trough_carrier",
    model: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z,
      sites,category=category, model=model)


T = TypeVar("T", bound=CarrierSite)


def create_carrier_sites(
  klass: Type[T],
  locations: List[Coordinate],
  site_size_x: List[Union[float, int]],
  site_size_y: List[Union[float, int]],
  **kwargs
) -> List[T]:
  """ Create a list of carrier sites with the given sizes. """

  sites = []
  for spot, (l, x, y) in enumerate(zip(locations, site_size_x, site_size_y)):
    site = klass(
      name = f"carrier-site-{spot}",
      size_x=x, size_y=y, size_z=0,
      **kwargs)
    site.location = l
    sites.append(site)
  return sites


def create_homogeneous_carrier_sites(
  klass: Type[T],
  locations: List[Coordinate],
  site_size_x: float,
  site_size_y: float,
  **kwargs
) -> List[T]:
  """ Create a list of carrier sites with the same size. """

  n = len(locations)
  return create_carrier_sites(klass=klass, locations=locations, site_size_x=[site_size_x]*n,
                              site_size_y=[site_size_y]*n, **kwargs)

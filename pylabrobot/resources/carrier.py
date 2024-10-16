from __future__ import annotations

import logging
from typing import Dict, Generic, List, Optional, Type, TypeVar, Union

from pylabrobot.resources.resource_holder import ResourceHolder

from .coordinate import Coordinate
from .plate import Plate
from .resource import Resource
from .resource_stack import ResourceStack
from .plate_adapter import PlateAdapter

logger = logging.getLogger("pylabrobot")


class CarrierSite(ResourceHolder, Resource):
  """A single site within a carrier."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    category: str = "carrier_site",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )


S = TypeVar("S", bound=Resource)


class Carrier(Resource, Generic[S]):
  """Base class for all carriers."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[Dict[int, S]] = None,
    category: Optional[str] = "carrier",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )

    sites = sites or {}

    self.sites: Dict[int, S] = {}
    for spot, site in sites.items():
      site.name = f"carrier-{self.name}-spot-{spot}"
      if site.location is None:
        raise ValueError(f"site {site} has no location")
      self.assign_child_resource(site, location=site.location)

  @property
  def capacity(self):
    """The number of sites on this carrier."""
    return len(self.sites)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Coordinate,
    reassign: bool = True,
  ):
    # FIXME: type
    # if not isinstance(resource, CarrierSite):
    #   raise TypeError(f"Invalid resource {resource}")

    # add in order
    idx = len(self.sites)
    self.sites[idx] = resource

    super().assign_child_resource(resource, location=location)

  def assign_resource_to_site(self, resource: Resource, spot: int):
    if spot < 0 or spot >= self.capacity:
      raise IndexError(f"Invalid spot {spot}")
    if self.sites[spot].resource is not None:
      raise ValueError(f"spot {spot} already has a resource")
    self.sites[spot].assign_child_resource(resource)

  def unassign_child_resource(self, resource: Resource):
    """Unassign a resource from this carrier, checked by name.
    Also see :meth:`~Resource.assign_child_resource`

    Args:
      resource: The resource to unassign.

    Raises:
      ValueError: If the resource is not assigned to this carrier.
    """

    if not isinstance(resource.parent, CarrierSite) or not resource.parent.parent == self:
      raise ValueError(f"Resource {resource} is not assigned to this carrier")
    resource.unassign()

  def __getitem__(self, idx: int) -> S:
    """Get a site by index."""
    if not 0 <= idx < self.capacity:
      raise IndexError(f"Invalid index {idx}")
    return self.sites[idx]

  def __setitem__(self, idx: int, resource: Optional[Resource]):
    """Assign a resource to this carrier. See :meth:`~Carrier.assign_child_resource`"""
    if resource is None:
      if self[idx].resource is not None:
        self.unassign_child_resource(self[idx].resource)
    else:
      self.assign_resource_to_site(resource, spot=idx)

  def __delitem__(self, idx: int):
    """Unassign a resource from this carrier. See :meth:`~Carrier.unassign_child_resource`"""
    self.unassign_child_resource(self[idx].resource)

  def get_resources(self) -> List[Resource]:
    """Get all resources, using self.__getitem__ (so that the location is within this carrier)."""
    return [site.resource for site in self.sites if site.resource is not None]

  def __eq__(self, other):
    return super().__eq__(other) and self.sites == other.sites


class TipCarrier(Carrier):
  r"""Base class for tip carriers.
  Name prefix: 'TIP\_'
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[Dict[int, CarrierSite]] = None,
    category="tip_carrier",
    model: Optional[str] = None,
  ):
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      sites,
      category=category,
      model=model,
    )


class PlateCarrierSite(CarrierSite):
  """A single site within a plate carrier."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    pedestal_size_z: float = None,  # type: ignore
    category="plate_carrier_site",
    model: Optional[str] = None,
  ):
    super().__init__(name, size_x, size_y, size_z, category=category, model=model)
    if pedestal_size_z is None:
      raise ValueError(
        "pedestal_size_z must be provided. See "
        "https://docs.pylabrobot.org/plate_carriers.html#pedestal_size_z for more "
        "information."
      )

    self.pedestal_size_z = pedestal_size_z
    self.resource: Optional[Plate] = None  # fix type
    # TODO: add self.pedestal_2D_offset if necessary in the future

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    if isinstance(resource, ResourceStack):
      if not resource.direction == "z":
        raise ValueError("ResourceStack assigned to PlateCarrierSite must have direction 'z'")
      if not all(isinstance(c, Plate) for c in resource.children):
        raise TypeError(
          "If a ResourceStack is assigned to a PlateCarrierSite, the items "
          + f"must be Plates, not {type(resource.children[-1])}"
        )
    elif not isinstance(resource, (Plate, PlateAdapter)):
      raise TypeError(
        "PlateCarrierSite can only store Plate, PlateAdapter or ResourceStack "
        + f"resources, not {type(resource)}"
      )
    return super().assign_child_resource(resource, location, reassign)

  def _get_sinking_depth(self, resource: Resource) -> Coordinate:
    def get_plate_sinking_depth(plate: Plate):
      # Sanity check for equal well clearances / dz
      well_dz_set = {
        round(well.location.z, 2)
        for well in plate.get_all_children()
        if well.category == "well" and well.location is not None
      }
      assert len(well_dz_set) == 1, "All wells must have the same z location"
      well_dz = well_dz_set.pop()
      # Plate "sinking" logic based on well dz to pedestal relationship
      pedestal_size_z = abs(self.pedestal_size_z)
      z_sinking_depth = min(pedestal_size_z, well_dz)
      return z_sinking_depth

    z_sinking_depth = 0.0
    if isinstance(resource, Plate):
      z_sinking_depth = get_plate_sinking_depth(resource)
    elif isinstance(resource, ResourceStack) and len(resource.children) > 0:
      first_child = resource.children[0]
      if isinstance(first_child, Plate):
        z_sinking_depth = get_plate_sinking_depth(first_child)

      # TODO #246 - _get_sinking_depth should not handle callbacks
      resource.register_did_assign_resource_callback(self._update_resource_stack_location)
      self.register_did_unassign_resource_callback(self._deregister_resource_stack_callback)
    return -Coordinate(z=z_sinking_depth)

  def get_default_child_location(self, resource: Resource) -> Coordinate:
    return super().get_default_child_location(resource) + self._get_sinking_depth(resource)

  def _update_resource_stack_location(self, resource: Resource):
    """Callback called when the lowest resource on a ResourceStack changes. Since the location of
    the lowest resource on the stack wrt the ResourceStack is always 0,0,0, we need to update the
    location of the ResourceStack itself to make sure we take into account sinking of the plate.

    Args:
      resource: The Resource on the ResourceStack tht was assigned.
    """
    resource_stack = resource.parent
    assert isinstance(resource_stack, ResourceStack)
    if resource_stack.children[0] == resource:
      resource_stack.location = self.get_default_child_location(resource)

  def _deregister_resource_stack_callback(self, resource: Resource):
    """Callback called when a ResourceStack (or child) is unassigned from this PlateCarrierSite."""
    if isinstance(resource, ResourceStack):  # the ResourceStack itself is unassigned
      resource.deregister_did_assign_resource_callback(self._update_resource_stack_location)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "pedestal_size_z": self.pedestal_size_z,
    }


class PlateCarrier(Carrier):
  r"""Base class for plate carriers.
  Name prefix: 'PLT\_'
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[Dict[int, PlateCarrierSite]] = None,
    category="plate_carrier",
    model: Optional[str] = None,
  ):
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      sites,
      category=category,
      model=model,
    )


class MFXCarrier(Carrier[ResourceHolderMixin]):
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Dict[int, ResourceHolderMixin],
    category="mfx_carrier",
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


class TubeCarrier(Carrier):
  r"""Base class for tube/sample carriers.
  Name prefix: 'SMP\_'
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[Dict[int, CarrierSite]] = None,
    category="tube_carrier",
    model: Optional[str] = None,
  ):
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      sites,
      category=category,
      model=model,
    )


class TroughCarrier(Carrier):
  r"""Base class for reagent/trough carriers.
  Name prefix: 'RGT\_'
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[Dict[int, CarrierSite]] = None,
    category="trough_carrier",
    model: Optional[str] = None,
  ):
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      sites,
      category=category,
      model=model,
    )


T = TypeVar("T", bound=CarrierSite)


def create_carrier_sites(
  klass: Type[T],
  locations: List[Coordinate],
  site_size_x: List[Union[float, int]],
  site_size_y: List[Union[float, int]],
  **kwargs,
) -> Dict[int, T]:
  """Create a list of carrier sites with the given sizes."""

  sites = {}
  for spot, (location, x, y) in enumerate(zip(locations, site_size_x, site_size_y)):
    site = klass(
      name=f"carrier-site-{spot}",
      size_x=x,
      size_y=y,
      size_z=0,
      **kwargs,
    )
    site.location = location
    sites[spot] = site
  return sites


def create_homogeneous_carrier_sites(
  klass: Type[T],
  locations: List[Coordinate],
  site_size_x: float,
  site_size_y: float,
  **kwargs,
) -> Dict[int, T]:
  """Create a list of carrier sites with the same size."""

  n = len(locations)
  return create_carrier_sites(
    klass=klass,
    locations=locations,
    site_size_x=[site_size_x] * n,
    site_size_y=[site_size_y] * n,
    **kwargs,
  )

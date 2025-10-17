from __future__ import annotations

import logging
from typing import Dict, Generic, List, Optional, Type, TypeVar, Union, cast

from pylabrobot.resources.resource_holder import ResourceHolder, get_child_location

from .coordinate import Coordinate
from .plate import Plate
from .resource import Resource
from .resource_stack import ResourceStack

logger = logging.getLogger("pylabrobot")


S = TypeVar("S", bound=ResourceHolder)


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
      if site.location is None:
        raise ValueError(f"site {site} has no location")
      self.assign_child_resource(site, location=site.location + get_child_location(site), spot=spot)

  @property
  def capacity(self):
    """The number of sites on this carrier."""
    return len(self.sites)

  def __len__(self) -> int:
    """Return the number of sites on this carrier."""
    return len(self.sites)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True,
    spot: Optional[int] = None,
  ):
    if not isinstance(resource, (ResourceHolder, Carrier)):
      raise TypeError(f"Invalid resource {resource}")

    # see if we have an index for the resource name (eg from deserialization or user specification),
    # otherwise add in first available spot
    idx = spot if spot is not None else len(self.sites)
    if not reassign and self.sites[idx] is not None:
      raise ValueError(f"a site with index {idx} already exists")
    self.sites[idx] = cast(S, resource)

    super().assign_child_resource(resource, location=location, reassign=reassign)

  def assign_resource_to_site(self, resource: Resource, spot: int):
    if self.sites[spot].resource is not None:
      raise ValueError(f"spot {spot} already has a resource")
    self.sites[spot].assign_child_resource(resource)

  def unassign_child_resource(self, resource: Resource):
    """Unassign a resource from this carrier, checked by name.

    Raises:
      ValueError: If the resource is not assigned to this carrier.
    """

    if not isinstance(resource.parent, ResourceHolder) or not resource.parent.parent == self:
      raise ValueError(f"Resource {resource} is not assigned to this carrier")
    resource.unassign()

  def __getitem__(self, idx: int) -> S:
    """Get a site by index."""
    return self.sites[idx]

  def __setitem__(self, idx: int, resource: Optional[Resource]):
    """Assign a resource to this carrier."""
    if resource is None:  # setting to None
      assigned_resource = self[idx].resource
      if assigned_resource is not None:
        self.unassign_child_resource(assigned_resource)
    else:
      self.assign_resource_to_site(resource, spot=idx)

  def __delitem__(self, idx: int):
    """Unassign a resource from this carrier."""
    assigned_resource = self[idx].resource
    if assigned_resource is not None:
      self.unassign_child_resource(assigned_resource)

  def get_resources(self) -> List[Resource]:
    """Get all resources, using self.__getitem__ (so that the location is within this carrier)."""
    all_resources = [site.resource for site in self.sites.values()]
    return [resource for resource in all_resources if resource is not None]

  def __eq__(self, other):
    return super().__eq__(other) and self.sites == other.sites

  def get_free_sites(self) -> List[S]:
    return [site for site in self.sites.values() if site.resource is None]


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
    sites: Optional[Dict[int, ResourceHolder]] = None,
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


class PlateHolder(ResourceHolder):
  """A single site within a plate carrier."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    pedestal_size_z: float = None,  # type: ignore
    child_location=Coordinate.zero(),
    category="plate_holder",
    model: Optional[str] = None,
  ):
    super().__init__(
      name, size_x, size_y, size_z, category=category, model=model, child_location=child_location
    )
    if pedestal_size_z is None:
      raise ValueError(
        "pedestal_size_z must be provided. See "
        "https://docs.pylabrobot.org/resources/resource-holder/plate-holder.html#pedestal-z-height for more "
        "information."
      )

    self.pedestal_size_z = pedestal_size_z
    self.resource: Optional[Plate]  # fix type
    # TODO: add self.pedestal_2D_offset if necessary in the future

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    if isinstance(resource, ResourceStack):
      if not resource.direction == "z":
        raise ValueError("ResourceStack assigned to PlateHolder must have direction 'z'")
      if not all(isinstance(c, Plate) for c in resource.children):
        raise TypeError(
          "If a ResourceStack is assigned to a PlateHolder, the items "
          + f"must be Plates, not {type(resource.children[-1])}"
        )
    if isinstance(resource, Plate) and resource.plate_type != "skirted":
      raise ValueError("PlateHolder can only store plates that are skirted")
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
      resource: The Resource on the ResourceStack that was assigned.
    """
    resource_stack = resource.parent
    assert isinstance(resource_stack, ResourceStack)
    if resource_stack.children[0] == resource:
      resource_stack.location = self.get_default_child_location(resource)

  def _deregister_resource_stack_callback(self, resource: Resource):
    """Callback called when a ResourceStack (or child) is unassigned from this PlateHolder."""
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
    sites: Optional[Dict[int, PlateHolder]] = None,
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
    self.sites: Dict[int, PlateHolder] = sites or {}  # fix type

  def summary(self) -> str:
    """Return a summary of the carrier's sites and their contents."""

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

    indices = sorted(self.sites.keys())
    header = ["Site", "Content"]
    site_numbers = list(reversed([str(i) for i in indices]))
    site_resources = list(reversed([self.sites[i].resource for i in indices]))
    site_contents = [r.name if r is not None else "<empty>" for r in site_resources]
    return create_pretty_table(header, site_numbers, site_contents)


class MFXCarrier(Carrier[ResourceHolder]):
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[Dict[int, ResourceHolder]] = None,
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
    sites: Optional[Dict[int, ResourceHolder]] = None,
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
    sites: Optional[Dict[int, ResourceHolder]] = None,
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


T = TypeVar("T", bound=ResourceHolder)


def create_resources(
  klass: Type[T],
  locations: List[Coordinate],
  resource_size_x: List[Union[float, int]],
  resource_size_y: List[Union[float, int]],
  resource_size_z: Optional[List[Union[float, int]]] = None,
  name_prefix: Optional[str] = None,
  **kwargs,
) -> Dict[int, T]:
  """Create a list of resource with the given sizes and locations.

  Args:
    klass: The class of the resources.
    locations: The locations of the resources.
    resource_size_x: The x size of the resources.
    resource_size_y: The y size of the resources.
    resource_size_z: The z size of the resources. If None, it will be set to 0.
    name_prefix: names of the resources will be f"{name_prefix}-{idx}" if name_prefix is not None,
      else f"{klass.__name__}_{idx}".
  """
  # TODO: should be possible to merge with create_equally_spaced_y

  if resource_size_z is None:
    resource_size_z = [0] * len(locations)

  sites = {}
  for idx, (location, x, y, z) in enumerate(
    zip(locations, resource_size_x, resource_size_y, resource_size_z)
  ):
    site = klass(
      name=f"{name_prefix}-{idx}" if name_prefix else f"{klass.__name__}_{idx}",
      size_x=x,
      size_y=y,
      size_z=z,
      **kwargs,
    )
    site.location = location
    sites[idx] = site
  return sites


def create_homogeneous_resources(
  klass: Type[T],
  locations: List[Coordinate],
  resource_size_x: float,
  resource_size_y: float,
  resource_size_z: Optional[float] = None,
  name_prefix: Optional[str] = None,
  **kwargs,
) -> Dict[int, T]:
  """Create a list of resources with the same size at specified locations."""
  # TODO: should be possible to merge with create_equally_spaced_y

  n = len(locations)
  return create_resources(
    klass=klass,
    locations=locations,
    resource_size_x=[resource_size_x] * n,
    resource_size_y=[resource_size_y] * n,
    resource_size_z=[resource_size_z] * n if resource_size_z is not None else None,
    name_prefix=name_prefix,
    **kwargs,
  )

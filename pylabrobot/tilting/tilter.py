import math
from typing import List, Optional

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, Plate, Resource
from pylabrobot.resources.carrier import CarrierSite

from .tilter_backend import TilterBackend

class Tilter(Machine):
  """A tilt module with multiple sites"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: Optional[List[CarrierSite]] = None,
    backend: TilterBackend = None,
    hinge_coordinate: Coordinate = None,
    category: str = "tilter",
    model: Optional[str] = None,
  ):
    """
    Initialize a RetroTilter instance.

    Args:
      name (str): The name of the tilter.
      size_x (float): The size of the tilter in the x dimension.
      size_y (float): The size of the tilter in the y dimension.
      size_z (float): The size of the tilter in the z dimension. Make sure to include a buffer for tilting to avoid collisions.
      sites (Optional[List[CarrierSite]]): A list of carrier sites for the tilter.
      backend (TilterBackend): The backend to use for the tilter.
      hinge_coordinate (Coordinate): The coordinate of the hinge center relative to the origin of the tilter. Only x and z are used.
      category (str): The category of the tilter. Default is "tilter".
      model (Optional[str]): The model of the tilter.
    """
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, backend=backend, category=category, model=model
    )
    if backend is None:
      raise ValueError("backend must be provided")

    if hinge_coordinate is None:
      raise ValueError("hinge_coordinate must be provided")

    if sites is None:
      raise ValueError("sites must be provided")

    self.backend: TilterBackend = backend  # fix type
    self._absolute_angle: int = 0
    self._hinge_coordinate = hinge_coordinate
    self._size_x = size_x
    self.size_y = size_y
    self.size_z = size_z

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

  def get_plate(self, position: int) -> Plate:
    """Get the plate that is currently attached to the tilt module at a given position. If no plate is assigned, raise
    a RuntimeError."""

    if self.children[position].children == None:
      raise RuntimeError(f"No plate on this tilt module at position {position}.")

    return self.children[position].children[0]

  def assign_child_resource(self, resource: Resource, location: Coordinate, reassign: bool = True):
    if not isinstance(resource, CarrierSite):
      raise TypeError(f"Invalid resource {resource}")

    self.sites.append(resource)
    super().assign_child_resource(resource, location=location, reassign=reassign)

  def assign_resource_to_site(self, resource: Resource, position: int):
    if position < 0 or position >= self.capacity:
      raise IndexError(f"Invalid spot {position}")
    if self.sites[position].resource is not None:
      raise ValueError(f"spot {position} already has a resource")
    self.sites[position].assign_child_resource(resource, location=Coordinate.zero())

  def unassign_child_resource(self, resource):
    """Unassign a resource from this tilter, checked by name.
    Also see :meth:`~Resource.assign_child_resource`

    Args:
      resource: The resource to unassign.

    Raises:
      ValueError: If the resource is not assigned to this tilter.
    """

    if resource not in self.children:
      raise ValueError(f"Resource {resource} is not assigned to this tilter")
    resource.unassign()

  def __getitem__(self, idx: int) -> Plate:
    """Get a plate by index."""
    return self.get_plate(idx)

  def __setitem__(self, idx, resource: Optional[Resource]):
    """Assign a resource to this tilter. See :meth:`~Tilter.assign_child_resource`"""
    if resource is None:
      if self[idx] is not None:
        self.unassign_child_resource(self[idx])
    else:
      self.assign_resource_to_site(resource, position=idx)

  def __delitem__(self, idx):
    """Unassign a resource from this tilter. See :meth:`~Tilter.unassign_child_resource`"""
    self.unassign_child_resource(self[idx])

  def get_resources(self) -> List[Resource]:
    """Get all resources, using self.__getitem__ (so that the location is within this tilter)."""
    return [self[idx] for idx in range(self.capacity) if self[idx] is not None]

  @property
  def absolute_angle(self) -> int:
    return self._absolute_angle

  async def set_angle(self, absolute_angle: int):
    """Set the tilt module to rotate to a given angle.
    Args:
      angle: The absolute angle to set rotation to, in degrees, measured from horizontal as zero.
    """
    # if the hinge is on the left side of the tilter, the angle is kept positive
    # else, the angle is converted to negative. this follows Euler angle conventions.
    # This conversion is done to simplify coordinate calculations.
    angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle
    await self.backend.set_angle(angle=abs(angle))
    self._absolute_angle = absolute_angle

  def rotate_coordinate_around_hinge(self, absolute_coordinate: Coordinate, angle: int) -> Coordinate:
    """
    Rotate an absolute coordinate around the hinge of the tilter by a given angle.

    Args:
      absolute_coordinate (Coordinate): The coordinate to rotate.
      angle (int): The angle to rotate by, in degrees. Negative is clockwise according to Euler conventions.

    Returns:
      Coordinate: The new coordinate after rotation.
    """
    theta = math.radians(angle)

    rotation_arm_x = absolute_coordinate.x - (
      self._hinge_coordinate.x + self.get_absolute_location("l", "f", "b").x
    )
    rotation_arm_z = absolute_coordinate.z - (
      self._hinge_coordinate.z + self.get_absolute_location("l", "f", "b").z
    )

    x_prime = rotation_arm_x * math.cos(theta) - rotation_arm_z * math.sin(theta)
    z_prime = rotation_arm_x * math.sin(theta) + rotation_arm_z * math.cos(theta)

    new_x = x_prime + (self._hinge_coordinate.x + self.get_absolute_location("l", "f", "b").x)
    new_z = z_prime + (self._hinge_coordinate.z + self.get_absolute_location("l", "f", "b").z)

    return Coordinate(new_x, absolute_coordinate.y, new_z)

  def return_well_drain_offsets(self, position: int, absolute_angle: int = None):
    """
    Return the edge offset for the wells in the plate at a given position, rotated at a given absolute angle.

    Args:
      position (int): The position index of the plate.
      absolute_angle (int, optional): The absolute angle to rotate the plate. Defaults to None.

    Returns:
      List[Coordinate]: A list of offsets for the wells in the plate.
    """

    if absolute_angle is None:
      angle = self._absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -self._absolute_angle
    else:
      angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle

    _hinge_side = "l" if self._hinge_coordinate.x < self._size_x / 2 else "r"

    offsets = []
    for well in self.get_plate(position).children:
      zero_absolute_well_drain_coordinate = well.get_absolute_location(_hinge_side, "c", "b")
      print(zero_absolute_well_drain_coordinate)
      rotated_absolute_well_drain_coordinate = self.rotate_coordinate_around_hinge(
        zero_absolute_well_drain_coordinate, angle
      )
      print(rotated_absolute_well_drain_coordinate)
      drain_offset = rotated_absolute_well_drain_coordinate - well.get_absolute_location("c", "c", "b")
      offsets.append(drain_offset)

    return offsets

  async def tilt(self, angle: int):
    """Tilt the plate contained in the tilt module by a given angle relative to the current angle.

    Args:
    angle: The angle to rotate by, in degrees. Clockwise. 0 is horizontal.
    """

    await self.set_angle(self._absolute_angle + angle)
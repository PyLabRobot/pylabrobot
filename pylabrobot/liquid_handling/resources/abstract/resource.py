from __future__ import annotations

import copy
from typing import List, Optional, TypeVar

from .coordinate import Coordinate

Self = TypeVar("Self", bound="Resource")


class Resource:
  """ Base class for deck resources.

  Args:
    name: The name of the resource.
    size_x: The size of the resource in the x-direction.
    size_y: The size of the resource in the y-direction.
    size_z: The size of the resource in the z-direction.
    location: The location of the resource, relative to its parent.
      (see :meth:`get_absolute_location`)
    category: The category of the resource, e.g. `tips`, `plate_carrier`, etc.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    category: Optional[str] = None,
    model: Optional[str] = None
  ):
    self.name = name
    self._size_x = size_x
    self._size_y = size_y
    self._size_z = size_z
    self.category = category
    self.model = model

    self.location: Optional[Coordinate] = None
    self.parent: Optional[Resource] = None
    self.children: List[Resource] = []

    self.rotation = 0

  def serialize(self) -> dict:
    """ Serialize this resource. """
    return dict(
      name=self.name,
      type=self.__class__.__name__,
      size_x=self._size_x,
      size_y=self._size_y,
      size_z=self._size_z,
      location=self.location.serialize() if self.location is not None else None,
      category=self.category,
      children=[child.serialize() for child in self.children],
      parent_name=self.parent.name if self.parent is not None else None
    )

  @classmethod
  def deserialize(cls, data: dict) -> Resource:
    """ Deserialize this resource from a dictionary. """

    data_copy = data.copy()
    # remove keys that are already present in the definition or that we added in the serialization
    for key in ["type", "children", "parent_name", "location"]:
      del data_copy[key]
    return cls(**data_copy)

  def copy(self):
    """ Copy this resource. """
    if self.parent is not None:
      raise ValueError("Cannot copy a resource that is assigned to another resource.")

    return copy.deepcopy(self)

  def __eq__(self, other):
    return (
      isinstance(other, Resource) and
      self.name == other.name and
      self.get_size_x() == other.get_size_x() and
      self.get_size_y() == other.get_size_y() and
      self.get_size_z() == other.get_size_z() and
      self.location == other.location and
      self.category == other.category and
      self.children == other.children
    )

  def __repr__(self) -> str:
    return f"{self.__class__.__name__}(name={self.name}, location={self.location}, " \
           f"size_x={self._size_x}, size_y={self._size_y}, size_z={self._size_z}, " \
           f"category={self.category})"

  def __hash__(self) -> int:
    return hash(repr(self))

  def get_absolute_location(self) -> Coordinate:
    """ Get the absolute location of this resource, probably within the
    :class:`pylabrobot.liquid_handling.resources.abstract.Deck`. """
    assert self.location is not None, "Resource has no location."
    if self.parent is None or self.parent.location is None:
      return self.location
    return self.parent.get_absolute_location() + self.location

  def get_size_x(self) -> float:
    if self.rotation in {90, 270}:
      return self._size_y
    return self._size_x

  def get_size_y(self) -> float:
    if self.rotation in {90, 270}:
      return self._size_x
    return self._size_y

  def get_size_z(self) -> float:
    """ Get the size of this resource in the z-direction. """
    return self._size_z

  def assign_child_resource(self, resource: Resource, location: Coordinate):
    """ Assign a child resource to this resource.

    Will use :meth:`~Resource.resource_assigned_callback` to notify the parent of the assignment,
    if parent is not `None`.  Note that the resource to be assigned may have child resources, in
    which case you will be responsible for handling any checking, if necessary.
    """

    resource.parent = self
    resource.location = location
    try:
      self.resource_assigned_callback(resource) # call callbacks first.
    except Exception as e:
      resource.parent = None
      resource.location = None
      raise e

    self.children.append(resource)

  def unassign_child_resource(self, resource: Resource):
    """ Unassign a child resource from this resource.

    Will use :meth:`~Resource.resource_unassigned_callback` to notify the parent of the
    unassignment, if parent is not `None`.
    """

    if resource not in self.children:
      raise ValueError(f"Resource with name '{resource.name}' is not a child of this resource "
                       f"('{self.name}').")

    self.resource_unassigned_callback(resource) # call callbacks first.
    resource.parent = None
    self.children.remove(resource)

  def unassign(self):
    """ Unassign this resource from its parent. """
    if self.parent is not None:
      self.parent.unassign_child_resource(self)

  def resource_assigned_callback(self, resource):
    """ Called when a resource is assigned to this resource.

    May be overridden by subclasses.

    May raise an exception if the resource cannot be assigned to this resource.

    Args:
      resource: The resource that was assigned.
    """

    if self.parent is not None:
      self.parent.resource_assigned_callback(resource)

  def resource_unassigned_callback(self, resource):
    """ Called when a resource is unassigned from this resource.

    May be overridden by subclasses.

    May raise an exception if the resource cannot be unassigned from this resource.

    Args:
      resource: The resource that was unassigned.
    """

    if self.parent is not None:
      self.parent.resource_unassigned_callback(resource)

  def get_all_children(self) -> List[Resource]:
    """ Recursively get all children of this resource. """
    children = self.children.copy()
    for child in self.children:
      children += child.get_all_children()
    return children

  def get_resource(self, name: str) -> Resource:
    """ Get a resource by name.

    Args:
      name: The name of the resource to get.

    Returns:
      The resource with the given name.

    Raises:
      ValueError: If no resource with the given name exists.
    """

    if self.name == name:
      return self

    for child in self.children:
      try:
        return child.get_resource(name)
      except ValueError:
        pass

    raise ValueError(f"Resource with name '{name}' does not exist.")

  def get_2d_center_offsets(self, n: int = 1) -> List[Coordinate]:
    """ Get the offsets (from bottom left) of the center(s) of this resoure. If `n` is greater than
    one, the offsets are equally spaced along a column (the y axis), all having the same x and z
    coordinates. The z coordinate is the bottom of the resource. """

    dx = self.get_size_x() / 2
    dy = self.get_size_y() / (n+1)
    if dy < 9: # TODO: too specific?
      raise ValueError(f"Resource is too small to space {n} channels evenly.")
    offsets = [Coordinate(dx, dy * (i+1), 0) for i in range(n)]
    return offsets

  def rotate(self, degrees: int):
    """ Rotate counter clockwise by the given number of degrees.

    Args:
      degrees: must be a multiple of 90, but not also 360.
    """

    effective_degrees = degrees % 360

    if effective_degrees == 0 or effective_degrees % 90 != 0:
      raise ValueError(f"Invalid rotation: {degrees}")

    for child in self.children:
      assert child.location is not None, "child must have a location when it's assigned."

      old_x = child.location.x

      if effective_degrees == 90:
        child.location.x = self.get_size_y() - child.location.y - child.get_size_y()
        child.location.y = old_x
      elif effective_degrees == 180:
        child.location.x = self.get_size_x() - child.location.x - child.get_size_x()
        child.location.y = self.get_size_y() - child.location.y - child.get_size_y()
      elif effective_degrees == 270:
        child.location.x = child.location.y
        child.location.y = self.get_size_x() - old_x - child.get_size_x()
      child.rotate(effective_degrees)

    self.rotation = (self.rotation + degrees) % 360

  def rotated(self: Self, degrees: int) -> Self:
    """ Return a copy of this resource rotated by the given number of degrees.

    Args:
      degrees: must be a multiple of 90, but not also 360.
    """

    new_resource = copy.deepcopy(self)
    new_resource.rotate(degrees)
    return new_resource

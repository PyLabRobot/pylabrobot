from __future__ import annotations

from typing import List, Optional

from .coordinate import Coordinate


class Resource(object):
  """ Abstract base class for deck resources.

  Args:
    name: The name of the resource.
    size_x: The size of the resource in the x-direction.
    size_y: The size of the resource in the y-direction.
    size_z: The size of the resource in the z-direction.
    location: The location of the resource.
    category: The category of the resource, e.g. `tips`, `plate_carrier`, etc.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    location: Coordinate = Coordinate(None, None, None),
    category: str = None
  ):
    self.name = name
    self.size_x = size_x
    self.size_y = size_y
    self.size_z = size_z
    self.location = location
    self.category = category

    self.parent: Optional[Resource] = None
    self.children: List[Resource] = []

  def serialize(self) -> dict:
    """ Serialize this resource. """
    return dict(
      name=self.name,
      type=self.__class__.__name__,
      size_x=self.size_x,
      size_y=self.size_y,
      size_z=self.size_z,
      location=self.location.serialize(),
      category=self.category or "unknown"
    )

  def __eq__(self, other):
    return (
      isinstance(other, Resource) and
      self.name == other.name and
      self.size_x == other.size_x and
      self.size_y == other.size_y and
      self.size_z == other.size_z and
      self.location == other.location and
      self.category == other.category
    )

  def get_absolute_location(self):
    """ Get the absolute location of this resource, probably within the :class:`~Deck`. """
    if self.parent is None:
      return self.location
    return self.parent.get_absolute_location() + self.location

  def assign_child_resource(self, resource, **kwargs):
    """ Assign a child resource to this resource.

    Will use :method:`~Resource.resource_assigned_callback` to notify the parent of the assignment,
    if parent is not `None`.  If the resource to be assigned has child resources, this method will
    be called for each of them.
    """

    self.resource_assigned_callback(resource) # call callbacks first.

    for child in resource.children:
      self.resource_assigned_callback(child)

    resource.parent = self
    self.children.append(resource)

  def unassign_child_resource(self, resource):
    """ Unassign a child resource from this resource.

    Will use :method:`~Resource.resource_unassigned_callback` to notify the parent of the
    unassignment, if parent is not `None`.
    """

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

  def get_resource(self, name: str) -> Optional[Resource]:
    """ Get a resource by name. """
    if self.name == name:
      return self

    for child in self.children:
      if child.name == name:
        return child

      resource = child.get_resource(name)
      if resource is not None:
        return resource

    return None

from __future__ import annotations

import copy
import json
import logging
import sys
from typing import List, Optional, Type, cast

from .coordinate import Coordinate
from pylabrobot.serializer import serialize, deserialize

if sys.version_info >= (3, 11):
  from typing import Self
else:
  from typing_extensions import Self

logger = logging.getLogger("pylabrobot")


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
    self._name = name
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
    return {
      "name": self.name,
      "type": self.__class__.__name__,
      "size_x": self._size_x,
      "size_y": self._size_y,
      "size_z": self._size_z,
      "location": serialize(self.location),
      "category": self.category,
      "model": self.model,
      "children": [child.serialize() for child in self.children],
      "parent_name": self.parent.name if self.parent is not None else None
    }

  @property
  def name(self) -> str:
    """ Get the name of this resource. """
    return self._name

  @name.setter
  def name(self, name: str):
    """ Set the name of this resource.

    Will raise a `RuntimeError` if the resource is assigned to another resource.
    """

    if self.parent is not None:
      raise RuntimeError("Cannot change the name of a resource that is assigned.")
    self._name = name

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
    :class:`pylabrobot.resources.Deck`. """
    assert self.location is not None, "Resource has no location."
    if self.parent is None:
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

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True):
    """ Assign a child resource to this resource.

    Will use :meth:`~Resource.resource_assigned_callback` to notify the parent of the assignment,
    if parent is not `None`. Note that the resource to be assigned may have child resources, in
    which case you will be responsible for handling any checking, if necessary.

    Args:
      resource: The resource to assign.
      location: The location of the resource, relative to this resource.
      reassign: If `False`, an error will be raised if the resource to be assigned is already
        assigned to this resource. Defaults to `True`.
    """

    # Check for unsupported resource assignment operations
    self._check_assignment(resource=resource, reassign=reassign)

    resource.parent = self
    resource.location = location

    try:
      self.resource_assigned_callback(resource) # call callbacks first.
    except Exception as e:
      resource.parent = None
      resource.location = None
      raise e

    self.children.append(resource)

  def _check_assignment(self, resource: Resource, reassign: bool = True):
    """ Check if the resource assignment produces unsupported or dangerous conflicts. """
    msgs = []

    # Check for self assignment
    if resource is self:
      msgs.append(f"Will not assign resource '{self.name}' to itself.")

    # Check for reassignment to the same (non-null) parent
    if (resource.parent is not None) and (resource.parent is self):
      if reassign:
        # Inform the user that this is redundant.
        logger.warning("Resource '%s' already assigned to '%s'", resource.name,
          resource.parent.name)
      else:
        # Else raise an error.
        msgs.append(f"Will not reassign resource '{resource.name}' " +
                    f"to the same parent: '{resource.parent.name}'.")

    # Check for pre-existing parent
    if resource.parent is not None and not reassign:
      msgs.append(f"Will not assign resource '{resource.name}' " +
                  f"that already has a parent: '{resource.parent.name}'.")

    # TODO: write other checks, perhaps recursive or location checks.

    if len(msgs) > 0:
      msg = " ".join(msgs)
      raise ValueError(msg)

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
    """ Get the offsets (from bottom left) of the center(s) of this resource.

    If `n` is greater than one, the offsets are equally spaced along a column (the y axis), all
    having the same x and z coordinates. The z coordinate is the bottom of the resource.

    The offsets are returned from high y (back) to low y (front).
    """

    dx = self.get_size_x() / 2
    dy = self.get_size_y() / (n+1)
    offsets = [Coordinate(dx, dy * (i+1), 0) for i in range(n)]
    return list(reversed(offsets))

  def get_2d_center_offset(self) -> Coordinate:
    """ Get the offset (from bottom left) of the center of this resource. """
    return self.get_2d_center_offsets()[0]

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

  def rotated(self, degrees: int) -> Self: # type: ignore
    """ Return a copy of this resource rotated by the given number of degrees.

    Args:
      degrees: must be a multiple of 90, but not also 360.
    """

    new_resource = copy.deepcopy(self)
    new_resource.rotate(degrees)
    return new_resource

  def center(self) -> Coordinate:
    """ Get the center of the bottom plane of this resource. """

    return Coordinate(self.get_size_x() / 2, self.get_size_y() / 2, 0)

  def save(self, fn: str, indent: Optional[int] = None):
    """ Save a resource to a JSON file.

    Args:
      fn: File name. Caution: file will be overwritten.
      indent: Same as `json.dump`'s `indent` argument (for json pretty printing).

    Examples:
      Saving to a json file:

      >>> from pylabrobot.resources.hamilton import STARLetDeck
      >>> deck = STARLetDeck()
      >>> deck.save("my_layout.json")
    """

    serialized = self.serialize()
    with open(fn, "w", encoding="utf-8") as f:
      json.dump(serialized, f, indent=indent)

  @classmethod
  def deserialize(cls, data: dict) -> Self:
    """ Deserialize a resource from a dictionary.

    Examples:
      Loading a resource from a json file:

      >>> from pylabrobot.resources import Resource
      >>> with open("my_resource.json", "r") as f:
      >>>   content = json.load(f)
      >>> resource = Resource.deserialize(content)
    """

    data_copy = data.copy() # copy data because we will be modifying it

    subclass = get_resource_class_from_string(data["type"])
    if subclass is None:
      raise ValueError(f"Could not find subclass with name '{data['type']}'")
    assert issubclass(subclass, cls) # mypy does not know the type after the None check...

    for key in ["type", "parent_name", "location"]: # delete meta keys
      del data_copy[key]
    children_data = data_copy.pop("children")
    resource = subclass(**data_copy)

    for child_data in children_data:
      child_cls = get_resource_class_from_string(child_data["type"])
      if child_cls is None:
        raise ValueError(f"Could not find subclass with name {child_data['type']}")
      child = child_cls.deserialize(child_data)
      location_data = child_data.get("location", None)
      if location_data is not None:
        location = cast(Coordinate, deserialize(location_data))
      else:
        location = None
      resource.assign_child_resource(child, location=location)

    return resource

  @classmethod
  def load_from_json_file(cls, json_file: str) -> Self: # type: ignore
    """ Loads resources from a JSON file.

    Args:
      json_file: The path to the JSON file.

    Examples:
      Loading a resource from a json file:

      >>> from pylabrobot.resources import Resource
      >>> resource = Resource.deserialize("my_resource.json")
    """

    with open(json_file, "r", encoding="utf-8") as f:
      content = json.load(f)

    return cls.deserialize(content)


def get_resource_class_from_string(
  class_name: str,
  cls: Type[Resource] = Resource
) -> Optional[Type[Resource]]:
  """ Recursively find a subclass with the correct name.

  Args:
    class_name: The name of the class to find.
    cls: The class to search in.

  Returns:
    The class with the given name, or `None` if no such class exists.
  """

  if cls.__name__ == class_name:
    return cls
  for subclass in cls.__subclasses__():
    subclass_ = get_resource_class_from_string(class_name=class_name, cls=subclass)
    if subclass_ is not None:
      return subclass_
  return None

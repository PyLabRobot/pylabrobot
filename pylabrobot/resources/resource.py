from __future__ import annotations

import itertools
import json
import logging
import sys
from typing import Any, Callable, Dict, List, Optional, cast

from .coordinate import Coordinate
from .errors import ResourceNotFoundError
from .rotation import Rotation
from pylabrobot.serializer import serialize, deserialize
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3
from pylabrobot.utils.object_parsing import find_subclass

if sys.version_info >= (3, 11):
  from typing import Self
else:
  from typing_extensions import Self

logger = logging.getLogger("pylabrobot")


WillAssignResourceCallback = Callable[["Resource"], None]
DidAssignResourceCallback = Callable[["Resource"], None]
WillUnassignResourceCallback = Callable[["Resource"], None]
DidUnassignResourceCallback = Callable[["Resource"], None]
ResourceDidUpdateState = Callable[[Dict[str, Any]], None]


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
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    self._name = name
    self._size_x = size_x
    self._size_y = size_y
    self._size_z = size_z
    self._local_size_z = size_z
    self.rotation = rotation or Rotation()
    self.category = category
    self.model = model

    self.location: Optional[Coordinate] = None
    self.parent: Optional[Resource] = None
    self.children: List[Resource] = []

    self._will_assign_resource_callbacks: List[WillAssignResourceCallback] = []
    self._did_assign_resource_callbacks: List[DidAssignResourceCallback] = []
    self._will_unassign_resource_callbacks: List[WillUnassignResourceCallback] = []
    self._did_unassign_resource_callbacks: List[DidUnassignResourceCallback] = []
    self._resource_state_updated_callbacks: List[ResourceDidUpdateState] = []

  def get_size_x(self) -> float:
    """ Local size in the x direction. """
    return self._size_x

  def get_size_y(self) -> float:
    """ Local size in the y direction. """
    return self._size_y

  def get_size_z(self) -> float:
    """ Local size in the z direction. """
    return self._local_size_z

  def serialize(self) -> dict:
    """ Serialize this resource. """
    return {
      "name": self.name,
      "type": self.__class__.__name__,
      "size_x": self._size_x,
      "size_y": self._size_y,
      "size_z": self._size_z,
      "location": serialize(self.location),
      "rotation": serialize(self.rotation),
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

  def __eq__(self, other):
    return (
      isinstance(other, Resource) and
      self.name == other.name and
      self.get_absolute_size_x() == other.get_absolute_size_x() and
      self.get_absolute_size_y() == other.get_absolute_size_y() and
      self.get_absolute_size_z() == other.get_absolute_size_z() and
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

  def get_anchor(self, x: str, y: str, z: str) -> Coordinate:
    """ Get a relative location within the resource.

    Args:
      x: `"l"`/`"left"`, `"c"`/`"center"`, or `"r"`/`"right"`
      y: `"b"`/`"back"`, `"c"`/`"center"`, or `"f"`/`"front"`
      z: `"t"`/`"top"`, `"c"`/`"center"`, or `"b"`/`"bottom"`

    Returns:
      A relative location within the resource, the anchor point wrt the left front bottom corner.

    Examples:
      >>> r = Resource("resource", size_x=12, size_y=12, size_z=12)
      >>> r.get_anchor("l", "b", "t")

      Coordinate(x=0.0, y=12.0, z=12.0)

      >>> r.get_anchor("c", "c", "c")

      Coordinate(x=6.0, y=6.0, z=6.0)

      >>> r.get_anchor("r", "f", "b")

      Coordinate(x=12.0, y=0.0, z=0.0)
    """

    x_: float
    if x.lower() in {"l", "left"}:
      x_ = 0
    elif x.lower() in {"c", "center"}:
      x_ = self.get_size_x() / 2
    elif x.lower() in {"r", "right"}:
      x_ = self.get_size_x()
    else:
      raise ValueError(f"Invalid x value: {x}")

    y_: float
    if y.lower() in {"b", "back"}:
      y_ = self.get_size_y()
    elif y.lower() in {"c", "center"}:
      y_ = self.get_size_y() / 2
    elif y.lower() in {"f", "front"}:
      y_ = 0
    else:
      raise ValueError(f"Invalid y value: {y}")

    z_: float
    if z.lower() in {"t", "top"}:
      z_ = self.get_size_z()
    elif z.lower() in {"c", "center"}:
      z_ = self.get_size_z() / 2
    elif z.lower() in {"b", "bottom"}:
      z_ = 0
    else:
      raise ValueError(f"Invalid z value: {z}")

    return Coordinate(x_, y_, z_)

  def get_absolute_rotation(self) -> Rotation:
    """ Get the absolute rotation of this resource. """
    if self.parent is None:
      return self.rotation
    return self.parent.get_absolute_rotation() + self.rotation

  def get_absolute_location(self, x: str = "l", y: str = "f", z: str = "b") -> Coordinate:
    """ Get the absolute location of this resource, probably within the
    :class:`pylabrobot.resources.Deck`. The `x`, `y`, and `z` arguments specify the anchor point
    within the resource. The default is the left front bottom corner.

    Args:
      x: `"l"`/`"left"`, `"c"`/`"center"`, or `"r"`/`"right"`
      y: `"b"`/`"back"`, `"c"`/`"center"`, or `"f"`/`"front"`
      z: `"t"`/`"top"`, `"c"`/`"center"`, or `"b"`/`"bottom"`
    """

    assert self.location is not None, "Resource has no location."
    if self.parent is None:
      return self.location
    parent_pos = self.parent.get_absolute_location()

    rotated_location = Coordinate(*matrix_vector_multiply_3x3(
        self.parent.get_absolute_rotation().get_rotation_matrix(),
        self.location.vector()
    ))
    rotated_anchor = Coordinate(*matrix_vector_multiply_3x3(
        self.get_absolute_rotation().get_rotation_matrix(),
        self.get_anchor(x=x, y=y, z=z).vector()
    ))
    return parent_pos + rotated_location + rotated_anchor

  def _get_rotated_corners(self) -> List[Coordinate]:
    absolute_rotation = self.get_absolute_rotation()
    rot_mat = absolute_rotation.get_rotation_matrix()
    return [
      Coordinate(*matrix_vector_multiply_3x3(rot_mat, corner.vector()))
      for corner in [
        Coordinate(0, 0, 0),
        Coordinate(self.get_size_x(), 0, 0),
        Coordinate(0, self.get_size_y(), 0),
        Coordinate(self.get_size_x(), self.get_size_y(), 0),
        Coordinate(0, 0, self.get_size_z()),
        Coordinate(self.get_size_x(), 0, self.get_size_z()),
        Coordinate(0, self.get_size_y(), self.get_size_z()),
        Coordinate(self.get_size_x(), self.get_size_y(), self.get_size_z())
      ]
    ]

  def get_absolute_size_x(self) -> float:
    """ Get the absolute size in the x direction. """
    rotated_corners = self._get_rotated_corners()
    return max(c.x for c in rotated_corners) - min(c.x for c in rotated_corners)

  def get_absolute_size_y(self) -> float:
    """ Get the absolute size in the y direction. """
    rotated_corners = self._get_rotated_corners()
    return max(c.y for c in rotated_corners) - min(c.y for c in rotated_corners)

  def get_absolute_size_z(self) -> float:
    """ Get the absolute size in the z direction. """
    rotated_corners = self._get_rotated_corners()
    return max(c.z for c in rotated_corners) - min(c.z for c in rotated_corners)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Coordinate,
    reassign: bool = True):
    """ Assign a child resource to this resource.

    Before the resource is assigned, all callbacks registered with
    :meth:`~Resource.register_will_assign_resource_callback` will be called. If any of these
    callbacks raises an exception, the resource will not be assigned.

    After the resource is assigned, all callbacks registered with
    :meth:`~Resource.register_did_assign_resource_callback` will be called.

    Args:
      resource: The resource to assign.
      location: The location of the resource, relative to this resource.
      reassign: If `False`, an error will be raised if the resource to be assigned is already
        assigned to this resource. Defaults to `True`.
    """

    # Check for unsupported resource assignment operations
    self._check_assignment(resource=resource, reassign=reassign)

    # Call "will assign" callbacks
    for callback in self._will_assign_resource_callbacks:
      callback(resource)

    # Modify the tree structure
    resource.parent = self
    resource.location = location
    self.children.append(resource)

    # Register callbacks on the new child resource so that they can be propagated up the tree.
    resource.register_will_assign_resource_callback(self._call_will_assign_resource_callbacks)
    resource.register_did_assign_resource_callback(self._call_did_assign_resource_callbacks)
    resource.register_will_unassign_resource_callback(self._call_will_unassign_resource_callbacks)
    resource.register_did_unassign_resource_callback(self._call_did_unassign_resource_callbacks)

    # Call "did assign" callbacks
    for callback in self._did_assign_resource_callbacks:
      callback(resource)

  # Helper methods to call all callbacks. These are used to propagate callbacks up the tree.
  def _call_will_assign_resource_callbacks(self, resource: Resource):
    for callback in self._will_assign_resource_callbacks:
      callback(resource)
  def _call_did_assign_resource_callbacks(self, resource: Resource):
    for callback in self._did_assign_resource_callbacks:
      callback(resource)
  def _call_will_unassign_resource_callbacks(self, resource: Resource):
    for callback in self._will_unassign_resource_callbacks:
      callback(resource)
  def _call_did_unassign_resource_callbacks(self, resource: Resource):
    for callback in self._did_unassign_resource_callbacks:
      callback(resource)

  def _check_assignment(self, resource: Resource, reassign: bool = True):
    """ Check if the resource assignment produces unsupported or dangerous conflicts. """
    msgs = []

    # Check for self assignment
    if resource is self:
      msgs.append(f"Cannot assign resource '{self.name}' to itself.")

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

    if len(msgs) > 0:
      msg = " ".join(msgs)
      raise ValueError(msg)

  def unassign_child_resource(self, resource: Resource):
    """ Unassign a child resource from this resource.

    Before the resource is unassigned, all callbacks registered with
    :meth:`~Resource.register_will_unassign_resource_callback` will be called.

    After the resource is unassigned, all callbacks registered with
    :meth:`~Resource.register_did_unassign_resource_callback` will be called.
    """

    if resource not in self.children:
      raise ValueError(f"Resource with name '{resource.name}' is not a child of this resource "
                       f"('{self.name}').")

    # Call "will unassign" callbacks
    for callback in self._will_unassign_resource_callbacks:
      callback(resource)

    # Update the tree structure
    resource.parent = None
    self.children.remove(resource)

    # Delete callbacks on the child resource so that they are not propagated up the tree.
    resource.deregister_will_assign_resource_callback(self._call_will_assign_resource_callbacks)
    resource.deregister_did_assign_resource_callback(self._call_did_assign_resource_callbacks)
    resource.deregister_will_unassign_resource_callback(self._call_will_unassign_resource_callbacks)
    resource.deregister_did_unassign_resource_callback(self._call_did_unassign_resource_callbacks)

    # Call "did unassign" callbacks
    for callback in self._did_unassign_resource_callbacks:
      callback(resource)

  def unassign(self):
    """ Unassign this resource from its parent. """
    if self.parent is not None:
      self.parent.unassign_child_resource(self)

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
      except ResourceNotFoundError:
        pass

    raise ResourceNotFoundError(f"Resource with name '{name}' does not exist.")

  def rotate(self, x: float = 0, y: float = 0, z: float = 0):
    """ Rotate counter-clockwise by the given number of degrees. """

    self.rotation.x = (self.rotation.x + x) % 360
    self.rotation.y = (self.rotation.y + y) % 360
    self.rotation.z = (self.rotation.z + z) % 360

  def copy(self) -> Self:
    resource_copy = self.__class__.deserialize(self.serialize(), allow_marshal=True)
    resource_copy.load_all_state(self.serialize_all_state())
    return resource_copy

  def rotated(self, x: float = 0, y: float = 0, z: float = 0) -> Self:
    """ Return a copy of this resource rotated by the given number of degrees. """

    new_resource = self.copy()
    new_resource.rotate(x=x, y=y, z=z)
    return new_resource

  def center(self, x: bool = True, y: bool = True, z: bool = False) -> Coordinate:
    """ Get the center of this resource.

    Args:
      x: If `True`, the x-coordinate will be the center, otherwise it will be 0.
      y: If `True`, the y-coordinate will be the center, otherwise it will be 0.
      z: If `True`, the z-coordinate will be the center, otherwise it will be 0.

    Examples:
      Get the center of a resource in the xy plane:

      >>> r = Resource("resource", size_x=12, size_y=12, size_z=12)
      >>> r.center()

      Coordinate(x=6.0, y=6.0, z=0.0)

      Get the center of a resource with only the x-coordinate:

      >>> r = Resource("resource", size_x=12, size_y=12, size_z=12)
      >>> r.center(x=True, y=False, z=False)

      Coordinate(x=6.0, y=0.0, z=0.0)

      Get the center of a resource in the x, y, and z directions:

      >>> r = Resource("resource", size_x=12, size_y=12, size_z=12)
      >>> r.center(x=True, y=True, z=True)

      Coordinate(x=6.0, y=6.0, z=6.0)
    """

    return Coordinate(
      self.get_size_x() / 2 if x else 0,
      self.get_size_y() / 2 if y else 0,
      self.get_size_z() / 2 if z else 0
    )

  def centers(self, xn: int = 1, yn: int = 1, zn: int = 1) -> List[Coordinate]:
    """ Get equally spaced points in the x, y, and z directions.

    Args:
      xn: the number of points in the x direction.
      yn: the number of points in the y direction.
      zn: the number of points in the z direction.

    Returns:
      A grid of points in the x, y, and z directions.

    Examples:
      Get the center of a resource:

      >>> r = Resource("resource", size_x=12, size_y=12, size_z=12)
      >>> r.centers()

      Coordinate(x=6.0, y=6.0, z=6.0)

      Get the center of a resource with 2 points in the x direction:

      >>> r = Resource("resource", size_x=12, size_y=12, size_z=12)
      >>> r.centers(xn=2)

      [Coordinate(x=4.0, y=6.0, z=6.0), Coordinate(x=9.0, y=6.0, z=6.0)]

      Get the center of a resource with 2 points in the x and y directions:

      >>> r = Resource("resource", size_x=12, size_y=12, size_z=12)
      >>> r.centers(xn=2, yn=2)
      [Coordinate(x=4.0, y=4.0, z=6.0), Coordinate(x=8.0, y=4.0, z=6.0),
       Coordinate(x=4.0, y=8.0, z=6.0), Coordinate(x=8.0, y=8.0, z=6.0)]
    """

    def _get_centers(n, dim_size):
      if n < 0:
        raise ValueError(f"Invalid number of points: {n}")
      if n == 0:
        return [0]
      return [(i+1) * dim_size/(n+1)  for i in range(n)]

    xs = _get_centers(xn, self.get_size_x())
    ys = _get_centers(yn, self.get_size_y())
    zs = _get_centers(zn, self.get_size_z())

    return [Coordinate(x, y, z) for x, y, z in itertools.product(xs, ys, zs)]

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
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> Self:
    """ Deserialize a resource from a dictionary.

    Args:
      allow_marshal: If `True`, the `marshal` module will be used to deserialize functions. This
        can be a security risk if the data is not trusted. Defaults to `False`.

    Examples:
      Loading a resource from a json file:

      >>> from pylabrobot.resources import Resource
      >>> with open("my_resource.json", "r") as f:
      >>>   content = json.load(f)
      >>> resource = Resource.deserialize(content)
    """

    data_copy = data.copy() # copy data because we will be modifying it

    subclass = find_subclass(data["type"], cls=Resource)
    if subclass is None:
      raise ValueError(f'Could not find subclass with name "{data["type"]}"')
    assert issubclass(subclass, cls) # mypy does not know the type after the None check...

    for key in ["type", "parent_name", "location"]: # delete meta keys
      del data_copy[key]
    children_data = data_copy.pop("children")
    rotation = data_copy.pop("rotation")
    resource = subclass(**deserialize(data_copy, allow_marshal=allow_marshal))
    resource.rotation = Rotation.deserialize(rotation) # not pretty, should be done in init.

    for child_data in children_data:
      child_cls = find_subclass(child_data["type"], cls=Resource)
      if child_cls is None:
        raise ValueError(f'Could not find subclass with name {child_data["type"]}')
      child = child_cls.deserialize(child_data, allow_marshal=allow_marshal)
      location_data = child_data.get("location", None)
      if location_data is not None:
        location = cast(Coordinate, deserialize(location_data))
      else:
        raise ValueError(f"Child resource '{child.name}' has no location.")
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

  def register_will_assign_resource_callback(self, callback: WillAssignResourceCallback):
    """ Add a callback that will be called before a resource is assigned to this resource. These
    callbacks can raise errors in case the proposed assignment is invalid. """
    self._will_assign_resource_callbacks.append(callback)

  def register_did_assign_resource_callback(self, callback: DidAssignResourceCallback):
    """ Add a callback that will be called after a resource is assigned to this resource. """
    self._did_assign_resource_callbacks.append(callback)

  def register_will_unassign_resource_callback(self, callback: WillUnassignResourceCallback):
    """ Add a callback that will be called before a resource is unassigned from this resource. """
    self._will_unassign_resource_callbacks.append(callback)

  def register_did_unassign_resource_callback(self, callback: DidUnassignResourceCallback):
    """ Add a callback that will be called after a resource is unassigned from this resource. """
    self._did_unassign_resource_callbacks.append(callback)

  def deregister_will_assign_resource_callback(self, callback: WillAssignResourceCallback):
    """ Remove a callback that will be called before a resource is assigned to this resource. """
    self._will_assign_resource_callbacks.remove(callback)

  def deregister_did_assign_resource_callback(self, callback: DidAssignResourceCallback):
    """ Remove a callback that will be called after a resource is assigned to this resource. """
    self._did_assign_resource_callbacks.remove(callback)

  def deregister_will_unassign_resource_callback(self, callback: WillUnassignResourceCallback):
    """ Remove a callback that will be called before a resource is unassigned from this resource."""
    self._will_unassign_resource_callbacks.remove(callback)

  def deregister_did_unassign_resource_callback(self, callback: DidUnassignResourceCallback):
    """ Remove a callback that will be called after a resource is unassigned from this resource. """
    self._did_unassign_resource_callbacks.remove(callback)

  # -- state --

  # Developer note: this method serializes the state of this resource only. If you want to serialize
  # a custom state for a resource, override this method in the subclass.
  def serialize_state(self) -> Dict[str, Any]:
    """ Serialize the state of this resource only.

    Use :meth:`pylabrobot.resources.resource.Resource.serialize_all_state` to serialize the state of
    this resource and all children.
    """
    return {}

  # Developer note: you probably don't need to override this method. Instead, override
  # `serialize_state`.
  def serialize_all_state(self) -> Dict[str, Dict[str, Any]]:
    """ Serialize the state of this resource and all children.

    Use :meth:`pylabrobot.resources.resource.Resource.serialize_state` to serialize the state of
    this resource only.

    Returns:
      A dictionary where the keys are the names of the resources and the values are the serialized
      states of the resources.
    """

    state = {self.name: self.serialize_state()}
    for child in self.children:
      state.update(child.serialize_all_state())
    return state

  # Developer note: this method deserializes the state of this resource only. If you want to
  # deserialize a custom state for a resource, override this method in the subclass.
  def load_state(self, state: Dict[str, Any]) -> None:
    """ Load state for this resource only. """
    # no state to load by default

  # Developer note: you probably don't need to override this method. Instead, override `load_state`.
  def load_all_state(self, state: Dict[str, Dict[str, Any]]) -> None:
    """ Load state for this resource and all children. """
    for child in self.children:
      child.load_state(state[child.name])
      child.load_all_state(state)

  def save_state_to_file(self, fn: str, indent: Optional[int] = None):
    """ Save the state of this resource and all children to a JSON file.

    Args:
      fn: File name. Caution: file will be overwritten.
      indent: Same as `json.dump`'s `indent` argument (for json pretty printing).

    Examples:
      Saving to a json file:

      >>> deck.save_state_to_file("my_state.json")
    """

    serialized = self.serialize_all_state()
    with open(fn, "w", encoding="utf-8") as f:
      json.dump(serialized, f, indent=indent)

  def load_state_from_file(self, fn: str) -> None:
    """ Load the state of this resource and all children from a JSON file.

    Args:
      fn: The file name to load the state from.

    Examples:
      Loading from a json file:

      >>> deck.load_state_from_file("my_state.json")
    """

    with open(fn, "r", encoding="utf-8") as f:
      content = json.load(f)
    self.load_all_state(content)

  def register_state_update_callback(self, callback: ResourceDidUpdateState):
    """ Register a callback that will be called when the state of the resource changes. """
    self._resource_state_updated_callbacks.append(callback)

  def deregister_state_update_callback(self, callback: ResourceDidUpdateState):
    """ Remove a callback that will be called when the state of the resource changes. """
    self._resource_state_updated_callbacks.remove(callback)

  def _state_updated(self):
    for callback in self._resource_state_updated_callbacks:
      callback(self.serialize_state())

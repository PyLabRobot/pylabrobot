from __future__ import annotations

from typing import Dict, List, cast

from pylabrobot.resources.errors import ResourceNotFoundError

from .coordinate import Coordinate
from .resource import Resource
from .trash import Trash


class Deck(Resource):
  """ Base class for liquid handler decks.

  This class maintains a dictionary of all resources on the deck. The dictionary is keyed by the
  resource name and is updated when resources are assigned and unassigned from the deck. The point
  of this dictionary is to allow O(1) naming collision checks as well as the quick lookup of
  resources by name.
  """

  def __init__(
    self,
    name: str = "deck",
    size_x: float = 1360,
    size_y: float = 653.5,
    size_z: float = 900,
    origin: Coordinate = Coordinate(0, 0, 0),
    category: str = "deck",
  ):
    """ Initialize a new deck. """

    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category)
    self.location = origin
    self.resources: Dict[str, Resource] = {}

    self.register_will_assign_resource_callback(self._check_name_exists)
    self.register_did_assign_resource_callback(self._register_resource)
    self.register_did_unassign_resource_callback(self._deregister_resource)

  def serialize(self) -> dict:
    """ Serialize this deck. """
    super_serialized = super().serialize()
    del super_serialized["model"] # deck's don't typically have a model
    return super_serialized

  def _check_name_exists(self, resource: Resource):
    """ Callback called before a resource is assigned to the deck. (will_assign_resource_callback)
    Raises a ValueError if the resource name already exists. This method is recursive, and
    will also check children of the resource that is to be assigned.
    """

    if self.has_resource(resource.name):
      raise ValueError(f"Resource '{resource.name}' already assigned to deck")
    for child in resource.children:
      self._check_name_exists(child)

  def _register_resource(self, resource: Resource):
    """ Recursively assign the given resource and all child resources to the `self.resources`
    dictionary. This method is called after a resource is assigned to the deck
    (did_assign_resource_callback).

    Precondition: All child resources must be assignable, see `self._check_name_exists`.
    """

    for child in resource.children:
      self._register_resource(child)
    self.resources[resource.name] = resource

  def _deregister_resource(self, resource: Resource):
    """ Recursively deregisters the given resource and all child resources from the `self.resources`
    dictionary. This method is called after a resource is unassigned from the deck
    (did_unassign_resource_callback).
    """

    if self.has_resource(resource.name):
      del self.resources[resource.name]
    for child in resource.children:
      self._deregister_resource(child)

  def get_resource(self, name: str) -> Resource:
    """ Returns the resource with the given name.

    Raises:
      ValueError: If the resource is not found.
    """
    if not self.has_resource(name):
      raise ValueError(f"Resource '{name}' not found")
    return self.resources[name]

  def has_resource(self, name: str) -> bool:
    """ Returns True if the deck has a resource with the given name. """
    return name in self.resources

  def get_all_resources(self) -> List[Resource]:
    """ Returns a list of all resources in the deck. """
    return list(self.resources.values())

  def clear(self):
    """ Removes all resources from the deck.

    Examples:

      Clearing all resources on a liquid handler deck:

      >>> lh.deck.clear()
    """

    all_resources = list(self.resources.values()) # can't change size during iteration
    for resource in all_resources:
      resource.unassign()

  def get_trash_area(self) -> Trash:
    """ Returns the trash area resource. """
    if not self.has_resource("trash"):
      raise ResourceNotFoundError("Trash area not found")
    return cast(Trash, self.get_resource("trash"))

  def summary(self) -> str:
    """ Returns a summary of the deck layout. """
    summary_ = f"Deck: {self.get_size_x()} x {self.get_size_y()} mm\n\n"
    for resource in self.children:
      summary_ += f"{resource.name}: {resource}\n"
    return summary_

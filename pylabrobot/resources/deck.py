from __future__ import annotations

from typing import Dict, List, cast

from pylabrobot.resources.errors import ResourceNotFoundError

from .coordinate import Coordinate
from .resource import Resource
from .trash import Trash


class Deck(Resource):
  """Base class for liquid handler decks.

  This class maintains a dictionary of all resources on the deck. The dictionary is keyed by the
  resource name and is updated when resources are assigned and unassigned from the deck. The point
  of this dictionary is to allow O(1) naming collision checks as well as the quick lookup of
  resources by name.
  """

  def __init__(
    self,
    size_x: float,
    size_y: float,
    size_z: float,
    name: str = "deck",
    origin: Coordinate = Coordinate(0, 0, 0),
    category: str = "deck",
  ):
    """Initialize a new deck."""

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
    )
    self.location = origin
    self._resources: Dict[str, Resource] = {}

    self.register_did_assign_resource_callback(self._register_resource)
    self.register_did_unassign_resource_callback(self._deregister_resource)

  def serialize(self) -> dict:
    """Serialize this deck."""
    super_serialized = super().serialize()
    del super_serialized["model"]  # deck's don't typically have a model
    return super_serialized

  def _check_naming_conflicts(self, resource: Resource):
    """overwrite for speed"""
    if self.has_resource(resource.name):
      raise ValueError(f"Resource '{resource.name}' already assigned to deck")

  def _register_resource(self, resource: Resource):
    """Recursively assign the given resource and all child resources to the `self._resources`
    dictionary. This method is called after a resource is assigned to the deck
    (did_assign_resource_callback).

    Precondition: All child resources must be assignable, see `self._check_name_exists`.
    """

    for child in resource.children:
      self._register_resource(child)
    self._resources[resource.name] = resource

  def _deregister_resource(self, resource: Resource):
    """Recursively deregisters the given resource and all child resources from the `self._resources`
    dictionary. This method is called after a resource is unassigned from the deck
    (did_unassign_resource_callback).
    """

    if self.has_resource(resource.name):
      del self._resources[resource.name]
    for child in resource.children:
      self._deregister_resource(child)

  def get_resource(self, name: str) -> Resource:
    """Returns the resource with the given name.

    Raises:
      ResourceNotFoundError: If the resource is not found.
    """
    if name == self.name:
      return self
    if not self.has_resource(name):
      raise ResourceNotFoundError(f"Resource '{name}' not found")
    return self._resources[name]

  def has_resource(self, name: str) -> bool:
    """Returns True if the deck has a resource with the given name."""
    return name in self._resources

  def get_all_resources(self) -> List[Resource]:
    """Returns a list of all resources in the deck."""
    return list(self._resources.values())

  def clear(self, include_trash: bool = False):
    """Removes all resources from the deck.

    Examples:
      Clearing all resources on a liquid handler deck:

      >>> lh.deck.clear()

      Clearing all resources on a liquid handler deck, including the trash area:

      >>> lh.deck.clear(include_trash=True)
    """

    children_names = [child.name for child in self.children]
    for resource_name in children_names:
      resource = self.get_resource(resource_name)
      if isinstance(resource, Trash) and not include_trash:
        continue
      resource.unassign()

  def get_trash_area(self) -> Trash:
    """Returns the trash area resource."""
    if not self.has_resource("trash"):
      raise ResourceNotFoundError("Trash area not found")
    return cast(Trash, self.get_resource("trash"))

  def summary(self) -> str:
    """Returns a summary of the deck layout."""
    summary_ = f"Deck: {self.get_absolute_size_x()} x {self.get_absolute_size_y()} mm\n\n"
    for resource in self.children:
      summary_ += f"{resource.name}: {resource}\n"
    return summary_

  def get_trash_area96(self) -> Trash:
    deck_class = self.__class__.__name__
    raise NotImplementedError(f"This method is not implemented by deck '{deck_class}'")

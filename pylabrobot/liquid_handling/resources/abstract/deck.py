from __future__ import annotations

import inspect
import json
from typing import Optional, Callable, List, Dict, cast

import pylabrobot.liquid_handling.resources as resources_module

from .coordinate import Coordinate
from .resource import Resource


class Deck(Resource):
  """ Base class for liquid handler decks. """

  def __init__(
    self,
    size_x: float = 1360,
    size_y: float = 653.5,
    size_z: float = 900,
    resource_assigned_callback: Optional[Callable] = None,
    resource_unassigned_callback: Optional[Callable] = None,
    origin: Coordinate = Coordinate(0, 0, 0),
  ):
    """ Initialize a new deck.

    Args:
      resource_assigned_callback: A callback function that is called when a resource is assigned to
        the deck. This includes resources assigned to child resources. The callback function is
        called with the resource as an argument. This method may raise an exception to prevent the
        resource from being assigned.
      resource_unassigned_callback: A callback function that is called when a resource is unassigned
        from the deck. This includes resources unassigned from child resources. The callback
        function is called with the resource as an argument.
    """

    super().__init__(name="deck", size_x=size_x, size_y=size_y, size_z=size_z, category="deck")
    self.location = origin
    self.resources: Dict[str, Resource] = {}
    self.resource_assigned_callback_callback = resource_assigned_callback
    self.resource_unassigned_callback_callback = resource_unassigned_callback

  @classmethod
  def deserialize(cls, data: dict):
    """ Deserialize the deck from a dictionary. """
    return cls(
      size_x=data["size_x"],
      size_y=data["size_y"],
      size_z=data["size_z"],
      origin=Coordinate.deserialize(data["location"]),
    )

  def _check_name_exists(self, resource: Resource):
    """ Raises a ValueError if the resource name already exists. This method is recursive, and
    will also check child resources. """

    if self.has_resource(resource.name):
      raise ValueError(f"Resource '{resource.name}' already assigned to deck")
    for child in resource.children:
      self._check_name_exists(child)

  def _assign_resource(self, resource: Resource):
    """ Recursively assign the given resource and all child resources to the `self.resources`
    dictionary.

    Precondition: All child resources must be assignable, see `self._check_name_exists`.
    """

    for child in resource.children:
      self._assign_resource(child)
    self.resources[resource.name] = resource

  def resource_assigned_callback(self, resource: Resource):
    """
    - Keeps track of the resources in the deck.
    - Raises a `ValueError` if a resource with the same name is already assigned.
    """

    self._check_name_exists(resource)
    super().resource_assigned_callback(resource)

    self._assign_resource(resource)

    if self.resource_assigned_callback_callback is not None:
      self.resource_assigned_callback_callback(resource)

  def _unassign_resource(self, resource: Resource):
    """ Recursively unassigns the given resource and all child resources from the `self.resources`
    dictionary."""

    if self.has_resource(resource.name):
      del self.resources[resource.name]
    for child in resource.children:
      self._unassign_resource(child)

  def resource_unassigned_callback(self, resource: Resource):
    self._unassign_resource(resource)
    super().resource_unassigned_callback(resource)
    if self.resource_unassigned_callback_callback is not None:
      self.resource_unassigned_callback_callback(resource)

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

  def save(self, fn: str, indent: Optional[int] = None):
    """ Save the deck layout to a JSON file.

    Args:
      fn: File name. Caution: file will be overwritten.
      indent: Same as `json.dump`'s `indent` argument (for json pretty printing).

    Examples:
      Saving to a json file:

      >>> from pylabrobot.liquid_handling.resources.hamilton import STARLetDeck
      >>> deck = STARLetDeck()
      >>> deck.save("my_layout.json")
    """

    serialized = self.serialize()
    serialized = dict(deck=serialized)

    with open(fn, "w", encoding="utf-8") as f:
      json.dump(serialized, f, indent=indent)

  @classmethod
  def load_from_json(cls, content: dict) -> Deck:
    """ Loads resources from a JSON file.

    Args:
      content: The content of the JSON file.

    Examples:
      Loading from a .json file:

      >>> from pylabrobot.liquid_handling import Deck
      >>> with open("my_layout.json", "r") as f:
      >>>   content = json.load(f)
      >>> deck = Deck.load_from_json(content)
    """

    # Get class names of all defined resources.
    resource_classes = [c[0] for c in inspect.getmembers(resources_module)]

    def deserialize_resource(dict_resource) -> Resource:
      """ Deserialize a single resource. """

      # Get class name.
      class_name = dict_resource["type"]
      if class_name in resource_classes:
        klass = getattr(resources_module, class_name)
        resource = klass.deserialize(dict_resource)
        for child_dict in dict_resource["children"]:
          child_resource = deserialize_resource(child_dict)
          child_location = child_dict.pop("location")
          child_location = Coordinate.deserialize(child_location)
          resource.assign_child_resource(child_resource, location=child_location)
        return cast(Resource, resource)
      else:
        raise ValueError(f"Resource with classname {class_name} not found.")

    deck_dict = content["deck"]
    deck = deserialize_resource(deck_dict)
    return cast(Deck, deck)

  @classmethod
  def load_from_json_file(cls, json_file: str) -> Deck:
    """ Loads resources from a JSON file.

    Args:
      json_file: The path to the JSON file.

    Examples:
      Loading from a .json file:

      >>> from pylabrobot.liquid_handling import Deck
      >>> deck = Deck.load_from_json("deck.json")
    """

    with open(cast(str, json_file), "r", encoding="utf-8") as f:
      content = json.load(f)

    return cls.load_from_json(content)

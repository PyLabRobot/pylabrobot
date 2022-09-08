from typing import Optional, Callable, List

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

    TODO: sizes from HAMILTON\\Config\\ML_Starlet.dck (mm probably), want to create
      STARDeck(HamiltonDeck)

    Args:
      resource_assigned_callback: A callback function that is called when a resource is assigned to
        the deck. This includes resources assigned to child resources. The callback function is
        called with the resource as an argument. This method may raise an exception to prevent the
        resource from being assigned.
      resource_unassigned_callback: A callback function that is called when a resource is unassigned
        from the deck. This includes resources unassigned from child resources. The callback
        function is called with the resource as an argument.
    """

    super().__init__(name="deck", size_x=size_x, size_y=size_y, size_z=size_z,
      location=origin, category="deck")
    self.resources = {}
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

  def get_resource(self, name: str) -> Optional[Resource]:
    """ Returns the resource with the given name. """
    # override = faster, because we have kept the resources in a dictionary.
    return self.resources.get(name)

  def has_resource(self, name: str) -> bool:
    """ Returns True if the deck has a resource with the given name. """
    return name in self.resources

  def get_resources(self) -> List[Resource]:
    """ Returns a list of all resources in the deck. """
    return list(self.resources.values())

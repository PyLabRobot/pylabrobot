from typing import Optional, Callable, List

from .coordinate import Coordinate
from .resource import Resource


class Deck(Resource):
  """ Base class for liquid handler decks. """

  def __init__(
    self,
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

    # sizes from HAMILTON\Config\ML_Starlet.dck (mm probably)
    super().__init__(name="deck", size_x=1360, size_y=653.5, size_z=900, location=origin, category="deck")
    self.resources = {}
    self.resource_assigned_callback_callback = resource_assigned_callback
    self.resource_unassigned_callback_callback = resource_unassigned_callback

  def resource_assigned_callback(self, resource):
    """
    - Keeps track of the resources in the deck.
    - Raises a `ValueError` if a resource with the same name is already assigned.
    """

    if self.has_resource(resource.name):
      raise ValueError(f"Resource '{resource.name}' already assigned to deck")
    super().resource_assigned_callback(resource)

    self.resources[resource.name] = resource

    if self.resource_assigned_callback_callback is not None:
      self.resource_assigned_callback_callback(resource)

  def resource_unassigned_callback(self, resource):
    if resource.name in self.resources:
      del self.resources[resource.name]
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

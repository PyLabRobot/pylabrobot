from typing import Callable, Optional

from pylabrobot.liquid_handling.resources import Coordinate, Deck, Resource


_RAILS_WIDTH = 22.5 # space between rails (mm)


class HamiltonDeck(Deck):
  """ Hamilton decks. Currently only STARLet and STAR are supported. """

  def __init__(
    self,
    num_rails: int,
    size_x: float,
    size_y: float,
    size_z: float,
    resource_assigned_callback: Optional[Callable] = None,
    resource_unassigned_callback: Optional[Callable] = None,
    origin: Coordinate = Coordinate(0, 63, 100),
  ):
    super().__init__(size_x, size_y, size_z,
      resource_assigned_callback, resource_unassigned_callback, origin)
    self.num_rails = num_rails

  def serialize(self) -> dict:
    """ Serialize this deck. """
    return {
      **super().serialize(),
      "num_rails": self.num_rails,
    }

  @classmethod
  def deserialize(cls, data: dict):
    """ Deserialize this deck. """
    return cls(
      num_rails=data["num_rails"],
      size_x=data["size_x"],
      size_y=data["size_y"],
      size_z=data["size_z"],
      origin=Coordinate.deserialize(data["location"]),
    )

  def assign_child_resource(
    self,
    resource: Resource,
    rails: Optional[int] = None,
    location: Optional[Coordinate] = None,
    replace=False
  ):
    """ Assign a new deck resource.

    The identifier will be the Resource.name, which must be unique amongst previously assigned
    resources.

    Note that some resources, such as tips on a tip carrier or plates on a plate carrier must
    be assigned directly to the tip or plate carrier respectively. See TipCarrier and PlateCarrier
    for details.

    Based on the rails argument, the absolute (x, y, z) coordinates will be computed.

    Args:
      resource: A Resource to assign to this liquid handler.
      rails: The left most real (inclusive) of the deck resource (between and 1-30 for STARLet,
             max 55 for STAR.) Either rails or location must be None, but not both.
      location: The location of the resource relative to the liquid handler. Either rails or
                location must be None, but not both.
      replace: Replace the resource with the same name that was previously assigned, if it exists.
               If a resource is assigned with the same name and replace is False, a ValueError
               will be raised.

    Raises:
      ValueError: If a resource is assigned with the same name and replace is `False`.
    """

    # TODO: many things here should be moved to Resource and Deck, instead of just STARLetDeck

    if rails is not None and not 1 <= rails <= self.num_rails:
      raise ValueError(f"Rails must be between 1 and {self.num_rails}.")

    # Check if resource exists.
    if self.has_resource(resource.name):
      if replace:
        # unassign first, so we don't have problems with location checking later.
        self.get_resource(resource.name).unassign()
      else:
        raise ValueError(f"Resource with name '{resource.name}' already defined.")

    if rails is not None and location is not None:
      raise ValueError("At least one of rails and location must be None.")

    if rails is not None:
      resource.location = Coordinate(x=self._x_coordinate_for_rails(rails), y=0, z=0)
    elif location is not None:
      resource.location = location


    if resource.location.x + resource.get_size_x() > \
        self._x_coordinate_for_rails(self.num_rails) and \
      rails is not None:
      raise ValueError(f"Resource with width {resource.get_size_x()} does not "
                       f"fit at rails {rails}.")

    resource.parent = self

    # Check collision
    # # Check if there is space for this new resource.
    for og_resource in self.children:
      og_x = og_resource.get_absolute_location().x
      og_y = og_resource.get_absolute_location().y

      # A resource is not allowed to overlap with another resource. Resources overlap when a corner
      # of one resource is inside the boundaries other resource.
      if (og_x <= resource.get_absolute_location().x < og_x + og_resource.get_size_x() or \
         og_x <= resource.get_absolute_location().x + resource.get_size_x() <
           og_x + og_resource.get_size_x()) and \
          (og_y <= resource.get_absolute_location().y < og_y + og_resource.get_size_y() or \
            og_y <= resource.get_absolute_location().y + resource.get_size_y() <
               og_y + og_resource.get_size_y()):
        tried_location = resource.location
        resource.location = None # Revert location.
        resource.parent = None # Revert parent.
        raise ValueError(f"Location {tried_location} is already occupied by resource "
                          f"'{og_resource.name}'.")

    return super().assign_child_resource(resource)

  def _x_coordinate_for_rails(self, rails: int):
    """ Convert a rail identifier to an x coordinate. """
    return 100.0 + (rails - 1) * _RAILS_WIDTH


def STARLetDeck(
  resource_assigned_callback: Optional[Callable] = None,
  resource_unassigned_callback: Optional[Callable] = None,
  origin: Coordinate = Coordinate(0, 63, 100),
) -> HamiltonDeck:
  """ A STARLet deck.

  Sizes from `HAMILTON\\Config\\ML_Starlet.dck`
  """

  return HamiltonDeck(
      num_rails=30,
      size_x=1360,
      size_y=653.5,
      size_z=900,
      resource_assigned_callback=resource_assigned_callback,
      resource_unassigned_callback=resource_unassigned_callback,
      origin=origin)


def STARDeck(
  resource_assigned_callback: Optional[Callable] = None,
  resource_unassigned_callback: Optional[Callable] = None,
  origin: Coordinate = Coordinate(0, 63, 100),
) -> HamiltonDeck:
  """ The Hamilton STAR deck.

  Sizes from `HAMILTON\\Config\\ML_STAR2.dck`
  """

  return HamiltonDeck(
      num_rails=55,
      size_x=1900,
      size_y=653.5,
      size_z=900,
      resource_assigned_callback=resource_assigned_callback,
      resource_unassigned_callback=resource_unassigned_callback,
      origin=origin)

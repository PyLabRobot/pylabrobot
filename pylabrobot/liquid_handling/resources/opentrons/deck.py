from typing import Optional, Callable

from pylabrobot.liquid_handling.resources.abstract import Coordinate, Deck, Resource


class OTDeck(Deck):
  """ The OpenTron deck. """

  def __init__(self, size_x: float = 624.3, size_y: float = 565.2, size_z: float = 900,
    resource_assigned_callback: Optional[Callable] = None,
    resource_unassigned_callback: Optional[Callable] = None,
    origin: Coordinate = Coordinate(0, 0, 0)):
    # size_z is probably wrong

    super().__init__(size_x, size_y, size_z,
     resource_assigned_callback=resource_assigned_callback,
     resource_unassigned_callback=resource_unassigned_callback,
     origin=origin)

    self.slots = [None] * 12

    self.slot_locations = [
      Coordinate(x=0.0,   y=0.0,   z=0.0),
      Coordinate(x=132.5, y=0.0,   z=0.0),
      Coordinate(x=265.0, y=0.0,   z=0.0),
      Coordinate(x=0.0,   y=90.5,  z=0.0),
      Coordinate(x=132.5, y=90.5,  z=0.0),
      Coordinate(x=265.0, y=90.5,  z=0.0),
      Coordinate(x=0.0,   y=181.0, z=0.0),
      Coordinate(x=132.5, y=181.0, z=0.0),
      Coordinate(x=265.0, y=181.0, z=0.0),
      Coordinate(x=0.0,   y=271.5, z=0.0),
      Coordinate(x=132.5, y=271.5, z=0.0),
      Coordinate(x=265.0, y=271.5, z=0.0)
    ]

  def assign_child_resource(self, resource: Resource, slot: int):
    if slot not in range(1, 13):
      raise ValueError("slot must be between 1 and 12")

    if self.slots[slot-1] is not None:
      raise ValueError(f"Spot {slot} is already occupied")

    resource.location = self.slot_locations[slot-1]
    self.slots[slot-1] = resource
    super().assign_child_resource(resource)

  def unassign_child_resource(self, resource: Resource):
    if resource not in self.slots:
      raise ValueError(f"Resource {resource.name} is not assigned to this deck")

    slot = self.slots.index(resource)
    self.slots[slot] = None
    super().unassign_child_resource(resource)

  def get_slot(self, resource: Resource) -> Optional[int]:
    """ Get the slot number of a resource. """
    if resource not in self.slots:
      return None
    return self.slots.index(resource) + 1

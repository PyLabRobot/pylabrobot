from typing import Optional, Callable, List

from pylabrobot.liquid_handling.resources.abstract import Coordinate, Deck, Resource, Trash


class OTDeck(Deck):
  """ The OpenTron deck. """

  def __init__(self, size_x: float = 624.3, size_y: float = 565.2, size_z: float = 900,
    resource_assigned_callback: Optional[Callable] = None,
    resource_unassigned_callback: Optional[Callable] = None,
    origin: Coordinate = Coordinate(0, 0, 0),
    no_trash: bool = False):
    # size_z is probably wrong

    super().__init__(size_x, size_y, size_z,
     resource_assigned_callback=resource_assigned_callback,
     resource_unassigned_callback=resource_unassigned_callback,
     origin=origin)

    self.slots: List[Optional[Resource]] = [None] * 12

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

    if not no_trash:
      self._assign_trash()

  def _assign_trash(self):
    """ Assign the trash area to the deck.

    Because all opentrons operations require that the resource passed references a parent, we need
    to create a dummy resource to represent the container of the actual trash area.
    """

    trash_container = Resource(
      name="trash_container",
      size_x=172.86,
      size_y=165.86,
      size_z=82,
    )

    actual_trash = Trash(
      name="trash",
      size_x=172.86,
      size_y=165.86,
      size_z=82,
    )

    trash_container.assign_child_resource(actual_trash, location=Coordinate(x=82.84, y=53.56, z=5))
    self.assign_child_at_slot(trash_container, 12)

  def assign_child_resource(self, resource: Resource, location: Coordinate):
    """ Assign a resource to a slot.

    ..warning:: This method exists only for deserialization. You should use
    :meth:`assign_child_at_slot` instead.
    """
    slot = self.slot_locations.index(location)
    self.assign_child_at_slot(resource, slot)

  def assign_child_at_slot(self, resource: Resource, slot: int):
    # pylint: disable=arguments-renamed
    if slot not in range(1, 13):
      raise ValueError("slot must be between 1 and 12")

    if self.slots[slot-1] is not None:
      raise ValueError(f"Spot {slot} is already occupied")

    self.slots[slot-1] = resource
    super().assign_child_resource(resource, location=self.slot_locations[slot-1])

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

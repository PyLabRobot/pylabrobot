import textwrap
from typing import Optional, List

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.trash import Trash


class OTDeck(Deck):
  """ The OpenTron deck. """

  def __init__(self, size_x: float = 624.3, size_y: float = 565.2, size_z: float = 900,
    origin: Coordinate = Coordinate(0, 0, 0),
    no_trash: bool = False, name: str = "deck"):
    # size_z is probably wrong

    super().__init__(size_x=size_x, size_y=size_y, size_z=size_z, origin=origin)

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

    # Trash location used to be Coordinate(x=86.43, y=82.93, z=0),
    # this is approximately the center of the trash area.
    # LiquidHandler will now automatically find the center of the trash before discarding tips,
    # so this location is no longer needed and we just use Coordinate.zero().
    # The actual location of the trash is determined by the slot number (12).
    trash_container.assign_child_resource(actual_trash, location=Coordinate.zero())
    self.assign_child_at_slot(trash_container, 12)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True
  ):
    """ Assign a resource to a slot.

    ..warning:: This method exists only for deserialization. You should use
    :meth:`assign_child_at_slot` instead.
    """

    if location not in self.slot_locations:
      super().assign_child_resource(resource, location=location)
    else:
      slot = self.slot_locations.index(location) + 1
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

  def summary(self) -> str:
    """ Get a summary of the deck.

    >>> print(deck.summary())

    Deck: 624.3mm x 565.2mm

    +-----------------+-----------------+-----------------+
    |                 |                 |                 |
    | 10: Empty       | 11: Empty       | 12: Trash       |
    |                 |                 |                 |
    +-----------------+-----------------+-----------------+
    |                 |                 |                 |
    |  7: tip_rack_1  |  8: tip_rack_2  |  9: tip_rack_3  |
    |                 |                 |                 |
    +-----------------+-----------------+-----------------+
    |                 |                 |                 |
    |  4: my_plate    |  5: my_other... |  6: Empty       |
    |                 |                 |                 |
    +-----------------+-----------------+-----------------+
    |                 |                 |                 |
    |  1: Empty       |  2: Empty       |  3: Empty       |
    |                 |                 |                 |
    +-----------------+-----------------+-----------------+
    """

    def _get_slot_name(slot: int) -> str:
      """ Get slot name, or 'Empty' if slot is empty. If the name is too long, truncate it. """
      length = 11
      resource = self.slots[slot]
      if resource is None:
        return "Empty".ljust(length)
      name = resource.name
      if len(name) > 10:
        name = name[:8] + "..."
      return name.ljust(length)

    summary_ = f"""
      Deck: {self.get_size_x()}mm x {self.get_size_y()}mm

      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      | 10: {_get_slot_name(9)} | 11: {_get_slot_name(10)} | 12: {_get_slot_name(11)} |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      |  7: {_get_slot_name(6)} |  8: {_get_slot_name(7)} |  9: {_get_slot_name(8)} |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      |  4: {_get_slot_name(3)} |  5: {_get_slot_name(4)} |  6: {_get_slot_name(5)} |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      |  1: {_get_slot_name(0)} |  2: {_get_slot_name(1)} |  3: {_get_slot_name(2)} |
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
    """

    return textwrap.dedent(summary_)

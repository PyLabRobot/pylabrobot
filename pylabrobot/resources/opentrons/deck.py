import textwrap
from typing import List, Optional, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.trash import Trash

_SLOT_SIZE_X = 128.0
_SLOT_SIZE_Y = 86.0

# The fixed trash (opentrons_1_trash_1100ml_fixed) is larger than a standard slot and overhangs it,
# so slot 12's holder takes the trash footprint instead of the standard slot size.
_TRASH_SIZE_X = 172.86
_TRASH_SIZE_Y = 165.86
_TRASH_SIZE_Z = 82.0

# Base OT-2 slot grid in the robot frame, where slot 1's corner is the origin.
_BASE_SLOT_LOCATIONS = [
  Coordinate(x=0.0, y=0.0, z=0.0),
  Coordinate(x=132.5, y=0.0, z=0.0),
  Coordinate(x=265.0, y=0.0, z=0.0),
  Coordinate(x=0.0, y=90.5, z=0.0),
  Coordinate(x=132.5, y=90.5, z=0.0),
  Coordinate(x=265.0, y=90.5, z=0.0),
  Coordinate(x=0.0, y=181.0, z=0.0),
  Coordinate(x=132.5, y=181.0, z=0.0),
  Coordinate(x=265.0, y=181.0, z=0.0),
  Coordinate(x=0.0, y=271.5, z=0.0),
  Coordinate(x=132.5, y=271.5, z=0.0),
  Coordinate(x=265.0, y=271.5, z=0.0),
]
# The deck plate corner sits at (-115.65, -68.03) in the robot frame, which is this resource's
# local origin, so each slot is re-based onto the plate corner by adding the offset.
_SLOT_CORNER_OFFSET = Coordinate(x=115.65, y=68.03, z=0.0)


class OTDeck(Deck):
  """The Opentrons OT-2 deck.

  The 12 slots are modeled as :class:`ResourceHolder` children, one per slot, so the deck geometry
  has a single source of truth and renders directly from the serialized resource tree. Labware is
  placed into a slot's holder with :meth:`assign_child_at_slot`.
  """

  def __init__(
    self,
    size_x: float = 624.3,
    size_y: float = 565.2,
    size_z: float = 0,
    origin: Coordinate = Coordinate(0, 0, 0),
    with_trash: bool = True,
    name: str = "ot2_deck",
    category: str = "deck",
  ):
    super().__init__(
      size_x=size_x, size_y=size_y, size_z=size_z, name=name, origin=origin, category=category
    )

    self._slot_holders: List[ResourceHolder] = []
    for i, base in enumerate(_BASE_SLOT_LOCATIONS):
      is_trash_slot = i == 11 and with_trash
      holder = ResourceHolder(
        name=f"{self.name}_slot_{i + 1}",
        size_x=_TRASH_SIZE_X if is_trash_slot else _SLOT_SIZE_X,
        size_y=_TRASH_SIZE_Y if is_trash_slot else _SLOT_SIZE_Y,
        size_z=_TRASH_SIZE_Z if is_trash_slot else 0,
      )
      self._slot_holders.append(holder)
      super().assign_child_resource(holder, location=base + _SLOT_CORNER_OFFSET)

    if with_trash:
      self._assign_trash()

  @property
  def slots(self) -> List[Optional[Resource]]:
    """The labware in each slot, or ``None`` for empty slots (slot 1 first)."""
    return [holder.resource for holder in self._slot_holders]

  @property
  def slot_locations(self) -> List[Coordinate]:
    """The location of each slot, re-based onto the deck plate corner (slot 1 first)."""
    return [cast(Coordinate, holder.location) for holder in self._slot_holders]

  def _assign_trash(self):
    """Assign the trash area to slot 12.

    Because all opentrons operations require that the resource passed references a parent, we need
    to create a dummy resource to represent the container of the actual trash area.
    """

    trash_container = Trash(
      name="trash_container",
      size_x=_TRASH_SIZE_X,
      size_y=_TRASH_SIZE_Y,
      size_z=_TRASH_SIZE_Z,
    )

    actual_trash = Trash(
      name="trash",
      size_x=_TRASH_SIZE_X,
      size_y=_TRASH_SIZE_Y,
      size_z=_TRASH_SIZE_Z,
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
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    """Assign a slot holder to the deck.

    The deck's direct children are the 12 slot holders created in ``__init__``. Deserialization
    re-assigns those holders by name, replacing the placeholder with the loaded one (which carries
    its labware). Labware itself is placed with :meth:`assign_child_at_slot`, not here.
    """

    existing = next((child for child in self.children if child.name == resource.name), None)
    if existing is not None:
      if not reassign:
        raise ValueError(f"Resource '{resource.name}' already assigned to deck")
      super().unassign_child_resource(existing)
      if existing in self._slot_holders:
        self._slot_holders[self._slot_holders.index(existing)] = cast(ResourceHolder, resource)
    elif not isinstance(resource, ResourceHolder):
      raise ValueError(
        f"Cannot assign '{resource.name}' directly to the deck. Use assign_child_at_slot to place "
        "labware into a slot. A deck serialized before slots became resource holders stores labware "
        "as direct children and will not load."
      )

    super().assign_child_resource(resource, location=location, reassign=reassign)

  def assign_child_at_slot(self, resource: Resource, slot: int):
    if slot not in range(1, 13):
      raise ValueError("slot must be between 1 and 12")

    holder = self._slot_holders[slot - 1]
    if holder.resource is not None:
      raise ValueError(f"Spot {slot} is already occupied")

    holder.assign_child_resource(resource)

  def unassign_child_resource(self, resource: Resource):
    for holder in self._slot_holders:
      if holder.resource is resource:
        holder.unassign_child_resource(resource)
        return
    super().unassign_child_resource(resource)

  def get_slot(self, resource: Resource) -> Optional[int]:
    """Get the slot number of a resource."""
    for i, holder in enumerate(self._slot_holders):
      if holder.resource is resource:
        return i + 1
    return None

  def summary(self) -> str:
    """Get a summary of the deck.

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
      """Get slot name, or 'Empty' if slot is empty. If the name is too long, truncate it."""
      length = 11
      resource = self.slots[slot]
      if resource is None:
        return "Empty".ljust(length)
      name = resource.name
      if len(name) > 10:
        name = name[:8] + "..."
      return name.ljust(length)

    summary_ = f"""
      Deck: {self.get_absolute_size_x()}mm x {self.get_absolute_size_y()}mm

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

"""FlexDeck — Opentrons Flex deck with A1–D3 grid layout plus staging area.

The Flex has 12 standard slots in a 4-row x 3-column grid (rows A–D
from rear to front, columns 1–3 from left to right), plus 4 staging
area slots in column 4.

Coordinates sourced from Opentrons ot3_standard deck definition v5.
Slot bounding box: 128.0 x 86.0 mm.

Provides collision detection for single-nozzle tip pickup: when
the 8-channel pipette uses only 1 nozzle, the other 7 extend into
the adjacent slot's airspace and could hit tall labware.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.trash import Trash

# OT-2 slot number → Flex slot identifier mapping
_OT2_TO_FLEX = {
  1: "D1",
  2: "D2",
  3: "D3",
  4: "C1",
  5: "C2",
  6: "C3",
  7: "B1",
  8: "B2",
  9: "B3",
  10: "A1",
  11: "A2",
  12: "A3",
}

# Valid slot pattern: A-D followed by 1-4
_SLOT_PATTERN = re.compile(r"^[A-D][1-4]$")

# Row ordering from front (D, index 0) to rear (A, index 3)
_ROW_ORDER = ["D", "C", "B", "A"]

# Slot coordinates (mm) from ot3_standard.json v5 cutout positions.
# Origin is front-left corner of slot D1.
SLOT_LOCATIONS: Dict[str, Dict[str, float]] = {
  "D1": {"x": 0.0, "y": 0.0, "z": 0.0},
  "D2": {"x": 164.0, "y": 0.0, "z": 0.0},
  "D3": {"x": 328.0, "y": 0.0, "z": 0.0},
  "C1": {"x": 0.0, "y": 107.0, "z": 0.0},
  "C2": {"x": 164.0, "y": 107.0, "z": 0.0},
  "C3": {"x": 328.0, "y": 107.0, "z": 0.0},
  "B1": {"x": 0.0, "y": 214.0, "z": 0.0},
  "B2": {"x": 164.0, "y": 214.0, "z": 0.0},
  "B3": {"x": 328.0, "y": 214.0, "z": 0.0},
  "A1": {"x": 0.0, "y": 321.0, "z": 0.0},
  "A2": {"x": 164.0, "y": 321.0, "z": 0.0},
  "A3": {"x": 328.0, "y": 321.0, "z": 0.0},
}

# Staging area coordinates (column 4).
STAGING_LOCATIONS: Dict[str, Dict[str, float]] = {
  "D4": {"x": 492.0, "y": 0.0, "z": 14.5},
  "C4": {"x": 492.0, "y": 107.0, "z": 14.5},
  "B4": {"x": 492.0, "y": 214.0, "z": 14.5},
  "A4": {"x": 492.0, "y": 321.0, "z": 14.5},
}

# Slot bounding box (mm)
SLOT_WIDTH = 128.0  # x dimension
SLOT_DEPTH = 86.0  # y dimension

# Overall deck footprint (mm), including the frame around the slot grid.
_DECK_SIZE_X = 855.0
_DECK_SIZE_Y = 582.0

# Default clearance Z when no operation height is provided.
# Conservative estimate — measured at 51.3mm on real hardware
# (tip at bottom of flat well plate, A1 nozzle to deck surface).
_DEFAULT_CLEARANCE_Z = 50.0


class FlexDeck(Deck):
  """Opentrons Flex deck — 16 slots with placement and collision detection.

  Each slot (the 12 standard A1–D3 slots plus the 4 staging slots A4–D4)
  is modeled as a :class:`ResourceHolder` child assigned into this deck's
  resource tree, so labware placed at a slot is properly parented —
  ``resource.parent`` walks up through the slot holder to this deck, and
  ``get_absolute_location()`` works through the standard PLR mechanism.
  Labware is placed into a slot's holder with :meth:`assign_child_at_slot`.

  Example::

      deck = FlexDeck()
      deck.assign_child_at_slot(tip_rack, slot="C1")
      print(deck.summary())
      deck.check_single_nozzle_clearance("C3", primary_nozzle="H1")
  """

  def __init__(
    self,
    with_trash_bin: bool = True,
    name: str = "flex_deck",
  ) -> None:
    super().__init__(size_x=_DECK_SIZE_X, size_y=_DECK_SIZE_Y, size_z=0.0, name=name)

    self._slot_holders: Dict[str, ResourceHolder] = {}
    for slot_id, loc in {**SLOT_LOCATIONS, **STAGING_LOCATIONS}.items():
      holder = ResourceHolder(
        name=f"{self.name}_slot_{slot_id}",
        size_x=SLOT_WIDTH,
        size_y=SLOT_DEPTH,
        size_z=0,
      )
      self._slot_holders[slot_id] = holder
      super().assign_child_resource(holder, location=Coordinate(x=loc["x"], y=loc["y"], z=loc["z"]))

    if with_trash_bin:
      trash = Trash(name="trash", size_x=SLOT_WIDTH, size_y=SLOT_DEPTH, size_z=82.0)
      self.assign_child_at_slot(trash, "A3")

  # --- Slot Validation ---

  @staticmethod
  def _validate_slot(slot: str) -> str:
    """Validate and normalize a slot identifier. Returns uppercase slot."""
    slot = slot.upper()
    if _SLOT_PATTERN.match(slot):
      return slot

    # Check if user passed an OT-2 integer slot
    try:
      ot2_slot = int(slot)
      if 1 <= ot2_slot <= 12:
        flex_slot = _OT2_TO_FLEX[ot2_slot]
        raise ValueError(
          f"'{slot}' looks like an OT-2 slot number. "
          f"The Flex uses letter-number identifiers: "
          f"slot {ot2_slot} on OT-2 is '{flex_slot}' on the Flex. "
          f"Use deck.assign_child_at_slot(resource, slot='{flex_slot}')."
        )
    except ValueError as e:
      if "OT-2" in str(e):
        raise

    raise ValueError(
      f"Invalid slot identifier '{slot}'. "
      f"Must be A1–D3 (standard) or A4–D4 (staging). "
      f"Examples: 'C1', 'A3', 'B4'."
    )

  # --- Slot Access ---

  def get_slot_location(self, slot: str) -> Dict[str, float]:
    """Get the XYZ coordinate for a slot."""
    slot = self._validate_slot(slot)
    if slot in SLOT_LOCATIONS:
      return SLOT_LOCATIONS[slot]
    if slot in STAGING_LOCATIONS:
      return STAGING_LOCATIONS[slot]
    raise ValueError(f"Unknown slot '{slot}'.")

  def assign_child_at_slot(self, resource: Resource, slot: str) -> None:
    """Place a resource at a named slot.

    Args:
        resource: The resource (tip rack, plate, etc.) to place.
        slot: Slot identifier, e.g., "C1", "A4".

    Raises:
        ValueError: If slot is invalid or already occupied.
    """
    slot = self._validate_slot(slot)
    holder = self._slot_holders[slot]
    if holder.resource is not None:
      name = getattr(holder.resource, "name", str(holder.resource))
      raise ValueError(f"Slot {slot} is already occupied by '{name}'.")
    holder.assign_child_resource(resource)

  def unassign_child_at_slot(self, slot: str) -> None:
    """Remove a resource from a slot."""
    slot = self._validate_slot(slot)
    holder = self._slot_holders[slot]
    if holder.resource is not None:
      holder.unassign_child_resource(holder.resource)

  def get_slot(self, resource: Resource) -> Optional[str]:
    """Get the slot identifier for a placed resource, or None."""
    for slot_id, holder in self._slot_holders.items():
      if holder.resource is resource:
        return slot_id
    return None

  def get_resource_at_slot(self, slot: str) -> Optional[Resource]:
    """Return the resource placed at a slot, or None."""
    slot = self._validate_slot(slot)
    return self._slot_holders[slot].resource

  def get_trash_area(self) -> Trash:
    """Return the trash resource (default at A3)."""
    for holder in self._slot_holders.values():
      if isinstance(holder.resource, Trash):
        return holder.resource
    raise ValueError("No trash area configured on this deck.")

  # --- OT-2 Conversion ---

  @staticmethod
  def ot2_slot_to_flex(ot2_slot: int) -> str:
    """Convert an OT-2 slot number to the Flex equivalent.

    Useful for migrating protocols. E.g., 5 → "C2".
    """
    if ot2_slot not in _OT2_TO_FLEX:
      mapping = ", ".join(f"{k}→{v}" for k, v in sorted(_OT2_TO_FLEX.items()))
      raise ValueError(f"OT-2 slot must be 1–12, got {ot2_slot}. Full mapping: {mapping}")
    return _OT2_TO_FLEX[ot2_slot]

  # --- Collision Detection ---

  def check_single_nozzle_clearance(
    self,
    slot: str,
    primary_nozzle: str = "H1",
    operation_z: Optional[float] = None,
  ) -> None:
    """Check that adjacent slots are clear for single-nozzle operations.

    When an 8-channel pipette uses a single nozzle, the 7 inactive
    nozzles extend ~63mm into the adjacent slot's airspace. Two rules:

    1. TipRack in adjacent slot → always blocked (inactive nozzles
       would physically engage tips).
    2. Other labware → blocked if taller than the operation Z
       (the height the nozzle descends to).

    Args:
        slot: Deck slot where the operation happens.
        primary_nozzle: "H1" (front) or "A1" (rear).
        operation_z: The Z height the nozzle descends to (mm).
            If None, uses the default conservative threshold.

    Raises:
        ValueError: If a collision risk is detected.
    """
    from pylabrobot.resources.tip_rack import TipRack

    slot = self._validate_slot(slot)
    row = slot[0]
    col = slot[1]
    row_idx = _ROW_ORDER.index(row)

    if primary_nozzle == "H1":
      # Front nozzle → inactive extend toward rear
      if row_idx + 1 < len(_ROW_ORDER):
        danger_slot = f"{_ROW_ORDER[row_idx + 1]}{col}"
      else:
        return  # Rearmost row (A), nothing behind
    elif primary_nozzle == "A1":
      # Rear nozzle → inactive extend toward front
      if row_idx - 1 >= 0:
        danger_slot = f"{_ROW_ORDER[row_idx - 1]}{col}"
      else:
        return  # Frontmost row (D), nothing in front
    else:
      return  # Other nozzle configs — skip for now

    resource = self._slot_holders[danger_slot].resource
    if resource is None:
      return  # Slot empty, safe

    direction = "behind" if primary_nozzle == "H1" else "in front of"
    name = getattr(resource, "name", str(resource))

    # Rule 1: TipRack always blocked — nozzles would grab tips
    if isinstance(resource, TipRack):
      raise ValueError(
        f"Collision risk: single-nozzle operation at {slot} "
        f"with nozzle {primary_nozzle} — the 7 inactive nozzles "
        f"extend into slot {danger_slot}, which contains tip rack "
        f"'{name}'. Inactive nozzles would engage tips. "
        f"Move the tip rack or use a different nozzle direction."
      )

    # Rule 2: Other labware — check against operation Z
    if hasattr(resource, "get_size_z"):
      resource_z = resource.get_size_z()
    else:
      resource_z = getattr(resource, "_size_z", 0) or getattr(resource, "size_z", 0)

    clearance_z = operation_z if operation_z is not None else _DEFAULT_CLEARANCE_Z

    if resource_z > clearance_z:
      raise ValueError(
        f"Collision risk: single-nozzle operation at {slot} "
        f"with nozzle {primary_nozzle} — the 7 inactive nozzles "
        f"extend into slot {danger_slot} at Z={clearance_z:.0f}mm, "
        f"which contains '{name}' (height {resource_z:.0f}mm). "
        f"Move '{name}' to a different slot, or use a slot with "
        f"no tall labware {direction} it."
      )

  def check_deck_clearance(self, slot: str, operation: str = "move") -> None:
    """Verify a slot has labware for an operation that requires it."""
    slot = self._validate_slot(slot)
    resource = self._slot_holders[slot].resource
    if resource is None and operation in ("pick_up_tips", "aspirate", "dispense"):
      raise ValueError(
        f"Cannot {operation} at slot {slot}: no labware assigned. "
        f"Use deck.assign_child_at_slot(resource, slot='{slot}') first."
      )

  # --- Summary ---

  def summary(self) -> str:
    """ASCII representation of the Flex deck.

    Example::

        Flex Deck (855mm x 582mm)

        +----------+----------+----------+----------+
        | A1       | A2       | A3       | A4       |
        | Empty    | Empty    | trash    | (staging)|
        +----------+----------+----------+----------+
        | B1       | B2       | B3       | B4       |
        | Empty    | Empty    | Empty    | (staging)|
        +----------+----------+----------+----------+
        ...
    """

    def _slot_label(slot_id: str) -> str:
      resource = self._slot_holders[slot_id].resource
      if resource is None:
        if slot_id.endswith("4"):
          return "(staging)"
        return "Empty"
      name = getattr(resource, "name", str(resource))
      if len(name) > 8:
        name = name[:6] + ".."
      return name

    sep = "+----------+----------+----------+----------+"
    lines = [
      f"Flex Deck ({self.get_absolute_size_x():g}mm x {self.get_absolute_size_y():g}mm)",
      "",
      sep,
    ]

    for row_letter in "ABCD":
      row_ids = [f"| {row_letter}{col}       " for col in "1234"]
      row_names = [f"| {_slot_label(f'{row_letter}{col}'):8s} " for col in "1234"]
      lines.append("".join(row_ids) + "|")
      lines.append("".join(row_names) + "|")
      lines.append(sep)

    return "\n".join(lines)

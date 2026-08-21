"""Labware definitions and deck tracking.

Defines a normalized, physical description of a labware item
(:class:`LabwareDefinition`, :class:`Labware`), a simple in-memory catalog of
definitions (:class:`LabwareCatalog`, :class:`InMemoryLabwareCatalog`), and
per-location stack tracking for the nine deck locations
(:class:`LabwareStack`, :class:`DeckState`).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from ..types import MAX_LOCATIONS, MIN_LOCATION

logger = logging.getLogger(__name__)


@dataclass
class LabwareDefinition:
  """A normalized labware definition: dimensions, stacking, and well geometry.

  Every field is a plain value with a zero/empty default, so a definition can
  be constructed with only the fields that matter for a given piece of
  labware.
  """

  id: str
  name: str
  kind: str
  vendor: str = ""
  catalog_number: str = ""
  description: str = ""
  base_class: str = ""
  wells: int = 0
  length_mm: float = 0.0
  width_mm: float = 0.0
  height_mm: float = 0.0
  stack_height_mm: float = 0.0
  gripper_offset_mm: float = 0.0
  lid_gripper_offset_mm: Optional[float] = None
  empty_check_offset_mm: Optional[float] = None
  shim_thickness_mm: float = 0.0
  can_be_sealed: bool = False
  sealed_height_mm: float = 0.0
  sealed_stacking_height_mm: float = 0.0
  can_have_lid: bool = False
  lidded_height_mm: float = 0.0
  lidded_stack_height_mm: float = 0.0
  lid_resting_height_mm: float = 0.0
  lid_departure_height_mm: float = 0.0
  max_robot_handling_speed: str = ""
  rows: int = 0
  cols: int = 0
  well_depth_mm: float = 0.0
  offset_x_mm: float = 0.0
  offset_y_mm: float = 0.0
  spacing_x_mm: float = 0.0
  spacing_y_mm: float = 0.0
  well_volume_ul: float = 0.0
  well_diameter_mm: float = 0.0
  disposable_tip_capacity_ul: float = 0.0
  tip_definition_id: str = ""
  supported_tip_ids: list[str] = field(default_factory=list)
  # Mount-ability flags — consulted by mount/unmount plate handling.
  # can_mount        = "this plate can sit on top of another and lock into
  #                    it" (e.g. a filter plate that nests into a
  #                    collection plate for vacuum filtration).
  # can_be_mounted   = "another plate can sit on top of me and lock in"
  #                    (e.g. a collection plate that accepts a filter).
  # Both flags are soft — a mount task should validate them but treat a
  # missing flag as a warning rather than a hard rejection.
  can_mount: bool = False
  can_be_mounted: bool = False

  def to_summary(self) -> dict[str, Any]:
    """Return this definition as a plain dict."""
    return asdict(self)


@dataclass
class Labware:
  """Physical description of a single labware item on the deck."""

  id: str
  name: str
  height: float
  width: float
  length: float
  labware_type: str = ""
  gripper_offset: float = 0.0
  stack_height: float = 0.0
  is_lidded: bool = False
  is_sealed: bool = False
  definition_id: str = ""
  wells: int = 0
  metadata: dict[str, Any] = field(default_factory=dict)
  # Instance-level barcode. Populated by sensor/barcode-read tasks; travels
  # with the plate across pick/place because LabwareStack.remove_top() and
  # .add() preserve the live instance. Distinct from `metadata` (which is
  # definition-level).
  barcode: str = ""
  # Free-form per-instance annotations written by workflow scripts
  # (`plate.tags["lane"] = "A2"`). Like `barcode`, travels with the plate
  # across pick/place/stack because it's on the live instance, not the
  # definition. Distinct from `metadata` (which is definition-level and
  # shared across all instances from a single LabwareDefinition).
  tags: dict[str, Any] = field(default_factory=dict)
  # Mount state — instance-level, set True when this labware is placed on
  # top of another and they become a locked unit (e.g. a filter plate
  # mounted on a collection plate for vacuum filtration). When True,
  # pick/place transports this labware together with the one immediately
  # below it on the stack. Travels with the instance across moves.
  is_mounted: bool = False

  @classmethod
  def from_definition(
    cls,
    definition: LabwareDefinition,
    *,
    is_lidded: bool = False,
    is_sealed: bool = False,
  ) -> "Labware":
    """Build a :class:`Labware` instance from a definition.

    Args:
      definition: The labware definition to instantiate.
      is_lidded: Whether this instance currently has a lid on.
      is_sealed: Whether this instance is currently sealed.

    Returns:
      A new instance whose height/stack height reflect the lidded/sealed
      state, with a metadata dict carrying the full definition plus derived
      geometry fields.
    """
    height, stack_height = _active_labware_geometry(
      definition,
      is_lidded=is_lidded,
      is_sealed=is_sealed,
    )
    metadata = definition.to_summary()
    metadata["base_height_mm"] = float(definition.height_mm or height)
    metadata["total_height_mm"] = float(height)
    metadata["is_lidded"] = bool(is_lidded)
    metadata["is_sealed"] = bool(is_sealed)
    metadata["height_mm"] = float(height)
    metadata["stack_height_mm"] = float(stack_height)
    if is_lidded:
      generated_lid = generated_lid_metadata(metadata)
      if generated_lid is not None:
        metadata["generated_lid"] = generated_lid
    return cls(
      id=definition.id,
      definition_id=definition.id,
      name=definition.name,
      height=height,
      width=definition.width_mm,
      length=definition.length_mm,
      labware_type=definition.kind,
      gripper_offset=definition.gripper_offset_mm,
      stack_height=stack_height,
      is_lidded=is_lidded,
      is_sealed=is_sealed,
      wells=definition.wells,
      metadata=metadata,
    )


def _active_labware_geometry(
  definition: LabwareDefinition,
  *,
  is_lidded: bool,
  is_sealed: bool,
) -> tuple[float, float]:
  """Return the (height, stack_height) that apply for a lidded/sealed state.

  Args:
    definition: The labware definition to read geometry from.
    is_lidded: Whether the lidded height/stack-height should apply.
    is_sealed: Whether the sealed height/stack-height should apply.

  Returns:
    ``(height_mm, stack_height_mm)`` for the requested state, falling back
    to the base (bare) height wherever a state-specific value is zero.
  """
  base_height = float(definition.height_mm or 0.0)
  base_stack_height = float(definition.stack_height_mm or base_height)
  if is_lidded:
    height = float(definition.lidded_height_mm or base_height)
    stack_height = float(definition.lidded_stack_height_mm or height)
    return height, stack_height
  if is_sealed:
    height = float(definition.sealed_height_mm or base_height)
    stack_height = float(definition.sealed_stacking_height_mm or height)
    return height, stack_height
  return base_height, base_stack_height


def lid_thickness_mm(metadata: Optional[dict[str, Any]]) -> float:
  """Return a plate's lid thickness, inferred from its metadata.

  Args:
    metadata: The plate's metadata dict (as produced by
      :meth:`Labware.from_definition`).

  Returns:
    The lid thickness in millimetres, preferring
    ``lidded_height_mm - lid_resting_height_mm``, then
    ``lidded_height_mm - base_height_mm``, then ``lid_resting_height_mm``
    alone, and finally a 0.1 mm floor so a lid is never zero-thickness.
  """
  meta = metadata or {}
  resting_height = float(meta.get("lid_resting_height_mm") or 0.0)
  lidded_height = float(meta.get("lidded_height_mm") or 0.0)
  if lidded_height > 0.0 and resting_height > 0.0 and lidded_height > resting_height:
    return max(0.1, lidded_height - resting_height)
  base_height = float(meta.get("base_height_mm") or meta.get("height_mm") or 0.0)
  if lidded_height > 0.0 and base_height > 0.0 and lidded_height > base_height:
    return max(0.1, lidded_height - base_height)
  if resting_height > 0.0:
    return max(0.1, resting_height)
  return 0.1


def lid_gripper_offset_mm(
  metadata: Optional[dict[str, Any]],
  *,
  fallback_gripper_offset_mm: float = 0.0,
  label: str = "labware",
) -> float:
  """Return the gripper offset to use when handling a plate's lid.

  Args:
    metadata: The plate's metadata dict.
    fallback_gripper_offset_mm: The offset to fall back to when no explicit
      lid gripper offset is configured.
    label: A human-readable name for the plate, used only in the warning
      logged when the fallback exceeds the lid's own thickness.

  Returns:
    The explicit ``lid_gripper_offset_mm``/``robot_lid_gripper_offset_mm``
    metadata value if present; otherwise the fallback offset, clamped to the
    lid's thickness (with a warning) so the gripper never targets a depth
    beyond the lid itself.
  """
  meta = dict(metadata or {})
  explicit = meta.get("lid_gripper_offset_mm")
  if explicit is None:
    explicit = meta.get("robot_lid_gripper_offset_mm")
  if explicit is not None:
    return max(0.0, float(explicit))

  fallback = max(
    0.0,
    float(
      fallback_gripper_offset_mm
      or meta.get("gripper_offset_mm")
      or meta.get("robot_gripper_offset_mm")
      or 0.0
    ),
  )
  lid_height = lid_thickness_mm(meta)
  if fallback > lid_height:
    logger.warning(
      "Clamping fallback lid gripper offset for %s from %.3f to lid thickness %.3f; "
      "configure robot_lid_gripper_offset_mm for correct lid handling geometry",
      label,
      fallback,
      lid_height,
    )
    fallback = lid_height
  return fallback


def generated_lid_metadata(metadata: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
  """Derive metadata for a synthetic lid sized to a plate's footprint.

  Args:
    metadata: The plate's metadata dict.

  Returns:
    A metadata dict for the lid, or ``None`` if the plate has no valid
    length/width to size the lid against.
  """
  meta = dict(metadata or {})
  length_mm = float(meta.get("length_mm") or meta.get("length") or 0.0)
  width_mm = float(meta.get("width_mm") or meta.get("width") or 0.0)
  if length_mm <= 0.0 or width_mm <= 0.0:
    return None
  thickness_mm = lid_thickness_mm(meta)
  grip_offset_mm = lid_gripper_offset_mm(
    meta,
    fallback_gripper_offset_mm=float(
      meta.get("gripper_offset_mm") or meta.get("robot_gripper_offset_mm") or 0.0
    ),
    label=str(meta.get("name") or "labware"),
  )
  return {
    "name": f"{meta.get('name', 'Labware')} Lid",
    "kind": "lid",
    "base_class": "lid",
    "length_mm": length_mm,
    "width_mm": width_mm,
    "height_mm": thickness_mm,
    "stack_height_mm": thickness_mm,
    "lid_thickness_mm": thickness_mm,
    "lid_gripper_offset_mm": grip_offset_mm,
    "lid_resting_height_mm": float(meta.get("lid_resting_height_mm") or 0.0),
    "lid_departure_height_mm": float(meta.get("lid_departure_height_mm") or 0.0),
    "source_plate_name": str(meta.get("name") or ""),
    "render_mode": "generated_lid",
  }


def synthesize_lid_labware(plate: Labware) -> Labware:
  """Build a standalone lid :class:`Labware` sized to fit *plate*.

  Args:
    plate: The plate to synthesize a lid for.

  Returns:
    A new ``Labware`` representing the lid, with its own id
    (``f"{plate.id}::lid"``) and the source plate's ``definition_id``.

  Raises:
    ValueError: If the plate's metadata has no valid length/width footprint.
  """
  plate_meta = dict(plate.metadata or {})
  lid_meta = generated_lid_metadata(plate_meta)
  if lid_meta is None:
    raise ValueError(f"Cannot synthesize lid for labware without valid footprint: {plate.name}")
  return Labware(
    id=f"{plate.id}::lid",
    definition_id=plate.definition_id,
    name=str(lid_meta["name"]),
    height=float(lid_meta["height_mm"]),
    width=float(lid_meta["width_mm"]),
    length=float(lid_meta["length_mm"]),
    labware_type="lid",
    gripper_offset=float(lid_meta.get("lid_gripper_offset_mm") or 0.0),
    stack_height=float(lid_meta["stack_height_mm"]),
    is_lidded=False,
    is_sealed=False,
    wells=0,
    metadata=lid_meta,
  )


class LabwareCatalog:
  """Lookup interface for normalized labware definitions."""

  def list_definitions(self) -> list[LabwareDefinition]:
    """Return every definition in the catalog."""
    raise NotImplementedError

  def get_definition(self, labware_id: str) -> Optional[LabwareDefinition]:
    """Return the definition with the given id, or ``None`` if not found."""
    raise NotImplementedError


class InMemoryLabwareCatalog(LabwareCatalog):
  """A fixed, in-memory list of labware definitions."""

  def __init__(
    self,
    definitions: list[LabwareDefinition],
    *,
    aliases: Optional[dict[str, str]] = None,
  ) -> None:
    """Initialize the catalog.

    Args:
      definitions: The definitions the catalog serves.
      aliases: Extra lookup ids that resolve to an existing definition's id,
        for ids that a caller might still reference. An alias that collides
        with a real definition id is ignored.
    """
    self._definitions = list(definitions)
    self._by_id = {d.id: d for d in definitions}
    for alias_id, canonical_id in dict(aliases or {}).items():
      if alias_id in self._by_id:
        continue
      canonical = self._by_id.get(canonical_id)
      if canonical is not None:
        self._by_id[alias_id] = canonical

  def list_definitions(self) -> list[LabwareDefinition]:
    """Return every definition in the catalog."""
    return list(self._definitions)

  def get_definition(self, labware_id: str) -> Optional[LabwareDefinition]:
    """Return the definition with the given id or alias, or ``None``."""
    return self._by_id.get(labware_id)


class LabwareStack:
  """An ordered stack of :class:`Labware` at a single deck position."""

  def __init__(self) -> None:
    self._items: list[Labware] = []

  def add(self, labware: Labware) -> None:
    """Push *labware* onto the top of the stack."""
    self._items.append(labware)

  def replace(self, labware: Labware) -> None:
    """Replace the entire stack with a single item."""
    self._items = [labware]

  def remove_top(self) -> Labware:
    """Pop and return the top item.

    Raises:
      IndexError: If the stack is empty.
    """
    if not self._items:
      raise IndexError("Cannot remove from an empty labware stack")
    return self._items.pop()

  def get_total_height(self) -> float:
    """Return the full physical height of the stack, in millimetres.

    Every item but the top contributes its stacking thickness (how much it
    adds once another item is nested/stacked on it); the top item
    contributes its full height.
    """
    if not self._items:
      return 0.0
    if len(self._items) == 1:
      return self._items[0].height
    total = 0.0
    for item in self._items[:-1]:
      total += item.stack_height or item.height
    total += self._items[-1].height
    return total

  def get_location_height(self) -> float:
    """Return vendor-style pickup offset above the taught plate-pad plane.

    This matches the vendor GetLocationHeight semantics for a visible top
    plate: the offset corresponds to the support surface under the top plate
    rather than the full physical height to the top of that plate.
    """
    if not self._items:
      return 0.0
    return max(0.0, self.get_total_height() - self._items[-1].height)

  def get_stacking_height(self) -> float:
    """Return the placement support height using stacking thickness.

    For placing a new plate onto a destination stack, this is the effective
    support surface produced by the plates already present, not the full
    physical top height of the visible top plate.
    """
    total = 0.0
    for item in self._items:
      total += item.stack_height or item.height
    return max(0.0, total)

  @property
  def top(self) -> Optional[Labware]:
    """Return the top item, or ``None`` if the stack is empty."""
    return self._items[-1] if self._items else None

  @property
  def items(self) -> list[Labware]:
    """Return the stack's items, bottom-first."""
    return list(self._items)

  def mounted_group_from_top(self) -> list[Labware]:
    """Return the slice of plates that move as a unit when the top is picked up.

    Returns items top->bottom.

    A plate with ``is_mounted=True`` is physically locked to the plate
    immediately below it (filter plate on collection plate, etc.), so
    picking the top drags the bottom along. This walks the stack downward
    from the top, including each subsequent plate while the plate above it
    is ``is_mounted``.

    Always returns at least one item for a non-empty stack — the top itself
    — so callers can treat the result as "the thing we actually pick up"
    regardless of mount state.
    """
    if not self._items:
      return []
    group: list[Labware] = [self._items[-1]]
    i = len(self._items) - 1
    # While the current top of the group is mounted, the plate immediately
    # below it moves with us.
    while i > 0 and group[-1].is_mounted:
      i -= 1
      group.append(self._items[i])
    return group

  def get_support_height_below_group(self) -> float:
    """Return the stacking-surface height under the mounted group at the top.

    This is what a gripper needs to clear on the way down to engage the
    bottom plate of the mounted group by its flanges.

    * For an ordinary (unmounted) stack, the group is just the top plate, so
      this is identical to :meth:`get_location_height` — everything
      beneath the top is support.
    * For a mounted pair at the top (filter on collection), the group is
      both plates. The gripper must engage the collection plate's flanges,
      which sit at the height of any plates below the collection plate
      (possibly zero if the pair is directly on the pad).
    """
    if not self._items:
      return 0.0
    group_size = len(self.mounted_group_from_top())
    below_count = len(self._items) - group_size
    if below_count <= 0:
      return 0.0
    # Stack heights of everything that stays put when the group lifts off —
    # matches the semantics of get_stacking_height() applied to the
    # sub-stack below the group.
    total = 0.0
    for item in self._items[:below_count]:
      total += item.stack_height or item.height
    return max(0.0, total)

  def __len__(self) -> int:
    return len(self._items)

  def __bool__(self) -> bool:
    return bool(self._items)


class DeckState:
  """Tracks a :class:`LabwareStack` at each of the nine deck locations."""

  def __init__(self) -> None:
    self._stacks: dict[int, LabwareStack] = {
      loc: LabwareStack() for loc in range(MIN_LOCATION, MAX_LOCATIONS + 1)
    }

  def _validate_location(self, location: int) -> None:
    if not (MIN_LOCATION <= location <= MAX_LOCATIONS):
      raise ValueError(f"Location must be {MIN_LOCATION}-{MAX_LOCATIONS}, got {location}")

  def add(self, location: int, labware: Labware) -> None:
    """Push *labware* onto the stack at *location*."""
    self._validate_location(location)
    self._stacks[location].add(labware)

  def set_single(self, location: int, labware: Labware) -> None:
    """Replace the stack at *location* with a single item."""
    self._validate_location(location)
    self._stacks[location].replace(labware)

  def remove(self, location: int) -> Labware:
    """Pop and return the top item at *location*."""
    self._validate_location(location)
    return self._stacks[location].remove_top()

  def remove_mounted_group(self, location: int) -> list[Labware]:
    """Remove every plate that moves as a unit from the top of *location*.

    Returns them top-first.

    Semantically a superset of :meth:`remove` — on an unmounted stack this
    is identical to removing the top plate (returns a single-item list). On
    a mounted pair (or N-level mounted stack) every locked member pops
    together so the move-to-destination side can re-add them as a unit.
    """
    self._validate_location(location)
    stack = self._stacks[location]
    group = stack.mounted_group_from_top()
    # Pop them off the stack in the same order the caller will re-add them
    # (top-first). This keeps the bottom of the mounted group as the new
    # top of the source after the move.
    for _ in group:
      stack.remove_top()
    return group

  def add_mounted_group(self, location: int, group: list[Labware]) -> None:
    """Place a mounted group produced by :meth:`remove_mounted_group` onto *location*.

    Expects the group top-first, the same ordering that removal returns, so
    items are pushed in reverse to preserve the original bottom->top layout.
    """
    self._validate_location(location)
    stack = self._stacks[location]
    for labware in reversed(group):
      stack.add(labware)

  def get_stack(self, location: int) -> LabwareStack:
    """Return the :class:`LabwareStack` at *location*."""
    self._validate_location(location)
    return self._stacks[location]

  def get_height(self, location: int) -> float:
    """Return the full physical stack height at *location*, in millimetres."""
    self._validate_location(location)
    return self._stacks[location].get_total_height()

  def get_location_height(self, location: int) -> float:
    """Return the vendor-style pickup offset at *location*, in millimetres."""
    self._validate_location(location)
    return self._stacks[location].get_location_height()

  def get_stacking_height(self, location: int) -> float:
    """Return the placement support height at *location*, in millimetres."""
    self._validate_location(location)
    return self._stacks[location].get_stacking_height()

  def get_all_heights(self) -> dict[int, float]:
    """Return the full physical stack height at every deck location."""
    return {loc: stack.get_total_height() for loc, stack in self._stacks.items()}

  def clear(self, location: int) -> None:
    """Empty the stack at *location*."""
    self._validate_location(location)
    self._stacks[location] = LabwareStack()

  def clear_all(self) -> None:
    """Empty every stack on the deck."""
    for loc in self._stacks:
      self._stacks[loc] = LabwareStack()

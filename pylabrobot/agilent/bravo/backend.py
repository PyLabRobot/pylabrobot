"""PyLabRobot :class:`~pylabrobot.legacy.liquid_handling.backends.backend.LiquidHandlerBackend`
for the Agilent Bravo.

Translates PyLabRobot's standard liquid-handling operations
(:mod:`pylabrobot.legacy.liquid_handling.standard`) into calls on a
:class:`~.bravo.Bravo` facade. The Bravo's own hardware model -- a single
plunger drive shared by every active barrel, and barrels only activatable
as a contiguous rectangular block anchored at one of the head's four
corners (see :mod:`.head_mode`) -- shapes almost every choice this module
makes:

- A per-channel operation's target wells or tip spots are translated into
  a :class:`~.block.HeadBlock` (see :mod:`.block`), which becomes the
  active head mode plus the plate/tipbox anchor cell that block sits at.
- Volume and flow rate must be identical across every channel in one
  aspirate/dispense call, because they drive the one shared plunger.
- ``aspirate96``/``dispense96`` always drive the whole head; their ``wells``
  list only ever locates the anchor well, never a shape, since a 96-head
  operation cannot select a subset of its own 96 barrels.

**384-channel heads:** this backend's per-channel path is channel-count
agnostic and works with a 384-channel head like any other. The *_tips96*/
``aspirate96``/``dispense96`` path is not reachable for one, though: PyLabRobot's
own :meth:`~pylabrobot.legacy.liquid_handling.liquid_handler.LiquidHandler.pick_up_tips96`
(and the sibling 96-head methods) hard-require exactly 96 items on the
target tip rack/plate before this backend is ever called, and this backend
additionally rejects a *_tips96*/``aspirate96``/``dispense96`` call outright
when the installed head does not have exactly 96 channels (see
:meth:`AgilentBravoBackend._require_96_channel_head`). Drive a 384-channel
head through the per-channel path instead.

**Unverified against real hardware:** the protocol and controller layers
this backend ultimately drives are exercised against real Agilent Bravo
instruments, but the PyLabRobot transport, deck mapping
(:mod:`.deck.resource`), and this backend itself are not. Please report any
issue at https://discuss.pylabrobot.org.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple, Union

from pylabrobot.legacy.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  DropTipRack,
  GripDirection,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot

from .block import HeadBlock, head_block_for_identifiers, parse_item_identifier
from .bravo import Bravo
from .deck.resource import BravoDeck
from .head_mode import HeadGeometry
from .tips import get_tip_definition
from .types import MAX_LOCATIONS, MIN_LOCATION

logger = logging.getLogger(__name__)

_SITE_COORDINATE_TOLERANCE_MM = 1.0
"""Tolerance, in millimetres, for matching a :class:`~pylabrobot.resources.coordinate.Coordinate`
against a Bravo deck site's taught X/Y, in :meth:`AgilentBravoBackend._site_for_coordinate`.

Chosen, not measured or derived from any hardware or PyLabRobot-documented
tolerance: adjacent deck sites are roughly 187 mm apart in X and 109 mm
apart in Y (:data:`~.types.X_TO_X_DISTANCE`/:data:`~.types.Y_TO_Y_DISTANCE`),
so anything up to several millimetres is nowhere near enough to confuse
two different sites for each other -- the risk this value trades against
is entirely on the other side: too tight, and a *legitimate* destination
computed by PyLabRobot's own resource-location arithmetic (composed
through :meth:`~pylabrobot.legacy.liquid_handling.liquid_handler.LiquidHandler.drop_resource`'s
offset/rotation math, which can accumulate more floating-point drift than
a single subtraction) gets rejected as "no site match" even though it was
plainly meant for one. 1 mm sits comfortably above ordinary floating-point
drift and comfortably below the ~100 mm+ gap between any two sites; it is
not a claim about the instrument's positioning accuracy."""


def _subset_for_block(block: HeadBlock, geometry: HeadGeometry) -> Tuple[str, int, int]:
  """Return the ``(subset_type, row_count, column_count)`` a block reduces to.

  Every shape reduces to the most specific mode :mod:`.head_mode` supports:
  the whole head, a single barrel, a full row or column, or a rectangle.
  The block is always anchored at head corner ``"back_left"`` -- any corner
  would do, since the caller separately pins the matching plate/tipbox
  anchor cell (see :meth:`AgilentBravoBackend._apply_channel_block`); this
  just needs to be consistent between the two.

  Args:
    block: The block of active barrels.
    geometry: The installed head's own barrel grid.

  Returns:
    The subset type and row/column counts to pass to
    :meth:`~.bravo.Bravo.set_head_mode`.
  """
  if block.num_rows == geometry.rows and block.num_columns == geometry.columns:
    return "all_barrels", geometry.rows, geometry.columns
  if block.num_rows == 1 and block.num_columns == 1:
    return "single_barrel", 1, 1
  if block.num_columns == geometry.columns:
    return "row", block.num_rows, geometry.columns
  if block.num_rows == geometry.rows:
    return "column", geometry.rows, block.num_columns
  return "rectangle", block.num_rows, block.num_columns


def _uniform_value(values: Sequence[float], *, label: str) -> float:
  """Return the single value shared by *values*, raising if they differ.

  Args:
    values: The value each channel in one call supplied. Never empty for a
      well-formed call (one entry per channel operation).
    label: What the value represents, used in the error message.

  Returns:
    The single shared value.

  Raises:
    RuntimeError: If *values* contains more than one distinct value: the
      Bravo head has a single plunger drive, so every channel in one
      aspirate/dispense call must move it the same way.
  """
  distinct = sorted({float(v) for v in values})
  if len(distinct) > 1:
    raise RuntimeError(
      f"AgilentBravoBackend requires a single {label} across every channel in one call, "
      f"because the Bravo head has one plunger drive shared by every active barrel; "
      f"got {distinct}."
    )
  return distinct[0]


def _uniform_optional(values: Sequence[Optional[float]], *, label: str) -> Optional[float]:
  """Return the single non-``None`` value shared by *values*, or ``None``.

  Args:
    values: The value each channel in one call supplied; ``None`` means
      "no override" and is excluded from the uniformity check.
    label: What the value represents, used in the error message.

  Returns:
    The single shared override, or ``None`` if every value was ``None``.

  Raises:
    RuntimeError: If more than one distinct override value is present.
  """
  present = sorted({float(v) for v in values if v is not None})
  if len(present) > 1:
    raise RuntimeError(
      f"AgilentBravoBackend requires a single {label} across every channel in one call, "
      f"because the Bravo head has one plunger drive shared by every active barrel; "
      f"got {present}."
    )
  return present[0] if present else None


def _require_itemized(parent: Optional[Resource], *, role: str) -> ItemizedResource:
  """Return *parent* as an :class:`~pylabrobot.resources.itemized_resource.ItemizedResource`.

  Args:
    parent: The candidate parent resource, e.g. a well's or tip spot's
      ``.parent``.
    role: What kind of resource *parent* is expected to be, for the error
      message.

  Raises:
    RuntimeError: If *parent* is ``None`` or not itemized, and so has no
      ``get_child_identifier`` to resolve an item's identifier from.
  """
  if parent is None or not isinstance(parent, ItemizedResource):
    raise RuntimeError(
      f"Expected an itemized {role} (with its own A1-style item grid), got {parent!r}."
    )
  return parent


class AgilentBravoBackend(LiquidHandlerBackend):
  """PyLabRobot liquid-handling backend for the Agilent Bravo.

  Wraps a :class:`~.bravo.Bravo` facade, translating PyLabRobot's standard
  operations into calls on it. See the module docstring for the shape of
  that translation and its hardware-driven limits.
  """

  def __init__(self, bravo: Bravo) -> None:
    """Initialize the backend.

    Args:
      bravo: The Bravo facade to operate. Already constructed by the
        caller (with whatever controller, transport, and config it needs);
        this class does no construction of its own.
    """
    super().__init__()
    self._bravo = bravo
    self._synced_resources: Dict[int, Optional[Resource]] = {}

  @property
  def bravo(self) -> Bravo:
    """The :class:`~.bravo.Bravo` facade this backend operates."""
    return self._bravo

  # -- Lifecycle --

  async def setup(self) -> None:
    """Bring the underlying :class:`~.bravo.Bravo` facade online.

    Logs the unverified-hardware warning described in the module
    docstring, then delegates to :meth:`~.bravo.Bravo.setup`. Does not
    home any axis; call :meth:`~.bravo.Bravo.home` or
    :meth:`~.bravo.Bravo.initialize` on :attr:`bravo` directly first if the
    protocol needs a cold-start sequence.
    """
    await super().setup()
    logger.warning(
      "AgilentBravoBackend is unverified against real Agilent Bravo hardware: the "
      "protocol and controller layers it drives are exercised against real "
      "instruments, but the PyLabRobot transport, deck mapping, and this backend "
      "are not. Please report any issue at https://discuss.pylabrobot.org."
    )
    await self._bravo.setup()
    self.setup_finished = True

  async def stop(self) -> None:
    """Take the underlying :class:`~.bravo.Bravo` facade offline."""
    await self._bravo.stop()
    self.setup_finished = False

  @property
  def num_channels(self) -> int:
    """The number of physical channels on the installed head (rows x columns)."""
    geometry = self._bravo.head_geometry
    return geometry.rows * geometry.columns

  @property
  def num_arms(self) -> int:
    """The number of robotic arms PyLabRobot can drive for resource pick/move/drop.

    1 when the installed model has a gripper, 0 when it does not (e.g. the
    gripperless SRT; see :attr:`~.bravo.Bravo.has_gripper`). The base
    class's own default of 0 is exactly right for the SRT, but wrong for
    every gripper-equipped model: left unset, ``LiquidHandler.setup()``
    builds an empty ``_resource_pickups`` map
    (``{a: None for a in range(self.backend.num_arms)}``), and every
    gripper call -- including the common ``LiquidHandler.move_plate()`` --
    raises PyLabRobot's own "No robotic arm is installed on this liquid
    handler" before this backend's :meth:`pick_up_resource` is ever
    reached, regardless of how complete that method is. Deriving this
    from :attr:`~.bravo.Bravo.has_gripper` means the SRT still reports 0
    and gets that same PyLabRobot error -- the right layering, since
    :meth:`_require_gripper` exists for a caller that bypasses
    ``LiquidHandler`` and calls this backend directly.
    """
    return 1 if self._bravo.has_gripper else 0

  @property
  def head96_installed(self) -> Optional[bool]:
    """Whether the installed head has exactly 96 channels.

    Overrides the base class's own hardcoded ``False`` for the same
    reason :attr:`num_arms` does: ``LiquidHandler.setup()`` sizes its
    ``head96`` tip-tracker dict from this flag (96 entries if true, none
    if false), and a real 96-head call -- ``pick_up_tips96``,
    ``drop_tips96``, ``aspirate96``, ``dispense96`` -- indexes that dict
    for every one of its 96 channels. Left at the base class's ``False``,
    every one of those calls would raise a ``KeyError`` against an empty
    dict before this backend's own, more informative
    :meth:`_require_96_channel_head` rejection is ever reached.
    """
    geometry = self._bravo.head_geometry
    return geometry.rows * geometry.columns == 96

  # -- Deck/head resolution --

  def _bravo_deck(self) -> BravoDeck:
    """Return the assigned deck, requiring it to be a :class:`~.deck.resource.BravoDeck`."""
    deck = self.deck
    if not isinstance(deck, BravoDeck):
      raise RuntimeError(
        f"AgilentBravoBackend requires a BravoDeck, got {type(deck).__name__}. Construct the "
        "LiquidHandler with a BravoDeck so PyLabRobot resources can be translated onto the "
        "instrument's nine deck sites."
      )
    return deck

  def _sync_site(self, deck: BravoDeck, site: int) -> None:
    """Sync a Bravo deck site's labware from *deck* if its occupant has changed.

    Compares the resource currently assigned to *site* against what this
    backend last saw there, by identity. A changed occupant (including
    becoming empty) is pushed into :attr:`bravo` via
    :meth:`~.bravo.Bravo.set_labware`/:meth:`~.bravo.Bravo.clear_labware`.
    An unchanged occupant is left alone, so tip occupancy Bravo already
    tracks for that site is not reset back to "full" on every call.
    """
    current = deck.resource_at_site(site)
    if site in self._synced_resources and self._synced_resources[site] is current:
      return
    if current is None:
      self._bravo.clear_labware(site)
    else:
      labware = deck.labware_for_site(site)
      if labware is None:
        raise RuntimeError(f"Internal error: no labware description for deck site {site}.")
      self._bravo.set_labware(site, labware)
    self._synced_resources[site] = current

  def _resolve_site(self, resource: Resource, *, role: str) -> int:
    """Return the deck site *resource* occupies, syncing it into :attr:`bravo`.

    Args:
      resource: The resource to locate -- a plate, tip rack, or other
        top-level occupant of a Bravo deck site.
      role: What kind of resource this is, for the error message.

    Raises:
      RuntimeError: If *resource* is not assigned to any Bravo deck site.
    """
    deck = self._bravo_deck()
    site = deck.site_for_resource(resource)
    if site is None:
      raise RuntimeError(
        f"The {role} '{resource.name}' is not assigned to a Bravo deck site. Assign it with "
        "BravoDeck.assign_child_at_site before using it in a liquid-handling operation."
      )
    self._sync_site(deck, site)
    return site

  def _site_for_coordinate(self, coordinate: Coordinate) -> int:
    """Return the Bravo deck site whose taught X/Y matches *coordinate*.

    The gripper facade (:meth:`~.bravo.Bravo.gripper_move`,
    :meth:`~.bravo.Bravo.gripper_place`) only knows how to target one of
    the instrument's nine taught sites, not an arbitrary coordinate; this
    matches PyLabRobot's resolved absolute coordinate back to whichever
    site it came from.

    Args:
      coordinate: The absolute (deck-relative) coordinate to match.

    Raises:
      RuntimeError: If no site's taught X/Y is within
        :data:`_SITE_COORDINATE_TOLERANCE_MM` of *coordinate*.
    """
    deck = self._bravo_deck()
    teachpoints = deck.teachpoints
    for site in range(MIN_LOCATION, MAX_LOCATIONS + 1):
      site_x = teachpoints.get_teachpoint(site, "x")
      site_y = teachpoints.get_teachpoint(site, "y")
      if (
        abs(coordinate.x - site_x) <= _SITE_COORDINATE_TOLERANCE_MM
        and abs(coordinate.y - site_y) <= _SITE_COORDINATE_TOLERANCE_MM
      ):
        return site
    raise RuntimeError(
      f"{coordinate} does not match any of the Bravo deck's nine taught sites; the gripper "
      "can only move to or place at one of those sites, not an arbitrary coordinate."
    )

  # -- Head-state guards --

  def _require_identified_head(self, operation: str) -> None:
    """Raise if the installed head has not been identified yet.

    ``head_type == "unknown"`` makes :attr:`~.bravo.Bravo.head_geometry`
    and :attr:`~.bravo.Bravo.head_capacity` report the 96-channel default
    as a permissive fallback (see :func:`~.head_mode.head_geometry_for_type`) --
    exactly right for a caller that just wants *some* answer, but wrong to
    build an operation on top of, since the real installed head might
    physically be the 8-barrel ``8_d_lt`` or any other size.

    Args:
      operation: The operation name, for the error message.
    """
    if self._bravo.head_type == "unknown":
      raise RuntimeError(
        f"Cannot run {operation}: the installed head has not been identified yet "
        "(head_type is 'unknown'). Call Bravo.initialize() to detect it, or set "
        "BravoMachineConfig.head.head_type explicitly."
      )

  def _require_96_channel_head(self, operation: str) -> None:
    """Raise unless the installed head has exactly 96 channels.

    Args:
      operation: The operation name, for the error message.
    """
    self._require_identified_head(operation)
    geometry = self._bravo.head_geometry
    channels = geometry.rows * geometry.columns
    if channels != 96:
      raise RuntimeError(
        f"{operation} requires a 96-channel head; the installed '{self._bravo.head_type}' "
        f"head has {geometry.rows}x{geometry.columns} = {channels} channels. Use the "
        "per-channel path (pick_up_tips/aspirate/dispense/drop_tips) instead."
      )

  def _require_gripper(self, operation: str) -> None:
    """Raise unless the installed hardware has a gripper.

    Args:
      operation: The operation name, for the error message.
    """
    if not self._bravo.has_gripper:
      raise RuntimeError(f"{self._bravo.model_name} has no gripper; {operation} is not available.")

  # -- Head-block dispatch --

  def _require_block_fits(self, block: HeadBlock) -> None:
    """Raise unless *block* fits the installed head's own barrel grid.

    :func:`~.head_mode.normalize_head_mode` silently clamps an oversized
    request to the head's own size rather than raising, so this check has
    to happen before :meth:`~.bravo.Bravo.set_head_mode` is ever called --
    otherwise a block that does not fit would silently become a smaller
    one instead of being rejected.

    Args:
      block: The block to check.

    Raises:
      RuntimeError: If *block* is larger, in either dimension, than the
        installed head.
    """
    geometry = self._bravo.head_geometry
    if not block.fits_within(geometry.rows, geometry.columns):
      raise RuntimeError(
        f"The requested {block.num_rows}x{block.num_columns} block of barrels does not fit "
        f"the installed {geometry.rows}x{geometry.columns} head (head_type="
        f"'{self._bravo.head_type}'). Select at most {geometry.rows} row(s) and "
        f"{geometry.columns} column(s)."
      )

  def _apply_channel_block(self, block: HeadBlock, location: int, *, target: str) -> None:
    """Set the active head mode from *block* and pin the matching anchor.

    Used for an operation that is choosing the active head mode fresh --
    a tip pickup, or a liquid-handling operation. Not used for a tip
    *return*, which must keep the head mode tips were picked up in rather
    than set a new one (see :meth:`_apply_tip_return_anchor`).

    Args:
      block: The block of active barrels.
      location: The deck site to pin the anchor cell at.
      target: ``"tip"`` to set a tipbox anchor, ``"plate"`` for a plate
        anchor.

    Raises:
      RuntimeError: If *block* does not fit the installed head (see
        :meth:`_require_block_fits`), or the resolved anchor is illegal at
        *location* (raised by :attr:`bravo` itself).
    """
    self._require_block_fits(block)
    subset_type, row_count, column_count = _subset_for_block(block, self._bravo.head_geometry)
    if target == "tip":
      self._set_tip_pickup_anchor(block, location, subset_type, row_count, column_count)
    else:
      self._bravo.set_head_mode(subset_type, "back_left", row_count, column_count)
      self._bravo.set_plate_selection(location, block.row_start, block.col_start)

  def _set_tip_pickup_anchor(
    self, block: HeadBlock, location: int, subset_type: str, row_count: int, column_count: int
  ) -> None:
    """Set the head mode and tipbox anchor for a tip *pickup*, trying every head corner.

    A tipbox anchor's legality depends on which corner of the box is
    currently being consumed from (see :func:`~.head_mode.tipbox_mirror_corner`
    and :func:`~.head_mode.is_legal_tipbox_anchor`), which is the *mirror
    image* of the head's own anchor corner -- unlike a plate anchor, which
    :meth:`~.bravo.Bravo` accepts at any reachable cell regardless of head
    corner. Rather than re-deriving that mirroring and the box's current
    occupancy here, this tries each of the four head corners (through
    :attr:`bravo`'s own public API) and keeps the first one under which the
    requested block is legal right now.

    Args:
      block: The block of tip spots to pick up.
      location: The deck site of the tip box.
      subset_type: The head subset type :func:`_subset_for_block` chose.
      row_count: The row count :func:`_subset_for_block` chose.
      column_count: The column count :func:`_subset_for_block` chose.

    Raises:
      RuntimeError: If no head corner makes the block a legal pickup
        anchor given the tip box's current occupancy.
    """
    last_error: Optional[RuntimeError] = None
    for subset_config in ("back_left", "back_right", "front_left", "front_right"):
      self._bravo.set_head_mode(subset_type, subset_config, row_count, column_count)
      try:
        self._bravo.set_tip_selection(location, block.row_start, block.col_start)
        return
      except RuntimeError as exc:
        last_error = exc
    raise RuntimeError(
      f"The requested tip block (rows {block.row_start}-{block.row_stop - 1}, columns "
      f"{block.col_start}-{block.col_stop - 1}) is not accessible for pickup at location "
      f"{location} under any head orientation, given the tip box's current occupancy."
    ) from last_error

  def _apply_tip_return_anchor(self, block: HeadBlock, location: int) -> None:
    """Pin the tipbox anchor for a tip return, without changing the active head mode.

    A return reuses whichever head mode picked the tips up --
    :class:`~.bravo.Bravo` tracks that itself and :meth:`~.bravo.Bravo.tips_off`
    resolves it automatically -- so only which tipbox cells the return
    targets needs pinning here.

    A tip box tracks its own depletion in full row/column bands (see
    :func:`~.head_mode.is_legal_tipbox_anchor`'s ``"return"`` branch): a
    block that spans the box's full width or height (``"row"``,
    ``"column"``, or ``"all_barrels"`` head mode) always returns cleanly,
    but a block that does not (``"single_barrel"`` or a ``"rectangle"``
    narrower than the box) is only accepted once every other tip in the
    row(s)/column(s) it touches has also been removed -- a lone tip
    picked and immediately returned next to still-full neighbours is
    illegal by that same rule, not a mapping defect here. This is caught
    and re-raised with that explanation instead of Bravo's own
    lower-level "not accessible for return" message.

    Args:
      block: The block of tip spots being returned to.
      location: The deck site of the tip box.

    Raises:
      RuntimeError: If *block* does not fit the installed head (see
        :meth:`_require_block_fits`), or the tip box's current occupancy
        does not permit a return of exactly this block's shape and
        position.
    """
    self._require_block_fits(block)
    try:
      self._bravo.set_tip_selection(location, block.row_start, block.col_start)
    except RuntimeError as exc:
      raise RuntimeError(
        f"Cannot return tips to rows {block.row_start}-{block.row_stop - 1}, columns "
        f"{block.col_start}-{block.col_stop - 1} at location {location}: this block does not "
        "span the tip box's full width or height, and a tip box only accepts a partial-width "
        "return once every other tip in the row(s)/column(s) it touches has also been "
        "removed -- returning a single barrel or a narrow rectangle right next to tips still "
        "in the same row/column is rejected the same way picking one would be from the "
        "middle of a still-full box. Return using a head mode that spans the box's full row "
        "or column (or all_barrels), or drop to a trash location instead. "
        f"Bravo's own error: {exc}"
      ) from exc

  def _resolve_channel_group(
    self, resources: Sequence[Resource], *, role: str
  ) -> Tuple[int, HeadBlock]:
    """Resolve the shared deck site and head block for a list of channel targets.

    Args:
      resources: The item (well or tip spot) each channel in one call is
        targeting. Every item must be a child of the same parent resource,
        since the Bravo head only ever addresses one deck site per call.
      role: What kind of item *resources* holds, for error messages (e.g.
        ``"well"``, ``"tip"``).

    Returns:
      The deck site the shared parent occupies, and the head block the
      items' identifiers form.

    Raises:
      RuntimeError: If *resources* is empty, its items do not share a
        single parent, that parent is not assigned to a Bravo deck site,
        or (from :func:`~.block.head_block_for_identifiers`) the items do
        not form a contiguous rectangular block.
    """
    if not resources:
      raise RuntimeError(f"At least one {role} is required.")
    parent = resources[0].parent
    if parent is None or any(r.parent is not parent for r in resources):
      names = ", ".join(sorted({r.name for r in resources}))
      raise RuntimeError(
        f"All channels in a single Bravo operation must target {role}s on the same labware; "
        f"got {role}s that are not all children of one resource ({names})."
      )
    itemized_parent = _require_itemized(parent, role="labware")
    location = self._resolve_site(itemized_parent, role="labware")
    identifiers = [itemized_parent.get_child_identifier(resource) for resource in resources]
    block = head_block_for_identifiers(identifiers)
    return location, block

  # -- Per-channel tips --

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]) -> None:
    """Pick up tips at the channels' target tip spots.

    Derives the head block the target tip spots form, sets the head mode
    and tipbox anchor to match, then picks up.
    """
    self._require_identified_head("pick_up_tips")
    location, block = self._resolve_channel_group([op.resource for op in ops], role="tip")
    self._apply_channel_block(block, location, target="tip")
    await self._bravo.tips_on(location)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]) -> None:
    """Drop tips at the channels' target tip spots, or to a shared trash.

    A tip-spot target derives the head block being returned and pins its
    tipbox anchor, keeping the head mode active tips were picked up in
    (see :meth:`_apply_tip_return_anchor`). A trash target has no item
    grid to derive a block from, so the head mode is left untouched;
    :meth:`~.bravo.Bravo.tips_off` resolves it from what is already
    tracked as mounted.
    """
    self._require_identified_head("drop_tips")
    resources = [op.resource for op in ops]
    if not resources:
      raise RuntimeError("At least one tip is required.")
    first = resources[0]
    if isinstance(first, TipSpot):
      if any(not isinstance(r, TipSpot) for r in resources):
        raise RuntimeError(
          "All drop_tips channels in one call must target the same kind of resource "
          "(tip spots or a shared trash), not a mix of both."
        )
      location, block = self._resolve_channel_group(resources, role="tip")
      self._apply_tip_return_anchor(block, location)
    else:
      if any(r is not first for r in resources):
        names = ", ".join(sorted({r.name for r in resources}))
        raise RuntimeError(
          f"All drop_tips channels dropping to trash in one call must target the same trash "
          f"resource; got {names}."
        )
      location = self._resolve_site(first, role="tip trash")
    await self._bravo.tips_off(location)

  # -- Per-channel liquid handling --

  @staticmethod
  def _require_no_mix(ops: Sequence[Union[SingleChannelAspiration, SingleChannelDispense]]) -> None:
    """Raise if any op carries an embedded mix step; unsupported by this backend."""
    if any(op.mix is not None for op in ops):
      raise RuntimeError(
        "AgilentBravoBackend does not support a mix step embedded in aspirate/dispense; "
        "call Bravo.mix directly instead."
      )

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]) -> None:
    """Aspirate at the channels' target wells.

    Volume and flow rate must be identical across every op, since the head
    has one plunger drive; ``liquid_height`` (mapped to
    :meth:`~.bravo.Bravo.aspirate`'s ``distance_from_bottom``) and
    ``blow_out_air_volume`` (mapped to its ``post_aspirate``) must be too,
    for the same reason -- there is only one Z position and one air-gap
    volume per call.
    """
    self._require_identified_head("aspirate")
    self._require_no_mix(ops)
    volume = _uniform_value([op.volume for op in ops], label="aspirate volume")
    flow_rate = _uniform_optional([op.flow_rate for op in ops], label="flow rate")
    liquid_height = _uniform_optional([op.liquid_height for op in ops], label="liquid height")
    blow_out = _uniform_optional(
      [op.blow_out_air_volume for op in ops], label="blow-out air volume"
    )
    location, block = self._resolve_channel_group([op.resource for op in ops], role="well")
    self._apply_channel_block(block, location, target="plate")
    liquid_class = {"aspirate": {"w_velocity_ul_s": flow_rate}} if flow_rate is not None else None
    await self._bravo.aspirate(
      location,
      volume,
      distance_from_bottom=liquid_height if liquid_height is not None else 1.0,
      post_aspirate=blow_out or 0.0,
      liquid_class=liquid_class,
    )

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]) -> None:
    """Dispense at the channels' target wells.

    See :meth:`aspirate` for why volume, flow rate, ``liquid_height``, and
    ``blow_out_air_volume`` must each be uniform across every op.
    ``blow_out_air_volume`` maps to :meth:`~.bravo.Bravo.dispense`'s
    ``blowout``.
    """
    self._require_identified_head("dispense")
    self._require_no_mix(ops)
    volume = _uniform_value([op.volume for op in ops], label="dispense volume")
    flow_rate = _uniform_optional([op.flow_rate for op in ops], label="flow rate")
    liquid_height = _uniform_optional([op.liquid_height for op in ops], label="liquid height")
    blow_out = _uniform_optional(
      [op.blow_out_air_volume for op in ops], label="blow-out air volume"
    )
    location, block = self._resolve_channel_group([op.resource for op in ops], role="well")
    self._apply_channel_block(block, location, target="plate")
    liquid_class = {"dispense": {"w_velocity_ul_s": flow_rate}} if flow_rate is not None else None
    await self._bravo.dispense(
      location,
      volume,
      distance_from_bottom=liquid_height if liquid_height is not None else 1.0,
      blowout=blow_out or 0.0,
      liquid_class=liquid_class,
    )

  # -- 96-head tips --

  async def pick_up_tips96(self, pickup: PickupTipRack) -> None:
    """Pick up tips with the 96 head.

    ``pickup.tips`` carries one entry per rack position, ``None`` where a
    position has no tip; a fully-populated rack reduces to the whole head,
    a partial one to the block those populated positions form (see
    :func:`~.block.head_block_for_identifiers`).
    """
    self._require_96_channel_head("pick_up_tips96")
    rack = pickup.resource
    location = self._resolve_site(rack, role="tip rack")
    items = rack.get_all_items()
    identifiers = [
      rack.get_child_identifier(item) for item, tip in zip(items, pickup.tips) if tip is not None
    ]
    if not identifiers:
      raise RuntimeError("pick_up_tips96 requires at least one populated tip rack position.")
    block = head_block_for_identifiers(identifiers)
    self._apply_channel_block(block, location, target="tip")
    await self._bravo.tips_on(location)

  async def drop_tips96(self, drop: DropTipRack) -> None:
    """Drop tips with the 96 head, to a tip rack or to trash.

    No per-position information is available at this call (unlike
    :meth:`pick_up_tips96`), so the head mode is left untouched;
    :meth:`~.bravo.Bravo.tips_off` resolves it from what is already
    tracked as mounted.
    """
    self._require_96_channel_head("drop_tips96")
    resource = drop.resource
    role = "tip rack" if isinstance(resource, TipRack) else "tip trash"
    location = self._resolve_site(resource, role=role)
    await self._bravo.tips_off(location)

  # -- 96-head liquid handling --

  def _resolve_96_location(
    self,
    op: Union[
      MultiHeadAspirationPlate,
      MultiHeadDispensePlate,
      MultiHeadAspirationContainer,
      MultiHeadDispenseContainer,
    ],
  ) -> int:
    """Return the deck site a 96-head operation targets."""
    if isinstance(op, (MultiHeadAspirationPlate, MultiHeadDispensePlate)):
      if not op.wells:
        raise RuntimeError("A 96-head plate operation requires at least one well.")
      parent = op.wells[0].parent
      if parent is None or any(w.parent is not parent for w in op.wells):
        raise RuntimeError("All wells in a 96-head operation must belong to the same plate.")
      return self._resolve_site(parent, role="plate")
    return self._resolve_site(op.container, role="container")

  def _set_96_plate_anchor(
    self, location: int, op: Union[MultiHeadAspirationPlate, MultiHeadDispensePlate]
  ) -> None:
    """Pin the plate anchor for a 96-head plate operation to its wells' own minimum cell.

    The whole head is always active for a 96-head operation (there is no
    partial-channel concept here, unlike :meth:`pick_up_tips96`); what
    varies is which plate cell aligns with the head's own reference
    barrel, which is the smallest (row, col) among the targeted wells --
    correct even when the wells are a strided subset of a higher-density
    plate (e.g. a 96-head striping a 384-well plate), since
    :class:`~.bravo.Bravo` itself resolves the barrel-to-plate pitch ratio
    from the anchor cell and the installed head/plate geometry.
    """
    plate = _require_itemized(op.wells[0].parent, role="plate")
    positions = [parse_item_identifier(plate.get_child_identifier(well)) for well in op.wells]
    anchor_row = min(row for row, _ in positions)
    anchor_col = min(col for _, col in positions)
    self._bravo.set_plate_selection(location, anchor_row, anchor_col)

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ) -> None:
    """Aspirate a single scalar volume with the whole 96 head.

    Matches the head's single plunger drive exactly: there is one volume,
    one flow rate, and one Z position for the whole head, so no
    uniformity check is needed here the way it is for the per-channel path.
    """
    self._require_96_channel_head("aspirate96")
    if aspiration.mix is not None:
      raise RuntimeError(
        "AgilentBravoBackend does not support a mix step embedded in aspirate96; call "
        "Bravo.mix directly instead."
      )
    location = self._resolve_96_location(aspiration)
    self._bravo.set_head_mode("all_barrels", "back_left")
    if isinstance(aspiration, MultiHeadAspirationPlate):
      self._set_96_plate_anchor(location, aspiration)
    flow_rate = aspiration.flow_rate
    liquid_class = {"aspirate": {"w_velocity_ul_s": flow_rate}} if flow_rate is not None else None
    height = aspiration.liquid_height
    await self._bravo.aspirate(
      location,
      aspiration.volume,
      distance_from_bottom=height if height is not None else 1.0,
      post_aspirate=aspiration.blow_out_air_volume or 0.0,
      liquid_class=liquid_class,
    )

  async def dispense96(
    self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]
  ) -> None:
    """Dispense a single scalar volume with the whole 96 head.

    See :meth:`aspirate96`.
    """
    self._require_96_channel_head("dispense96")
    if dispense.mix is not None:
      raise RuntimeError(
        "AgilentBravoBackend does not support a mix step embedded in dispense96; call "
        "Bravo.mix directly instead."
      )
    location = self._resolve_96_location(dispense)
    self._bravo.set_head_mode("all_barrels", "back_left")
    if isinstance(dispense, MultiHeadDispensePlate):
      self._set_96_plate_anchor(location, dispense)
    flow_rate = dispense.flow_rate
    liquid_class = {"dispense": {"w_velocity_ul_s": flow_rate}} if flow_rate is not None else None
    await self._bravo.dispense(
      location,
      dispense.volume,
      distance_from_bottom=dispense.liquid_height if dispense.liquid_height is not None else 1.0,
      blowout=dispense.blow_out_air_volume or 0.0,
      liquid_class=liquid_class,
    )

  # -- Gripper --

  def _require_zero_offset(self, offset: Coordinate, *, operation: str) -> None:
    """Raise unless *offset* is ``Coordinate.zero()``.

    The Bravo gripper has a single, fixed approach position per taught
    site -- there is no axis to apply an arbitrary XYZ offset onto.
    Silently ignoring a non-zero offset would place the resource
    somewhere other than where the caller asked, with nothing telling
    them that happened; rejecting is safer than guessing.

    Args:
      offset: The offset to check.
      operation: The operation name, for the error message.
    """
    if offset != Coordinate.zero():
      raise RuntimeError(
        f"{operation}: offset={offset} is not supported -- the Bravo gripper has a single, "
        "fixed approach position per deck site and cannot apply an arbitrary offset. Pass "
        "Coordinate.zero(), or omit it."
      )

  def _require_front_direction(
    self, direction: GripDirection, *, field: str, operation: str
  ) -> None:
    """Raise unless *direction* is ``GripDirection.FRONT``.

    The Bravo gripper approaches every site from a single fixed side; it
    cannot rotate to grip (or place) from the back, left, or right. See
    :meth:`_require_zero_offset` for why this rejects rather than ignores.

    Args:
      direction: The direction to check.
      field: Which field on the op this came from, for the error message.
      operation: The operation name, for the error message.
    """
    if direction != GripDirection.FRONT:
      raise RuntimeError(
        f"{operation}: {field}={direction.name} is not supported -- the Bravo gripper always "
        "approaches from a single fixed direction (GripDirection.FRONT) and cannot grip or "
        "place from another side."
      )

  def _require_zero_rotation(self, rotation: float, *, operation: str) -> None:
    """Raise unless *rotation* is ``0.0``.

    The Bravo gripper cannot rotate a resource while carrying it. See
    :meth:`_require_zero_offset` for why this rejects rather than ignores.

    Args:
      rotation: The rotation, in degrees, to check.
      operation: The operation name, for the error message.
    """
    if rotation != 0.0:
      raise RuntimeError(
        f"{operation}: rotation={rotation} is not supported -- the Bravo gripper cannot "
        "rotate a resource while carrying it."
      )

  async def pick_up_resource(self, pickup: ResourcePickup) -> None:
    """Pick up a resource with the gripper.

    ``pickup.pickup_distance_from_top`` is not representable in the
    Bravo's fixed gripper geometry (a single grip depth per taught site)
    and is ignored. ``pickup.offset`` and ``pickup.direction`` are
    rejected outright when not at their default instead: silently
    ignoring a caller's explicit offset or grip direction could place or
    grip the resource wrong without any indication that happened (see
    :meth:`_require_zero_offset`/:meth:`_require_front_direction`).

    Raises:
      RuntimeError: If the gripper is unavailable (see
        :meth:`_require_gripper`), *pickup.offset* is not
        ``Coordinate.zero()``, or *pickup.direction* is not
        ``GripDirection.FRONT``.
    """
    self._require_gripper("pick_up_resource")
    self._require_zero_offset(pickup.offset, operation="pick_up_resource")
    self._require_front_direction(pickup.direction, field="direction", operation="pick_up_resource")
    location = self._resolve_site(pickup.resource, role="resource")
    await self._bravo.gripper_pick(location)

  async def move_picked_up_resource(self, move: ResourceMove) -> None:
    """Move a resource the gripper is holding to hover over a deck site.

    ``move.location`` must match one of the Bravo deck's nine taught sites
    (see :meth:`_site_for_coordinate`). ``move.offset`` and
    ``move.gripped_direction`` are rejected when not at their default,
    for the reason given in :meth:`pick_up_resource`.

    Raises:
      RuntimeError: If the gripper is unavailable, *move.offset* is not
        ``Coordinate.zero()``, *move.gripped_direction* is not
        ``GripDirection.FRONT``, or *move.location* does not match a
        taught site.
    """
    self._require_gripper("move_picked_up_resource")
    self._require_zero_offset(move.offset, operation="move_picked_up_resource")
    self._require_front_direction(
      move.gripped_direction, field="gripped_direction", operation="move_picked_up_resource"
    )
    location = self._site_for_coordinate(move.location)
    await self._bravo.gripper_move(location)

  async def drop_resource(self, drop: ResourceDrop) -> None:
    """Place the resource the gripper is holding and release it.

    ``drop.destination`` must match one of the Bravo deck's nine taught
    sites (see :meth:`_site_for_coordinate`). ``drop.pickup_distance_from_top``
    is ignored, for the reason given in :meth:`pick_up_resource`.
    ``drop.offset``, ``drop.pickup_direction``, ``drop.direction``, and
    ``drop.rotation`` are rejected when not at their default, for the
    same reason a non-default offset/direction is rejected there --
    including ``rotation``, since a caller that asked to place a resource
    rotated 90 degrees from how it was picked up would otherwise have it
    placed unrotated with no error at all.

    Raises:
      RuntimeError: If the gripper is unavailable, *drop.offset* is not
        ``Coordinate.zero()``, *drop.pickup_direction* or *drop.direction*
        is not ``GripDirection.FRONT``, *drop.rotation* is not ``0.0``,
        or *drop.destination* does not match a taught site.
    """
    self._require_gripper("drop_resource")
    self._require_zero_offset(drop.offset, operation="drop_resource")
    self._require_front_direction(
      drop.pickup_direction, field="pickup_direction", operation="drop_resource"
    )
    self._require_front_direction(drop.direction, field="direction", operation="drop_resource")
    self._require_zero_rotation(drop.rotation, operation="drop_resource")
    location = self._site_for_coordinate(drop.destination)
    await self._bravo.gripper_place(location)

  # -- Tip compatibility --

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Return whether *tip* is compatible with the installed head.

    Every barrel on a Bravo head is identical, so *channel_idx* does not
    change the answer. Returns ``False`` for an unidentified head, since
    there is then no installed head to check the tip's capacity against.
    """
    del channel_idx
    head_type = self._bravo.head_type
    if head_type == "unknown":
      return False
    return get_tip_definition(head_type, tip.maximal_volume) is not None

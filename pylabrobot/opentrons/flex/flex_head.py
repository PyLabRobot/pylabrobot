"""Head sub-objects for :class:`~pylabrobot.opentrons.flex.flex.Flex`.

Each head is a plain-class sub-object (EL406/Cytation5 idiom), not a
Capability/CapabilityBackend split: it holds a back-reference to the owning
``Flex`` device and issues commands through the shared transport via
``self.flex._execute_command``. Deck-scoped labware loading stays on
``Flex`` (heads call ``self.flex._ensure_labware_loaded(...)``); only
which physical channel holds which tip is genuine head-local state
(``self._channel_tips``).

This module holds the ``_FlexHead`` base plus ``FlexHead1`` (single-channel,
well-addressed), ``FlexHead8`` (column-addressed, anchor-well fan-out) and
``FlexHead96`` (96 fixed nozzles, whole-plate-addressed). The transactional
stage->wire->verify->commit/rollback flow and hardware tip-presence
verification are factored onto the ``_FlexHead`` base
(``_execute_pickup``/``_execute_liquid_op``/``_execute_draw``/
``_execute_trash_drop``) so ``FlexHead1`` and ``FlexHead96`` reuse the exact
machinery ``FlexHead8`` established -- only the addressing (single well vs.
column vs. whole-plate anchor) and nozzle layout differ per head.

Plunger priming (``prepareToAspirate``) is the robot's business, not this
driver's: see ``prepare_to_aspirate`` for the whole rule and why nothing here
tracks it.

Successful pickups, tip drops, and well/container-addressed liquid operations
retract vertically to at least ``flex.traversal_height`` before returning.
In-place operations and explicit positioning leave the head where requested.
"""

import logging
import math
import warnings
from typing import (
  TYPE_CHECKING,
  Any,
  Dict,
  List,
  Optional,
  Sequence,
  Tuple,
  Union,
  cast,
)

from pylabrobot.opentrons.flex.errors import OpentronsCommandError, OpentronsError
from pylabrobot.opentrons.flex.pipette_defaults import FlowRates, flow_rates
from pylabrobot.opentrons.operations import OperationLock, instrument_operation
from pylabrobot.opentrons.tracking import track_liquid_transfer
from pylabrobot.resources import (
  Container,
  Plate,
  TipRack,
  TipSpot,
  Trash,
  VolumeTracker,
  Well,
  does_tip_tracking,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip

if TYPE_CHECKING:
  from pylabrobot.opentrons.flex.flex import Flex

logger = logging.getLogger(__name__)


class _FlexHead:
  """Base class for a mount- (or 96-head-) addressed pipette on an ``Flex``.

  Subclasses implement the liquid-handling ops appropriate to their channel
  count. This base holds the shared plumbing: the back-reference to the
  owning device, per-channel tip telemetry, and the well/labware helpers ops
  need to build robot-server command params.
  """

  def __init__(
    self,
    flex: "Flex",
    mount: str,
    pipette_id: str,
    channels: int,
    pipette_model: str,
    max_volume: float,
  ) -> None:
    self.flex = flex
    self._run = flex._require_run()
    self.mount = mount
    self.pipette_id = pipette_id
    self.channels = channels
    self.pipette_model = pipette_model
    # The pipette's own capacity, not the mounted tip's. Carried here so a caller
    # describing the head does not have to re-read /instruments to get it.
    self.max_volume = max_volume
    self._channel_tips: List[Optional[Tip]] = [None] * channels
    # The labware id the pipette last pipetted over, or None when its position is
    # unknown (start of run, after a jog or a trash drop). Used to arc high only
    # when a pipetting move crosses to a different slot -- see _travel_guard.
    self._current_labware_id: Optional[str] = None

  @property
  def _operation_lock(self) -> OperationLock:
    """Share the device lock across heads, gripper, and lifecycle methods."""
    return self.flex._operation_lock

  def _require_active(self) -> None:
    """Refuse an instrument retained from an earlier control session."""
    if self.flex._require_run() is not self._run:
      raise RuntimeError(
        "This instrument belongs to an earlier Flex run; use the current instrument"
      )

  async def _travel_guard(self, params: Dict[str, Any]) -> None:
    """Arc to a new slot's well at the safe travel plane before pipetting there.

    A pipetting move to a well on a DIFFERENT labware than the pipette last
    worked over crosses deck slots, so it is prefixed with a ``moveToWell`` at
    the computed traversal plane (never below a tip rack, see
    ``checks.traversal_z``) -- the ``aspirate``/``dispense``/``pickUpTip``/
    ``dropTip`` commands cannot carry a ``minimumZHeight`` themselves. A move
    WITHIN the same labware never crosses another slot, so it is left to the
    engine's own low arc. ``minimumZHeight`` is a mid-travel floor only; it does
    not clamp the descent, so the following op still reaches the well.
    """
    labware_id = params.get("labwareId")
    well_name = params.get("wellName")
    if not isinstance(labware_id, str) or not isinstance(well_name, str):
      return
    if labware_id == self._current_labware_id:
      return
    await self.flex._execute_command(
      "moveToWell",
      {
        "pipetteId": self.pipette_id,
        "labwareId": labware_id,
        "wellName": well_name,
        "wellLocation": {"origin": "top", "offset": {"x": 0, "y": 0, "z": 0}},
        "minimumZHeight": self.flex.traversal_height,
      },
    )
    self._current_labware_id = labware_id

  def get_mounted_tips(self) -> List[Optional[Tip]]:
    """Per-channel tip state (Case-2: no private-attribute peeking by consumers).

    Returns a copy — mutating the result never affects head state. This is
    PLR-side bookkeeping only; it is not queried from the robot. The Flex's
    hardware tip-presence sensor (see ``has_tip_on_hardware()``) is the
    aggregate ground truth for whether *a* tip is actually seated on this
    head's pipette — it reports one bool per pipette, not per channel, so it
    cannot replace this per-channel cache, only verify/reconcile against it.
    """
    return list(self._channel_tips)

  def default_flow_rates(self) -> FlowRates:
    """The robot's own defaults for this pipette and the tip currently on it.

    Tip-dependent, so it cannot be resolved before a pickup: the same p1000
    eight-channel defaults to 478 uL/s on a 50 uL tip and 716 on a 200.
    """
    mounted = [tip for tip in self._channel_tips if tip is not None]
    if not mounted:
      raise OpentronsError(
        "NoTipMounted",
        f"'{self.mount}' has no tip, so its default flow rate is undefined. Pick up a "
        "tip first, or pass an explicit flow_rate.",
      )
    return flow_rates(self.pipette_model, mounted[0].maximal_volume)

  @instrument_operation
  async def discard_tips(self, trash: Trash) -> None:
    """Discard all mounted tips into ``trash``. Implemented by each head."""
    raise NotImplementedError

  @instrument_operation
  async def blow_out(self, flow_rate: Optional[float] = None) -> None:
    """Blow out at the current position -- one ``blowOutInPlace`` command.

    Pushes the plunger past its dispense-bottom to expel residual liquid
    from the tip(s) wherever the pipette currently is (no well addressing --
    position explicitly with ``move_to_well`` first). ``flow_rate`` (uL/s) defaults to
    the dispense default. Blowing out leaves the plunger past its dispense
    bottom, so the next draw needs priming: see ``prepare_to_aspirate``. No
    trackers are involved.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.blow_out has not been tested on hardware.", stacklevel=2)
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().blow_out
    await self.flex._execute_command(
      "blowOutInPlace", {"pipetteId": self.pipette_id, "flowRate": rate}
    )

  @instrument_operation
  async def has_tip_on_hardware(self) -> Optional[bool]:
    """Query the Flex's hardware tip-presence sensor for THIS head's pipette.

    The Flex reports tip presence as one reading per pipette (mount), not per
    nozzle/channel. This is the aggregate hardware ground truth, used to
    verify/reconcile the per-channel ``_channel_tips`` bookkeeping -- it
    cannot tell you *which* channel(s) hold a tip.

    Asked for with the ``getTipPresence`` run command rather than the
    ``GET /instruments`` REST read: same bit, but the REST path re-caches the
    attached instruments as a side effect.

    Returns:
      ``True``/``False`` when the sensor reads present/absent, ``None`` when
      it reads unknown or reports no status.
    """
    status = await self._read_tip_presence()
    if status == "present":
      return True
    if status == "absent":
      return False
    return None

  async def _read_tip_presence(self) -> Optional[str]:
    result = await self.flex._execute_command("getTipPresence", {"pipetteId": self.pipette_id})
    return cast(Optional[str], result.get("result", {}).get("status"))

  async def _verify_tips_seated(self) -> None:
    """Raise if the hardware tip-presence sensor reports no tip after a pickup.

    Called immediately after a ``pickUpTip`` wire command succeeds. A
    ``False`` reading means the pipette moved through the pickup motion but
    the sensor did not detect a seated tip (e.g. an empty/damaged tip spot);
    ``None`` (unknown/no pipette found) is not treated as a failure.
    """
    if await self.has_tip_on_hardware() is False:
      raise OpentronsError(
        "Tip pickup not detected",
        f"Hardware tip-presence sensor reports no tip seated on mount {self.mount!r} "
        "after pickUpTip.",
      )

  async def _confirm_tips_cleared(self) -> None:
    """Warn if the hardware tip-presence sensor still reports a tip after a drop.

    Called after a drop wire command + tracker commit. A ``True`` reading
    means the drop motion completed but the sensor still detects a tip
    (e.g. stuck to the nozzle) -- logged as a warning rather than raised,
    since the tracker-side bookkeeping has already been committed by the
    time this runs.
    """
    if await self.has_tip_on_hardware() is True:
      logger.warning(
        "Tip drop may not have cleared: hardware tip-presence sensor still reports a "
        "tip seated on mount %r after drop.",
        self.mount,
      )

  # --- Shared transactional command flows ---
  #
  # These four helpers are the machinery every op (Head1/Head8/Head96 alike)
  # threads through: stage trackers (commit=False) BEFORE any of these run,
  # then the helper sends the wire command(s) and commits/rolls back the
  # staged trackers depending on outcome. Only the ADDRESSING (which well(s),
  # which labware) and nozzle-layout handling differ per head/op.

  async def _execute_pickup(
    self,
    command_type: str,
    params: Dict[str, Any],
    staged_trackers: List[Any],
  ) -> None:
    """wire -> verify (hardware tip-presence) -> commit/rollback.

    Shared by every ``pick_up_tips``/``pick_up_single_tip`` variant. Tip
    trackers must already be staged (``commit=False``) in ``staged_trackers``
    before calling this. Rolls back and re-raises if the wire command itself
    fails, or if it succeeds but ``_verify_tips_seated()`` reports no tip
    seated; commits only once both the wire command and hardware
    verification succeed. Callers are responsible for updating
    ``_channel_tips`` AFTER this returns successfully.
    """
    try:
      await self._travel_guard(params)
      await self.flex._execute_command(command_type, params)
    except BaseException:
      for tracker in staged_trackers:
        tracker.rollback()
      raise

    try:
      await self._verify_tips_seated()
    except BaseException:
      for tracker in staged_trackers:
        tracker.rollback()
      raise

    for tracker in staged_trackers:
      tracker.commit()

  async def _execute_liquid_op(
    self,
    command_type: str,
    params: Dict[str, Any],
    staged_trackers: List[Any],
  ) -> None:
    """wire -> commit/rollback (no hardware verification step).

    Used by rack-return tip drops. Trackers must already be staged
    (``commit=False``) before
    calling this.
    """
    try:
      await self._travel_guard(params)
      await self.flex._execute_command(command_type, params)
    except BaseException:
      for tracker in staged_trackers:
        tracker.rollback()
      raise
    else:
      for tracker in staged_trackers:
        tracker.commit()

  async def _execute_draw(self, command_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """``_execute`` for a draw, restating the robot's "plunger not primed" refusal.

    The robot refuses a draw that names no well while the plunger sits past
    its dispense bottom: fixing that means moving the plunger, and only the
    caller knows whether the tip is in liquid. The robot's own message offers
    one remedy (aspirate from a well instead); this adds the other one, which
    is to lift clear and prime by hand.
    """
    try:
      return await self.flex._execute_command(command_type, params)
    except OpentronsCommandError as e:
      if e.error_type != _NOT_READY_TO_ASPIRATE:
        raise
      raise OpentronsError("NotReadyToAspirateError", _NOT_PRIMED_REMEDY) from e

  @instrument_operation
  async def prepare_to_aspirate(self) -> None:
    """Move the plunger to where an aspirate starts from ("priming"). Tip OUT of the liquid.

    The plunger sits past its dispense bottom after a dispense that emptied
    the tip, a blow out, or a volume-mode change, and the robot will not draw
    from there. Priming lifts it back, which with the tip submerged draws
    that much of the well in, unmeasured: about 4 uL on a p50 in its normal
    mode, 12 uL in low-volume mode, 80 uL on a p1000. Move the tip above the
    liquid first (``move_to_well`` with a "top" origin) and prime there.

    Usually you do not need this at all. ``aspirate`` primes
    at traversal height in open air, then descends and draws. Only the
    in-place draws need it, because they name no well and so the robot cannot
    pick a safe height to prime at. Sending it when the plunger is already in
    place does nothing, so it is safe to send defensively.

    Nothing here tracks whether a prime is pending. The robot owns that flag
    and answers with it on every draw; a copy in this process would go stale
    the first time anything moved the plunger without going through this
    driver.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.prepare_to_aspirate has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    await self.flex._execute_command("prepareToAspirate", {"pipetteId": self.pipette_id})

  def _trash_addressable_area(self, trash: Trash) -> str:
    """The movable-trash addressable area for the slot this trash sits in."""
    slot = self.flex.deck.get_slot(trash)
    if slot not in _MOVABLE_TRASH_SLOTS:
      raise OpentronsError(
        "Trash is not in a trash slot",
        f"'{trash.name}' is in slot {slot!r}. A Flex accepts a movable trash only in "
        f"{', '.join(sorted(_MOVABLE_TRASH_SLOTS))}.",
      )
    return f"movableTrash{slot}"

  async def _execute_trash_drop(self, trash: Trash) -> None:
    """Send the two-command addressable-area trash-drop sequence.

    Shared by every ``discard_tips``/``drop_single_tip`` variant. No tracker
    involvement (trash has none); callers update ``_channel_tips`` and call
    ``_confirm_tips_cleared()`` themselves after this returns.

    ``minimumZHeight`` is set to the computed traversal plane so the travel to
    the trash arcs over every labware on the deck. Without it the engine picks
    its own arc height from only the labware it has been told is loaded, which
    can travel too low and clip a rack the robot was never told about.
    """
    await self.flex._execute_command(
      "moveToAddressableAreaForDropTip",
      {
        "pipetteId": self.pipette_id,
        "addressableAreaName": self._trash_addressable_area(trash),
        "alternateDropLocation": True,
        "minimumZHeight": self.flex.traversal_height,
      },
    )
    await self.flex._execute_command("dropTipInPlace", {"pipetteId": self.pipette_id})
    # Now over the trash, not a slot's labware: the next pipetting move arcs high.
    self._current_labware_id = None

  # --- Fine-pipetting shared helpers ---

  def _mounted_count(self) -> int:
    """How many channels currently hold a tip."""
    return sum(1 for tip in self._channel_tips if tip is not None)

  def _require_mounted_tip(self) -> None:
    """Raise if no channel holds a tip -- pre-wire guard for tip-motion ops.

    Without a tip the same command drives the bare NOZZLE into the labware,
    about a tip length lower than the pose it describes. The engine does
    reject a tipless op, but ``dispense`` moves to the well before it checks,
    so the crash lands first.
    """
    if all(tip is None for tip in self._channel_tips):
      raise OpentronsError(
        "NoTipError",
        "No tip mounted; pick up a tip first.",
      )

  async def _well_target(self, target: Union[Well, TipSpot, Container]) -> Tuple[str, str]:
    """Labware id and well name for a well or tip spot, or a container's sole well."""
    if isinstance(target, (Well, TipSpot)):
      parent = self._require_itemized_parent(target)
      loaded = await self.flex._ensure_labware_loaded(parent)
      return loaded, parent.get_child_identifier(target)
    return await self.flex._ensure_labware_loaded(target), _CONTAINER_WELL_NAME

  def _check_pipetting_clearance(self, target: Container, position: Coordinate) -> None:
    """Validate head-specific clearance before any pipetting motion."""

  async def _pipette(
    self,
    verb: str,
    target: Container,
    volume: float,
    flow_rate: Optional[float],
    offset: Optional[Coordinate],
    liquid_height: Optional[float],
    container_trackers: List[VolumeTracker],
  ) -> None:
    """Position from PLR geometry, pipette in place, then retract vertically.

    Heights are measured from the cavity floor. Bare containers center the
    complete nozzle array; wells locate the active primary nozzle. Stage
    volumes before motion and commit once the plunger command succeeds.
    """
    position = target.get_absolute_location(x="c", y="c", z="cavity_bottom")
    if not isinstance(target, Well):
      if self.channels == 8:
        position.y += _EIGHT_CHANNEL_Y_SPAN / 2
      elif self.channels == 96:
        position.x -= _NINETY_SIX_HEAD_X_SPAN / 2
        position.y += _NINETY_SIX_HEAD_Y_SPAN / 2
    height = _DEFAULT_WELL_BOTTOM_CLEARANCE if liquid_height is None else liquid_height
    position += Coordinate(z=height) + (offset or Coordinate.zero())
    if not all(math.isfinite(v) for v in (position.x, position.y, position.z)):
      raise ValueError("Pipetting coordinates must be finite")
    self._check_pipetting_clearance(target, position)
    clearance = max(self.flex.traversal_height, position.z)
    tip_trackers = [tip.tracker for tip in self._channel_tips if tip is not None]
    sources, destinations = (
      (container_trackers, tip_trackers)
      if verb == "aspirate"
      else (tip_trackers, container_trackers)
    )
    with track_liquid_transfer(sources, destinations, volume):
      await self.move_to(x=position.x, y=position.y, z=clearance, minimum_z_height=clearance)
      if verb == "aspirate":
        await self.prepare_to_aspirate()
      current = await self.position()
      await self.move_relative("z", position.z - current.z)
      if verb == "aspirate":
        await self.aspirate_in_place(volume, flow_rate=flow_rate)
      else:
        await self.dispense_in_place(volume, flow_rate=flow_rate)
    await self._retract_to_traversal_height()

  async def _configure_nozzle_layout(self, configuration_params: Dict[str, Any]) -> None:
    """Send ``configureNozzleLayout``, refusing while any channel holds a tip.

    The engine rejects EVERY nozzle reconfiguration, "ALL" included, while a
    tip is attached ("Cannot configure nozzle layout ... while it has tips
    attached"), because the layout decides which physical nozzles the pipette
    drives. Refused here instead, so the caller reads which tips are in the
    way rather than a mid-op command failure from the robot.
    """
    if any(tip is not None for tip in self._channel_tips):
      held = [i for i, tip in enumerate(self._channel_tips) if tip is not None]
      raise OpentronsError(
        "HasTipError",
        f"The nozzle layout cannot change while channel(s) {held} hold a tip; the robot "
        "refuses the reconfiguration. Drop the mounted tip(s) first.",
      )
    await self.flex._execute_command(
      "configureNozzleLayout",
      {"pipetteId": self.pipette_id, "configurationParams": configuration_params},
    )

  def _touch_tip_params(
    self,
    labware_id: str,
    well_name: str,
    radius: float,
    offset: Optional[Coordinate],
  ) -> Dict[str, Any]:
    """Build the ``touchTip`` params dict shared by every head's ``touch_tip``.

    The ``wellLocation`` is TOP-relative and the default touches 1 mm below
    the rim. A caller ``offset`` REPLACES it rather than shifting it, unlike
    the liquid position ``_well_location`` builds: this z IS the touch height
    (the Opentrons Python API's absolute ``v_offset``), and dropping the
    default here moves the tip UP toward the rim, away from the labware.
    """
    o = offset if offset is not None else Coordinate(z=_DEFAULT_TOUCH_TIP_Z_OFFSET)
    return {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "wellLocation": {"origin": "top", "offset": {"x": o.x, "y": o.y, "z": o.z}},
      "radius": radius,
    }

  async def _probe_z(self, command_type: str, labware_id: str, well_name: str) -> Optional[float]:
    """Send a ``liquidProbe``/``tryLiquidProbe`` command; return the found liquid z (mm).

    The probe starts just above the well rim (origin "top", +2 mm -- the
    engine's own ``LIQUID_PROBE_START_OFFSET_FROM_WELL_TOP``) and descends;
    the engine reads the offset into the stroke length, so a bottom-origin
    start would drive the tip from the well floor through the plate. The
    robot-server OMITS ``z_position`` from a successful command result
    entirely (rather than reporting null) when no liquid is detected, so
    absence is read with ``.get()`` and surfaced as ``None``.

    A probe pushes the plunger, so it is refused on an unprimed one exactly
    like an in-place draw, even though it does name a well: the robot
    deliberately does not prime for it, because reaching this state means the
    tip has held liquid and a probe wants a dry one.
    """
    result = await self._execute_draw(
      command_type,
      {
        "pipetteId": self.pipette_id,
        "labwareId": labware_id,
        "wellName": well_name,
        "wellLocation": {
          "origin": "top",
          "offset": {"x": 0, "y": 0, "z": _LIQUID_PROBE_START_OFFSET_Z},
        },
      },
    )
    return cast(Optional[float], result.get("result", {}).get("z_position"))

  async def _liquid_probe_z(self, labware_id: str, well_name: str, where: str) -> float:
    """``liquidProbe`` with both no-liquid signals mapped to ``LiquidNotFoundError``.

    On real hardware a no-liquid probe FAILS the command with the defined
    "liquidNotFound" error, so the wire failure is caught and translated;
    a successful response without ``z_position`` also means no liquid was
    found. Other command failures propagate unchanged.
    """
    try:
      z = await self._probe_z("liquidProbe", labware_id, well_name)
    except OpentronsCommandError as e:
      if e.error_type == "liquidNotFound":
        raise OpentronsError(
          "LiquidNotFoundError", f"liquid_probe found no liquid in {where}."
        ) from e
      raise
    if z is None:
      raise OpentronsError("LiquidNotFoundError", f"liquid_probe found no liquid in {where}.")
    return z

  @staticmethod
  def _require_itemized_parent(item: Resource) -> ItemizedResource:
    """Return ``item.parent``, asserted to be an addressable-by-name container."""
    parent = item.parent
    assert isinstance(parent, ItemizedResource), (
      f"'{item.name}' has no itemized parent resource (rack/plate)."
    )
    return parent

  @staticmethod
  def _well_location(
    offsets: Optional[List[Optional[Coordinate]]],
    liquid_height: Optional[List[Optional[float]]],
    origin: str = "bottom",
  ) -> Optional[dict]:
    """Build the Flex ``wellLocation`` param from an offset and/or liquid height.

    The default liquid position is ``_DEFAULT_WELL_BOTTOM_CLEARANCE`` above
    the well bottom, or ``liquid_height`` above it when one is given. A
    caller ``offset`` SHIFTS the head from that position rather than
    replacing it: a ``Coordinate`` carries a z of 0 when the caller only
    meant to nudge x/y, so a replacing offset would silently drop the
    clearance and drive the tip onto the well floor.

    ``origin`` defaults to ``"bottom"`` (aspirate/dispense); tip-pickup
    callers must pass ``origin="top"`` -- a tip-rack well's "bottom" is deep
    inside the tip, not the pickup engagement point -- and the "top" origin
    carries no clearance of its own, so there the offset is the whole
    position. Returns ``None`` on a non-bottom origin when neither offset nor
    liquid height is given.
    """
    o = offsets[0] if offsets is not None else None
    height = liquid_height[0] if liquid_height is not None else None
    # A bottom origin always sends a position: an omitted wellLocation makes
    # the Protocol Engine fall back to the rim, above the liquid.
    if o is None and height is None and origin != "bottom":
      return None
    if height is not None:
      base_z = height
    else:
      base_z = _DEFAULT_WELL_BOTTOM_CLEARANCE if origin == "bottom" else 0.0
    x, y, z = (o.x, o.y, o.z) if o is not None else (0.0, 0.0, 0.0)
    return {"origin": origin, "offset": {"x": x, "y": y, "z": base_z + z}}

  # --- Single-cavity container (trough/reservoir) shared helpers ---

  def _container_trackers(self, container: Container) -> List[VolumeTracker]:
    """Repeat a shared container tracker once per mounted nozzle."""
    return [container.tracker for tip in self._channel_tips if tip is not None]

  def _well_trackers(self, wells: List[Well]) -> List[VolumeTracker]:
    """Select well trackers in the same order as their mounted tips."""
    if len(wells) > self.channels:
      raise ValueError("Too many wells for the pipette")
    if any(tip is not None for tip in self._channel_tips[len(wells) :]):
      raise ValueError("Every mounted tip must have a target well")
    return [well.tracker for i, well in enumerate(wells) if self._channel_tips[i] is not None]

  @staticmethod
  def _require_span_fits_container(
    container: Container,
    x_span: float,
    y_span: float,
    offset: Optional[Coordinate],
  ) -> None:
    """Raise pre-wire if the nozzle array would overhang the container.

    PLR centers the array on the cavity and the caller's ``offset``
    shifts it, so the shifted span must still fit on each axis. The footprint
    is the deck-frame one the definition carries, not the container's own
    x/y: a rotated container presents its axes to the robot swapped. It is
    the OUTER box, so an array that fits the shell but not the cavity inside
    passes -- PLR carries no cavity x/y to check against.
    """
    o = offset if offset is not None else Coordinate.zero()
    cavity_x = container.get_absolute_size_x()
    cavity_y = container.get_absolute_size_y()
    required_x = x_span + 2 * abs(o.x)
    required_y = y_span + 2 * abs(o.y)
    if required_x > cavity_x or required_y > cavity_y:
      detail = f"The nozzle array spans {x_span} x {y_span} mm"
      if o.x or o.y:
        detail += f" and the offset ({o.x}, {o.y}) shifts it off-center"
      raise OpentronsError(
        "Container too small",
        f"{detail}, which does not fit inside '{container.name}' "
        f"({cavity_x} x {cavity_y} mm as the robot sees it). "
        "Aim it at a container that holds the whole array.",
      )

  # --- Direct head motion (teaching / recovery jog) ---

  async def _retract_to_traversal_height(self) -> None:
    """Raise vertically after a completed operation; never lower or move laterally.

    Read the robot's current tip/nozzle position after its tip state changes.
    Bookkeeping is already committed when called: a failed retraction must
    not undo a successful pickup, drop, or liquid transfer.
    """
    position = await self._run.get_position(self.pipette_id)
    if not all(math.isfinite(v) for v in (position.x, position.y, position.z)):
      raise ValueError("Cannot retract from a non-finite reported position")
    distance = self.flex.traversal_height - position.z
    if distance > 0:
      await self.flex._execute_command(
        "moveRelative", {"pipetteId": self.pipette_id, "axis": "z", "distance": distance}
      )

  @instrument_operation
  async def position(self) -> Coordinate:
    """The head's current deck-frame position -- one ``savePosition`` query.

    Reports the pipette's critical point: the bottom of the mounted tip, or
    the nozzle when no tip is mounted. The Flex's robot frame coincides with
    the deck frame, so the reported position needs no conversion.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.position has not been tested on hardware.", stacklevel=2)
    return await self._run.get_position(self.pipette_id)

  @instrument_operation
  async def move_to(
    self,
    x: Optional[float] = None,
    y: Optional[float] = None,
    z: Optional[float] = None,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
  ) -> None:
    """Move the head to an absolute deck-frame position, holding any axis left
    unspecified -- ONE ``moveToCoordinates`` command.

    Axes left unspecified are filled from ``position()`` first, so a combined
    move travels a single path instead of an axis-by-axis staircase (the read
    is skipped when all three axes are given). A mounted tip is NOT required:
    jogging is for teaching and recovery, and the target refers to the bottom
    of the mounted tip, or the nozzle when none is mounted.
    ``minimum_z_height`` (mm) defaults to the traversal height, so a lateral
    jog arcs over deck labware; ``speed`` is in mm/s (robot default if None).
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.move_to has not been tested on hardware.", stacklevel=2)
    if x is None and y is None and z is None:
      raise ValueError("move_to: supply at least one of x, y, z.")
    if x is None or y is None or z is None:
      current = await self.position()
      x = current.x if x is None else x
      y = current.y if y is None else y
      z = current.z if z is None else z
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "coordinates": {"x": x, "y": y, "z": z},
      "minimumZHeight": (
        minimum_z_height if minimum_z_height is not None else self.flex.traversal_height
      ),
    }
    if speed is not None:
      params["speed"] = speed
    await self.flex._execute_command("moveToCoordinates", params)
    # A raw jog leaves the pipette at an arbitrary point: the next pipetting move
    # can no longer assume it is over its last labware, so make it arc high.
    self._current_labware_id = None

  @instrument_operation
  async def move_to_well(
    self,
    target: Union[Well, TipSpot, Container],
    offset: Optional[Coordinate] = None,
    origin: str = "top",
    minimum_z_height: Optional[float] = None,
    speed: Optional[float] = None,
  ) -> None:
    """Move to a well, named rather than measured -- ONE ``moveToWell`` command.

    Prefer this over :meth:`move_to` for anything positioned relative to
    labware: naming the well lets the robot work out where that is and refuse
    a move it cannot make, where ``move_to`` sends raw coordinates nothing
    bounds-checks. ``origin`` is where the offset is measured from ("top",
    "bottom", "center", or "meniscus", the last needing a probed liquid
    level), so 10 mm above the well is ``origin="top"`` with
    ``offset=Coordinate(z=10)``. A tip spot is a valid target. No mounted tip
    is required: the target is the tip bottom when one is mounted, the nozzle
    when none is.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.move_to_well has not been tested on hardware.", stacklevel=2)
    if origin not in _WELL_ORIGINS:
      raise ValueError(f"origin must be one of {sorted(_WELL_ORIGINS)}, got {origin!r}")
    labware_id, well_name = await self._well_target(target)

    o = offset or Coordinate(0, 0, 0)
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "wellLocation": {"origin": origin, "offset": {"x": o.x, "y": o.y, "z": o.z}},
      "minimumZHeight": (
        minimum_z_height if minimum_z_height is not None else self.flex.traversal_height
      ),
    }
    if speed is not None:
      params["speed"] = speed
    await self.flex._execute_command("moveToWell", params)

  @instrument_operation
  async def move_relative(self, axis: str, distance: float) -> None:
    """Jog one axis by ``distance`` mm from wherever the head is now.

    ``axis`` is "x", "y" or "z". A negative distance moves the other way.
    Relative to the head's current position, so unlike :meth:`move_to` it
    needs no reading first.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.move_relative has not been tested on hardware.", stacklevel=2)
    if axis not in _MOVE_AXES:
      raise ValueError(f"axis must be one of {sorted(_MOVE_AXES)}, got {axis!r}")
    await self.flex._execute_command(
      "moveRelative",
      {"pipetteId": self.pipette_id, "axis": axis, "distance": distance},
    )

  @instrument_operation
  async def move_to_addressable_area(
    self,
    addressable_area_name: str,
    offset: Optional[Coordinate] = None,
    minimum_z_height: Optional[float] = None,
    speed: Optional[float] = None,
    stay_at_max_height: bool = False,
  ) -> None:
    """Move to a named fixture on the deck rather than to labware.

    An addressable area is somewhere the deck itself provides: a trash bin, a
    waste chute, a staging slot. Named, so the robot resolves the position.
    """
    if self.channels == 96:
      warnings.warn(
        "FlexHead96.move_to_addressable_area has not been tested on hardware.", stacklevel=2
      )
    o = offset or Coordinate(0, 0, 0)
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "addressableAreaName": addressable_area_name,
      "offset": {"x": o.x, "y": o.y, "z": o.z},
      "stayAtHighestPossibleZ": stay_at_max_height,
      "minimumZHeight": (
        minimum_z_height if minimum_z_height is not None else self.flex.traversal_height
      ),
    }
    if speed is not None:
      params["speed"] = speed
    await self.flex._execute_command("moveToAddressableArea", params)

  # --- In-place pipetting (acts where the head already is) ---

  @instrument_operation
  async def aspirate_in_place(self, volume: float, flow_rate: Optional[float] = None) -> None:
    """Aspirate ``volume`` uL where the head already is -- one ``aspirateInPlace`` command.

    Names no well, so no ``Well``/``Container`` tracker moves with it:
    position the head first (``move_to_well``/``move_to``) and account for the
    liquid yourself. Draw with the tip UNDER the surface and far enough off the
    floor not to seal against it; drawing from above the liquid takes air.
    ``flow_rate`` (uL/s) defaults to the aspirate default.

    Raises ``NotReadyToAspirateError`` when the plunger needs priming, since
    naming no well leaves the robot no safe height to prime at. See
    ``prepare_to_aspirate``.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.aspirate_in_place has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().aspirate
    await self._execute_draw(
      "aspirateInPlace",
      {"pipetteId": self.pipette_id, "volume": volume, "flowRate": rate},
    )

  @instrument_operation
  async def dispense_in_place(
    self,
    volume: float,
    flow_rate: Optional[float] = None,
    push_out: Optional[float] = None,
  ) -> None:
    """Dispense ``volume`` uL where the head already is -- one ``dispenseInPlace`` command.

    Names no well, so no tracker moves with it (see ``aspirate_in_place``).
    ``push_out`` (uL) pushes the plunger past its dispense bottom to clear the
    last drops; left out of the command entirely when None, so the robot
    applies its own default for the mounted tip and volume.

    Put the tip AT THE LIQUID SURFACE first, not above it. Dispensing from
    height splashes, aerosolises, and leaves volume hanging in the tip. "The
    caller owns the position" means the caller owes it a good one, not that
    any position will do.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.dispense_in_place has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().dispense
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "volume": volume,
      "flowRate": rate,
    }
    if push_out is not None:
      params["pushOut"] = push_out
    await self.flex._execute_command("dispenseInPlace", params)

  @instrument_operation
  async def air_gap_in_place(self, volume: float, flow_rate: Optional[float] = None) -> None:
    """Draw a ``volume`` uL air gap where the head already is -- one ``airGapInPlace`` command.

    The same plunger motion as ``aspirate_in_place``, but the robot books the
    volume as air, so park the tip above the liquid first. ``flow_rate``
    (uL/s) defaults to the aspirate default. Refuses an unprimed plunger the
    same way ``aspirate_in_place`` does. No tracker is involved.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.air_gap_in_place has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().aspirate
    await self._execute_draw(
      "airGapInPlace",
      {"pipetteId": self.pipette_id, "volume": volume, "flowRate": rate},
    )

  # --- Tip-presence sensor (command form) ---

  @instrument_operation
  async def get_tip_presence(self) -> Optional[str]:
    """Read this head's tip sensor: "present", "absent" or "unknown".

    One reading per pipette, not per channel. ``has_tip_on_hardware()`` is
    the same reading as a bool. ``None`` when the command reports no status.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.get_tip_presence has not been tested on hardware.", stacklevel=2)
    return await self._read_tip_presence()

  @instrument_operation
  async def verify_tip_presence(self, expected_state: bool) -> None:
    """Have the robot fail the command unless its tip sensor reads ``expected_state``.

    ``expected_state=True`` requires a tip; ``False`` requires no tip.
    Where ``get_tip_presence``
    reports and leaves the judgement to the caller, this one raises the
    mismatch from the robot side, so it reads as a checkpoint in a sequence.
    """
    if self.channels == 96:
      warnings.warn("FlexHead96.verify_tip_presence has not been tested on hardware.", stacklevel=2)
    if not isinstance(expected_state, bool):
      raise TypeError("expected_state must be a bool")
    await self.flex._execute_command(
      "verifyTipPresence",
      {"pipetteId": self.pipette_id, "expectedState": "present" if expected_state else "absent"},
    )

  @instrument_operation
  async def configure_for_volume(self, volume: float) -> None:
    """Put the pipette in the volume mode that suits ``volume`` uL.

    A Flex pipette only reaches its stated accuracy at small volumes in its
    low-volume mode, which this picks for the volume given. Call it before
    picking up tips: the robot refuses a mode change while a tip is attached.
    """
    if self.channels == 96:
      warnings.warn(
        "FlexHead96.configure_for_volume has not been tested on hardware.", stacklevel=2
      )
    await self.flex._execute_command(
      "configureForVolume", {"pipetteId": self.pipette_id, "volume": volume}
    )

  # --- Recovery ops ---

  @instrument_operation
  async def unsafe_drop_tip_in_place(self) -> None:
    """Drop the mounted tip where the head is, skipping the engine's own checks.

    The "unsafe/" commands are the recovery path: they still run once the
    engine has put the run into an error state, where the ordinary
    ``dropTipInPlace`` is refused. The tip falls wherever the head happens to
    be, so move somewhere it can be retrieved from first. Clears this head's
    per-channel tip bookkeeping; no tip tracker is touched, since the tip
    goes back to no rack.

    Deliberately does NOT require a tip in this head's own bookkeeping. That
    model is per-process and empty after any restart, which is exactly when a
    tip is stranded on the nozzle and this call is the way off. Guarding on it
    made the escape hatch refuse in the only situation it exists for.
    """
    if self.channels == 96:
      warnings.warn(
        "FlexHead96.unsafe_drop_tip_in_place has not been tested on hardware.", stacklevel=2
      )
    await self.flex._execute_command("unsafe/dropTipInPlace", {"pipetteId": self.pipette_id})
    self._channel_tips = [None] * self.channels

  @instrument_operation
  async def unsafe_blow_out_in_place(self, flow_rate: float) -> None:
    """Blow out where the head is, skipping the engine's own checks.

    The recovery counterpart to ``blow_out`` (see ``unsafe_drop_tip_in_place``
    for what "unsafe/" buys). ``flow_rate`` is in uL/s and has no default
    here, the recovery path being an explicit one. Like
    ``unsafe_drop_tip_in_place`` it does not consult this head's per-process
    tip bookkeeping, which is empty after a restart and would refuse the very
    recovery it exists for. Leaves the plunger past
    its dispense bottom, so the next draw needs priming.
    """
    if self.channels == 96:
      warnings.warn(
        "FlexHead96.unsafe_blow_out_in_place has not been tested on hardware.", stacklevel=2
      )
    await self.flex._execute_command(
      "unsafe/blowOutInPlace",
      {"pipetteId": self.pipette_id, "flowRate": flow_rate},
    )


# Where a wellLocation offset is measured from. "meniscus" needs the robot to
# hold a liquid level for the well, which only a liquid probe gives it.
_WELL_ORIGINS = frozenset({"top", "bottom", "center", "meniscus"})

_MOVE_AXES = frozenset({"x", "y", "z"})

# What the robot calls its refusal to draw with the plunger past its dispense
# bottom. Raised undefined, so the wire carries the exception's class name.
_NOT_READY_TO_ASPIRATE = "PipetteNotReadyToAspirateError"

_NOT_PRIMED_REMEDY = (
  "The plunger sits past its dispense bottom, so the robot will not draw from where it "
  "is. Either aspirate from a well by name, which primes itself at the well top and then "
  "descends, or lift the tip clear of the liquid and call prepare_to_aspirate() before "
  "drawing in place. Priming while submerged draws several uL of the well into the tip."
)

# The 8-channel head's rows front-to-back: channel 0 = "A" (rearmost) .. 7 = "H"
# (frontmost). Used to name the corner nozzles of a partial (QUADRANT) column.
_ROW_LETTERS = "ABCDEFGH"

# The only nozzles an 8-channel Flex can anchor a SINGLE layout on ("A1" is
# the rearmost, "H1" the frontmost), mapped to the channel each one is.
_SINGLE_NOZZLES = {"A1": 0, "H1": 7}

# The anchor is declared, never derived -- only the caller (or the tip tracker)
# knows which spots are still occupied. Default "H1", the FRONT nozzle: its idle
# seven trail toward the REAR, so the natural call (take the rear-most tip, well
# "A1") comes out clean -- they hang off the back edge over nothing, true whatever
# the rack holds -- and H1 reaches front deck slots the rear nozzle cannot (an A1
# anchor cannot reach a rack in deck row D). Recommended single-nozzle deck layout:
# racks in deck rows B and D with rows A and C left empty, so the idle seven always
# trail into an empty rear neighbour (FlexDeck.check_single_nozzle_clearance guards it).
_DEFAULT_SINGLE_NOZZLE = "H1"
_SINGLE_NOZZLE_BY_CHANNEL = {channel: nozzle for nozzle, channel in _SINGLE_NOZZLES.items()}

# Each anchor nozzle's y offset from the pipette mount, and the pipette body's own
# reach back/forward of it. shared-data pipette geometry v2, eight_channel p50 == p1000.
_SINGLE_NOZZLE_Y = {"A1": -16.0, "H1": -79.0}
_PIPETTE_BODY_BACK_Y = 0.0
_PIPETTE_BODY_FRONT_Y = -95.0

# The deck y band that body has to stay inside.
# shared-data robot definition ot3.json: padding front 51.8 / rear -169.42, extents y 493.8.
_ROBOT_FRONT_LIMIT = 51.8
_ROBOT_REAR_LIMIT = 493.8 - 169.42

_NUM_CHANNELS = 8

# The slots a Flex accepts a movable trash in (shared-data ot3_standard.json).
_MOVABLE_TRASH_SLOTS = frozenset({"A1", "B1", "C1", "D1", "A3", "B3", "C3", "D3"})

# Default aspirate/dispense position: 1mm above the well bottom, matching the
# Opentrons Python-API default. The raw Protocol-Engine /commands API defaults
# an OMITTED wellLocation to origin "top" (the well rim -- above the liquid),
# so a plain aspirate would draw air. We therefore always send an explicit
# bottom-referenced wellLocation for liquid ops.
_DEFAULT_WELL_BOTTOM_CLEARANCE = 1.0

# touch_tip's default z: 1 mm below the well rim, matching the Opentrons
# Python API's v_offset default.
_DEFAULT_TOUCH_TIP_Z_OFFSET = -1.0

# Liquid probing starts just above the well rim and descends from there;
# matches the engine's LIQUID_PROBE_START_OFFSET_FROM_WELL_TOP.
_LIQUID_PROBE_START_OFFSET_Z = 2.0

# Opentrons single-cavity labware definitions (troughs/reservoirs) expose
# exactly one well, named "A1" -- container ops always address it.
_CONTAINER_WELL_NAME = "A1"

# The 96-channel nozzle grid is 12 columns x 8 rows at 9 mm pitch: 99 mm
# A1->A12 in x, 63 mm A1->H1 in y. Used to center the head in a container.
_NINETY_SIX_HEAD_X_SPAN = (12 - 1) * 9.0
_NINETY_SIX_HEAD_Y_SPAN = (8 - 1) * 9.0

# The 8-channel head's single nozzle row has the same 7 gaps front-to-back.
_EIGHT_CHANNEL_Y_SPAN = _NINETY_SIX_HEAD_Y_SPAN


class FlexHead1(_FlexHead):
  """Single-channel pipette head, well-addressed.

  Liquid operations use PLR coordinates and in-place commands. Tip operations
  name their tip spot. There is no anchor-well fan-out or nozzle
  layout (there is only ever one physical nozzle). ``_channel_tips`` has
  length 1; the sole channel is index 0.

  Reuses the ``_FlexHead`` base's transactional stage -> wire -> verify ->
  commit/rollback flow and hardware tip-presence verification
  (``_verify_tips_seated``/``_confirm_tips_cleared``) -- the same machinery
  ``FlexHead8`` uses for its column ops, applied to a single well instead of
  a column.

  Verified on a real single-channel Flex (p50, robot-server API 9.1.1):
  motion, tip pickup and drop, liquid probe, in-place pipetting and volume
  mode, all against the hardware tip-presence sensor.
  """

  @instrument_operation
  async def pick_up_tips(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up one tip -- one ``pickUpTip`` command naming ``tip_spot``.

    Raises ``OpentronsError`` if the (sole) channel already holds a tip
    (double-pickup guard, mirrors ``FlexHead8``'s). Tip tracker change is
    staged (``commit=False``) before the wire command; after the wire
    command succeeds, the hardware tip-presence sensor is checked
    (``_verify_tips_seated()``) -- the tracker and ``_channel_tips`` are
    committed only if that verification passes, rolled back (with no
    ``_channel_tips`` mutation) if the sensor reports a missed pickup.
    """
    if self._channel_tips[0] is not None:
      raise OpentronsError(
        "HasTipError",
        "Channel already holds a tip; drop it before picking up another.",
      )

    rack = self._require_itemized_parent(tip_spot)
    labware_id = await self.flex._ensure_labware_loaded(rack)
    well_name = rack.get_child_identifier(tip_spot)

    tip = tip_spot.get_tip()
    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    if tracking and not tip_spot.tracker.is_disabled:
      tip_spot.tracker.remove_tip()  # commit=False: stages + validates
      staged_trackers.append(tip_spot.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
    }
    well_location = self._well_location([offset], [None], origin="top")
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_pickup("pickUpTip", params, staged_trackers)
    self._channel_tips[0] = tip
    await self._retract_to_traversal_height()

  @instrument_operation
  async def drop_tips(
    self,
    target: Union[TipSpot, Trash],
  ) -> None:
    """Drop the mounted tip -- one wire command naming ``target``.

    A ``TipSpot`` target returns the tip (one ``dropTip`` command); a
    ``Trash`` target discards via the addressable-area drop sequence. The
    tip tracker is committed only for a ``TipSpot`` target (None-skip: a
    no-op if the channel holds no tip). After the wire drop + tracker
    commit, ``_confirm_tips_cleared()`` checks the hardware tip-presence
    sensor and logs a warning (does not raise) if it still reports a tip.
    """

    if isinstance(target, Trash):
      await self._execute_trash_drop(target)
      self._channel_tips[0] = None
      await self._confirm_tips_cleared()
      await self._retract_to_traversal_height()
      return

    tip = self._channel_tips[0]
    rack = self._require_itemized_parent(target)
    labware_id = await self.flex._ensure_labware_loaded(rack)
    well_name = rack.get_child_identifier(target)

    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    if tip is not None and tracking and not target.tracker.is_disabled:
      target.tracker.add_tip(tip, commit=False)  # stages + validates (HasTipError if occupied)
      staged_trackers.append(target.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
    }

    await self._execute_liquid_op("dropTip", params, staged_trackers)
    self._channel_tips[0] = None
    await self._confirm_tips_cleared()
    await self._retract_to_traversal_height()

  @instrument_operation
  async def discard_tips(self, trash: Trash) -> None:
    """Discard the mounted tip into the trash."""
    await self.drop_tips(trash)

  @instrument_operation
  async def aspirate(
    self,
    target: Union[Well, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate from a well or single-cavity container using PLR coordinates and in-place aspiration.

    A ``Well`` is addressed through its plate parent by well name; a bare
    ``Container`` (trough/reservoir) at its own sole well. Requires a mounted
    tip. Follows stage -> validate -> wire -> commit/rollback: the tracker
    (``remove_liquid``) is staged BEFORE the wire command, so an infeasible
    aspirate raises before any hardware motion. The head primes at traversal
    height before descending to the PLR cavity floor plus clearance.
    """
    warnings.warn("FlexHead1.aspirate has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    staged_trackers = self._container_trackers(target)
    await self._pipette(
      "aspirate", target, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  @instrument_operation
  async def dispense(
    self,
    target: Union[Well, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense to a well or single-cavity container using PLR coordinates and in-place dispensing.

    Mirrors ``aspirate``: same addressing, same mounted-tip requirement, and
    stage -> validate -> wire -> commit/rollback with ``add_liquid`` staged
    BEFORE the wire command.
    """
    warnings.warn("FlexHead1.dispense has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    staged_trackers = self._container_trackers(target)
    await self._pipette(
      "dispense", target, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  @instrument_operation
  async def touch_tip(
    self,
    well: Well,
    radius: float = 1.0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Touch the mounted tip to the sides of ``well`` -- one ``touchTip`` command.

    ``radius`` is the fraction of the well radius the tip moves toward
    (1.0 = the wall); ``offset`` IS the touch position, not a shift (see
    ``_touch_tip_params``). Requires a mounted tip, checked before any wire
    command. No trackers are involved.
    """
    self._require_mounted_tip()
    labware_id, well_name = await self._well_target(well)
    await self.flex._execute_command(
      "touchTip", self._touch_tip_params(labware_id, well_name, radius, offset)
    )

  @instrument_operation
  async def liquid_probe(self, well: Well) -> float:
    """Probe downward in ``well`` until the pressure sensor detects liquid; return its z (mm).

    One ``liquidProbe`` command naming ``well``. Requires a mounted tip
    (checked before any wire command). Raises ``OpentronsError`` if no
    liquid is found; use ``try_liquid_probe`` for the non-raising variant.
    """
    self._require_mounted_tip()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    return await self._liquid_probe_z(labware_id, well_name, f"well {well.name!r}")

  @instrument_operation
  async def try_liquid_probe(self, well: Well) -> Optional[float]:
    """Like ``liquid_probe`` but return ``None`` instead of raising when no liquid is found."""
    self._require_mounted_tip()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    return await self._probe_z("tryLiquidProbe", labware_id, well_name)


class FlexHead8(_FlexHead):
  """8-channel pipette head, column-addressed (anchor-well fan-out).

  Liquid operations use PLR coordinates anchored at the rearmost
  well the nozzle row covers (on a 96-format plate, column 2 uses A3).
  The Flex hardware fans the in-place plunger command out to all 8 physical
  nozzles. Tip/volume trackers are committed only for the channels/wells
  actually actuated, skipping ``None`` (inactive) channels (None-skip) --
  and only after the wire command succeeds.

  ``column`` indexes the sets of 8 rows the nozzles can cover, which on a
  96-format plate is just the physical columns (0-11). A denser layout has
  more than one such set per physical column, since the nozzles skip rows to
  hold their 9 mm pitch: a 384 plate takes ``column`` 0-47, where 0 covers
  A1/C1/E1/../O1 and 1 covers B1/D1/../P1. See
  ``_column_anchor_and_items``.

  Single-tip cherry-pick (``pick_up_single_tip``/``aspirate_single``/
  ``dispense_single``/``drop_single_tip``) switches the pipette to SINGLE
  nozzle mode first via ``configureNozzleLayout``; column ops reset back to
  ALL mode if a prior single-tip op left the layout otherwise
  (``_ensure_all_mode``).

  Coordinate motion, position reads, priming, tip handling and plate air
  transfers have been exercised on the p1000 multi-channel Flex. Air tests
  establish command sequencing and motion, not liquid-volume accuracy.
  Tip-presence sensing can report absent for a physically seated single tip.
  """

  def __init__(
    self,
    flex: "Flex",
    mount: str,
    pipette_id: str,
    channels: int,
    pipette_model: str,
    max_volume: float,
  ) -> None:
    super().__init__(flex, mount, pipette_id, channels, pipette_model, max_volume)
    self._nozzle_layout: str = "ALL"  # "ALL" | "SINGLE"
    # The anchor the robot was actually SENT, not one merely computed here.
    self._single_anchor: Optional[str] = None
    self._partial_primary_channel: Optional[int] = None

  # --- Nozzle layout guard ---

  async def _ensure_single_mode(self, nozzle: str) -> None:
    """Put the robot on this anchor nozzle, sending the layout if it is not already.

    Every single-nozzle op calls this rather than assuming the pickup's anchor
    still stands. Which nozzle is primary decides which SIDE the seven idle
    nozzles hang on, so an anchor the driver assumed but never transmitted
    points them the opposite way from where its own clearance maths looked.
    """
    if self._nozzle_layout == "SINGLE" and self._single_anchor == nozzle:
      return
    await self._configure_nozzle_layout({"style": "SINGLE", "primaryNozzle": nozzle})
    self._nozzle_layout = "SINGLE"
    self._single_anchor = nozzle

  async def _ensure_all_mode(self) -> None:
    """Reset to the ALL nozzle layout before a column op.

    A prior single-tip op may have left the pipette in SINGLE mode. Column
    ops always address all 8 physical channels, so they must not silently
    run under a stale single-nozzle configuration -- if the layout isn't
    already ALL, reset it first. The reset is refused while the single tip
    from that op is still mounted, since the robot refuses it too.
    """
    if self._nozzle_layout == "ALL":
      return
    await self._configure_nozzle_layout({"style": "ALL"})
    self._nozzle_layout = "ALL"
    self._single_anchor = None

  # --- Column helpers ---

  @staticmethod
  def _column_anchor_and_items(itemized: ItemizedResource, column: int) -> Tuple[str, List[Any]]:
    """Validate ``column`` against the labware's real grid; return the anchor
    well name plus the 8 resources the nozzle row covers, rearmost first.

    Every column op calls this BEFORE any wire command so a rejected op ships
    nothing.

    The 8 nozzles sit at a 9 mm pitch, so on a denser layout they cover every
    ``row_stride``-th row rather than adjacent rows: a 384 plate has two
    interleaved sets of 8 per physical column, addressed as consecutive
    ``column`` indices (so 0-47, physical column ``column // 2``, rear-row set
    when even). A row count that is not a multiple of 8 has no such set and is
    rejected.
    """
    rows = itemized.num_items_y
    row_stride, remainder = divmod(rows, _NUM_CHANNELS)
    if row_stride < 1 or remainder:
      raise ValueError(
        f"'{itemized.name}' has {rows} rows; the 8 nozzles cover 8 evenly spaced rows, "
        f"so column ops need a row count that is a multiple of {_NUM_CHANNELS}."
      )
    num_columns = itemized.num_items_x * row_stride
    if not 0 <= column < num_columns:
      raise ValueError(
        f"Column {column} out of range for resource with {num_columns} columns "
        f"(0-{num_columns - 1})."
      )
    physical_column, row_phase = divmod(column, row_stride)
    start = physical_column * rows + row_phase
    column_items = itemized.get_all_items()[start : start + rows : row_stride]
    return itemized.get_child_identifier(column_items[0]), column_items

  # --- Column tip operations ---

  @instrument_operation
  async def pick_up_tips(
    self,
    target: Union[TipRack, Sequence[TipSpot], TipSpot],
    *,
    column: Optional[int] = None,
    use_channels: Optional[Sequence[int]] = None,
    offset: Optional[Coordinate] = None,
    primary_nozzle: Optional[str] = None,
  ) -> None:
    """Pick up tip(s), choosing the nozzle layout for this call.

    Pickup is where per-call nozzle configuration lives -- the engine only lets
    the layout change while no tip is on -- so this is the method that emits the
    ``configureNozzleLayout``. The ``target`` *type* selects the layout:

    - a ``Sequence[TipSpot]`` (a ``rack.column(c)``) -> ALL, a full column;
    - a single ``TipSpot`` -> SINGLE, one cherry-picked tip.

    ``use_channels`` names the channels to fill: ``None``/all 8 -> ALL; ``[0]``
    or ``[7]`` -> SINGLE on the A1 or H1 nozzle (the only two an 8-channel Flex
    can single-anchor). The ALL configuration is emitted only when a prior
    single-tip op left the layout otherwise (``_ensure_all_mode``); a SINGLE
    pickup always emits its ``configureNozzleLayout``. ``column`` is the
    transitional bridge for the old ``pick_up_tips(rack, column=c)`` call;
    prefer ``rack.column(c)``.
    """
    if column is not None:
      await self._pick_up_column(cast(TipRack, target), column, offset)
      return
    if isinstance(target, (list, tuple)):
      await self._pick_up_spots(list(target), use_channels, offset)
      return
    if isinstance(target, TipSpot):
      await self._pick_up_single_spot(target, use_channels, offset, primary_nozzle)
      return
    raise TypeError(
      f"pick_up_tips target must be a column (Sequence[TipSpot]) or a single TipSpot; "
      f"got {type(target).__name__}."
    )

  async def _pick_up_single_spot(
    self,
    spot: TipSpot,
    use_channels: Optional[Sequence[int]],
    offset: Optional[Coordinate],
    primary_nozzle: Optional[str],
  ) -> None:
    """Cherry-pick one tip (SINGLE layout), resolving the anchor nozzle.

    ``use_channels`` may name only the single-anchor channels (0 -> A1, 7 -> H1);
    any other channel is refused, since an 8-channel Flex cannot anchor a
    single-nozzle layout elsewhere.
    """
    parent = self._require_itemized_parent(spot)
    well_name = parent.get_child_identifier(spot)
    if use_channels is not None:
      if len(use_channels) != 1 or use_channels[0] not in _SINGLE_NOZZLE_BY_CHANNEL:
        raise OpentronsError(
          "NozzleConfigError",
          f"An 8-channel Flex can single-anchor only on channels "
          f"{sorted(_SINGLE_NOZZLE_BY_CHANNEL)} (A1/H1); got use_channels={list(use_channels)}.",
        )
      resolved = _SINGLE_NOZZLE_BY_CHANNEL[use_channels[0]]
      if primary_nozzle is not None and primary_nozzle != resolved:
        raise OpentronsError(
          "NozzleConfigError",
          f"use_channels={list(use_channels)} names nozzle {resolved}, but "
          f"primary_nozzle={primary_nozzle!r} was also given.",
        )
      primary_nozzle = resolved
    await self.pick_up_single_tip(
      cast(TipRack, parent),
      well_name,
      offset=offset,
      primary_nozzle=primary_nozzle if primary_nozzle is not None else _DEFAULT_SINGLE_NOZZLE,
    )

  async def _pick_up_spots(
    self,
    spots: List[TipSpot],
    use_channels: Optional[Sequence[int]],
    offset: Optional[Coordinate],
  ) -> None:
    """Pick up a full or partial column given as a list of tip spots.

    ``use_channels`` names the channel each spot fills, in order. The full
    8-channel set (or ``None`` for a full column) uses the ALL layout; a
    contiguous run from the front (incl. H1/ch7) or rear (incl. A1/ch0) end uses
    a QUADRANT partial layout.
    """
    if not spots:
      raise ValueError("pick_up_tips: the target spot sequence is empty.")
    n = len(spots)
    if use_channels is None:
      if n != self.channels:
        raise OpentronsError(
          "NozzleConfigError",
          f"{n} spots given without use_channels; pass use_channels to name which "
          f"nozzles a partial column fills, or give a full column of {self.channels}.",
        )
      use_channels = list(range(self.channels))
    uc = list(use_channels)
    if len(uc) != n:
      raise OpentronsError(
        "NozzleConfigError",
        f"use_channels has {len(uc)} entries but {n} spots were given; one per spot.",
      )
    ordered = sorted(uc)
    if ordered != list(range(ordered[0], ordered[-1] + 1)):
      raise OpentronsError(
        "NozzleConfigError",
        f"a partial column must be a contiguous run of channels; got use_channels={uc}.",
      )
    if ordered == list(range(self.channels)):
      parent = cast(TipRack, self._require_itemized_parent(spots[0]))
      anchor = parent.get_child_identifier(spots[0])
      await self._pick_up_spots_core(parent, anchor, spots, offset)
      return
    await self._pick_up_partial(spots, uc, offset)

  async def _pick_up_partial(
    self,
    spots: List[TipSpot],
    use_channels: List[int],
    offset: Optional[Coordinate],
  ) -> None:
    """Pick up a contiguous partial column in a QUADRANT nozzle layout.

    The layout matches what Opentrons' own ``configure_nozzle_layout`` emits for a
    ``PARTIAL_COLUMN``: a front-anchored run (including H1/ch7) sets
    ``primaryNozzle = frontRightNozzle = "H1"`` with ``backLeftNozzle`` the rear-most
    active nozzle; a rear-anchored run (including A1/ch0) mirrors it on A1. The
    ``configureNozzleLayout`` is emitted here (no tip is on yet, so the engine
    accepts it); the ``pickUpTip`` is anchored at the well under the primary nozzle.
    """
    ordered = sorted(use_channels)
    top = self.channels - 1
    if ordered[-1] == top:  # front-anchored partial (includes H1)
      primary_channel = top
      primary = front_right = "H1"
      back_left = f"{_ROW_LETTERS[ordered[0]]}1"
    elif ordered[0] == 0:  # rear-anchored partial (includes A1)
      primary_channel = 0
      primary = back_left = "A1"
      front_right = f"{_ROW_LETTERS[ordered[-1]]}1"
    else:
      raise OpentronsError(
        "NozzleConfigError",
        "an 8-channel partial column must run from the front (incl. H1) or the rear "
        f"(incl. A1) end; got use_channels={use_channels}.",
      )

    spot_by_channel = {use_channels[i]: spots[i] for i in range(len(spots))}
    for ch in ordered:
      if spot_by_channel[ch].has_tip() and self._channel_tips[ch] is not None:
        raise OpentronsError(
          "HasTipError",
          f"Channel {ch} already holds a tip; drop it before picking up another.",
        )

    await self._configure_nozzle_layout(
      {
        "style": "QUADRANT",
        "primaryNozzle": primary,
        "frontRightNozzle": front_right,
        "backLeftNozzle": back_left,
      }
    )
    self._nozzle_layout = "PARTIAL"
    self._partial_primary_channel = primary_channel

    primary_spot = spot_by_channel[primary_channel]
    parent = cast(TipRack, self._require_itemized_parent(primary_spot))
    labware_id = await self.flex._ensure_labware_loaded(parent)
    anchor = parent.get_child_identifier(primary_spot)

    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    tips: Dict[int, Tip] = {}
    for ch in ordered:
      spot = spot_by_channel[ch]
      if not spot.has_tip():
        continue
      tips[ch] = spot.get_tip()
      if tracking and not spot.tracker.is_disabled:
        spot.tracker.remove_tip()  # commit=False: stages + validates
        staged_trackers.append(spot.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": anchor,
    }
    well_location = self._well_location([offset], [None], origin="top")
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_pickup("pickUpTip", params, staged_trackers)
    for ch, tip in tips.items():
      self._channel_tips[ch] = tip
    await self._retract_to_traversal_height()

  async def _pick_up_column(
    self,
    tip_rack: TipRack,
    column: int,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up a full column (8 tips) by column index (transitional bridge)."""
    well_name, column_spots = self._column_anchor_and_items(tip_rack, column)
    await self._pick_up_spots_core(tip_rack, well_name, column_spots, offset)

  async def _pick_up_spots_core(
    self,
    tip_rack: TipRack,
    anchor_name: str,
    spots: List[Any],
    offset: Optional[Coordinate],
  ) -> None:
    """Shared ALL-layout column pickup with a single ``pickUpTip`` command.

    Anchored at the column's rearmost spot; the hardware fans the pickup motion
    out to all 8 physical nozzles. Follows stage -> validate -> wire ->
    verify -> commit/rollback: the double-pickup guard is validated before ANY
    wire command, tip trackers are staged (``commit=False``) before the pickup
    command, then, after the wire command succeeds, the hardware tip-presence
    sensor is checked (``_verify_tips_seated()``); trackers and ``_channel_tips``
    are committed only if that verification passes, and rolled back (with no
    ``_channel_tips`` mutation) if the sensor reports a missed pickup. Only spots
    that actually had a tip are staged (None-skip).
    """

    for i, spot in enumerate(spots):
      if spot.has_tip() and self._channel_tips[i] is not None:
        raise OpentronsError(
          "HasTipError",
          f"Channel {i} already holds a tip; drop it before picking up another.",
        )

    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(tip_rack)

    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    tips: List[Optional[Tip]] = [None] * len(spots)
    for i, spot in enumerate(spots):
      if not spot.has_tip():
        continue
      tips[i] = spot.get_tip()
      if tracking and not spot.tracker.is_disabled:
        spot.tracker.remove_tip()  # commit=False: stages + validates
        staged_trackers.append(spot.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": anchor_name,
    }
    well_location = self._well_location([offset], [None], origin="top")
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_pickup("pickUpTip", params, staged_trackers)
    for i, tip in enumerate(tips):
      self._channel_tips[i] = tip
    await self._retract_to_traversal_height()

  @instrument_operation
  async def drop_tips(
    self,
    target: Union[TipRack, Trash],
    column: Optional[int] = None,
  ) -> None:
    """Drop a full column of tips -- one wire command, fanned to 8 channels.

    A ``TipRack`` target returns tips to ``column`` (required, one
    ``dropTip`` command); a ``Trash`` target discards via the
    addressable-area drop sequence (``column`` ignored). Tip trackers are
    committed only for channels that actually held a tip (None-skip); trash
    drops never return tips to a rack tracker. After the wire drop + tracker
    commit, ``_confirm_tips_cleared()`` checks the hardware tip-presence
    sensor and logs a warning (does not raise) if it still reports a tip.

    The trash drop resets the nozzle layout AFTER the drop, not before: it
    addresses no channel, and the robot refuses every reconfiguration while
    a tip is on -- so resetting first would make this op, the only one that
    can clear a cherry-picked tip, need the tip already gone.
    """

    if isinstance(target, Trash):
      await self._execute_trash_drop(target)
      self._channel_tips = [None] * self.channels
      await self._confirm_tips_cleared()
      await self._retract_to_traversal_height()
      await self._ensure_all_mode()
      return

    if self._nozzle_layout == "SINGLE":
      raise OpentronsError(
        "NozzleLayoutError",
        "A column drop fans out to all 8 nozzles, so a single cherry-picked tip cannot "
        "be returned to a rack. Discard it with drop_single_tip(trash).",
      )

    if column is None:
      raise ValueError("column is required when dropping tips to a TipRack.")

    well_name, column_spots = self._column_anchor_and_items(target, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(target)

    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    for i, spot in enumerate(column_spots):
      tip = self._channel_tips[i]
      if tip is not None and tracking and not spot.tracker.is_disabled:
        spot.tracker.add_tip(tip, commit=False)  # stages + validates (HasTipError if occupied)
        staged_trackers.append(spot.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
    }

    await self._execute_liquid_op("dropTip", params, staged_trackers)
    for i in range(len(column_spots)):
      self._channel_tips[i] = None
    await self._confirm_tips_cleared()
    await self._retract_to_traversal_height()

  @instrument_operation
  async def discard_tips(self, trash: Trash) -> None:
    """Discard the mounted column of tips into the trash."""
    await self.drop_tips(trash)

  # --- Unified liquid handling (per-call nozzle configuration) ---

  def _require_use_channels_match_mounted(self, use_channels: Optional[Sequence[int]]) -> None:
    """Refuse a ``use_channels`` that is not exactly the mounted channels.

    ``use_channels`` names the active nozzles for the call, but the layout is
    fixed at pickup (the engine refuses to reconfigure while a tip is on) and
    one fanned command actuates every mounted nozzle -- so it cannot address a
    subset. An explicit ``use_channels`` must therefore equal the channels that
    currently hold tips; left ``None`` the mounted set is used implicitly. The
    reference channel it names is also what the reachability check reads to pick
    the active nozzle, so a mismatch here would misaim that guard.
    """
    if use_channels is None:
      return
    mounted = {i for i, tip in enumerate(self._channel_tips) if tip is not None}
    requested = set(use_channels)
    if requested != mounted:
      raise OpentronsError(
        "NozzleConfigMismatch",
        f"use_channels={sorted(requested)} does not match the mounted channels "
        f"{sorted(mounted)}. The nozzle layout is fixed at pickup and one command fans to "
        "every mounted nozzle, so use_channels must name exactly the channels holding tips.",
      )

  @instrument_operation
  async def aspirate(
    self,
    target: Union[Plate, Well, Sequence[Well], Container],
    volume: float,
    *,
    column: Optional[int] = None,
    use_channels: Optional[Sequence[int]] = None,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate ``volume`` uL from ``target`` using PLR coordinates and in-place aspiration.

    ONE method for every 8-channel aspirate; the ``target`` *type* selects the
    addressing and ``use_channels`` names the active nozzles for this call:

    - a ``Sequence[Well]`` (a ``plate.column(c)``) -> that column, anchored at
      its rearmost well, one ``Well.tracker`` per active channel (None-skip);
    - a single ``Well`` -> the mounted single nozzle draws from it;
    - a ``Container`` (trough/reservoir) -> every active nozzle dips into the
      one cavity, its single tracker staged with ``volume x active-channels``.

    The nozzle LAYOUT is fixed at pickup, not here: the engine refuses a
    reconfiguration while a tip is attached, and an aspirate always has a tip,
    so no ``configureNozzleLayout`` is ever emitted by an aspirate. This method
    only names which mounted nozzles it drives and refuses a request the mount
    cannot satisfy, before any wire command.

    ``column`` is the transitional bridge for the old ``aspirate(plate,
    column=c)`` call and takes a ``Plate`` target; prefer ``plate.column(c)``.
    """
    self._require_use_channels_match_mounted(use_channels)
    if column is not None:
      await self._aspirate_column(
        cast(Plate, target), column, volume, flow_rate, offset, liquid_height
      )
      return
    if isinstance(target, (list, tuple)):
      await self._aspirate_wells(
        list(target), volume, use_channels, flow_rate, offset, liquid_height
      )
      return
    if isinstance(target, Well):  # Well is a Container, so check it first
      await self._aspirate_single_well(target, volume, flow_rate, offset, liquid_height)
      return
    if isinstance(target, Container):
      await self.aspirate_container(
        target, volume, flow_rate=flow_rate, offset=offset, liquid_height=liquid_height
      )
      return
    raise TypeError(
      f"aspirate target must be a Plate column (Sequence[Well]), a single Well, or a "
      f"Container; got {type(target).__name__}."
    )

  def _check_pipetting_clearance(self, target: Container, position: Coordinate) -> None:
    """Check adjacent labware against the unused nozzles' lowest descent height.

    The requested position locates the mounted tip end. Bare nozzles are
    higher by the tip length minus its fitting overlap. Use the shortest
    mounted effective length so a mixed set cannot overstate clearance.
    """
    if self._nozzle_layout == "SINGLE":
      primary = self._active_single_channel()
    elif self._nozzle_layout == "PARTIAL":
      partial_primary = self._partial_primary_channel
      if partial_primary is None:
        raise ValueError("Partial layout has no primary nozzle")
      primary = partial_primary
    else:
      return
    tips = [tip for tip in self._channel_tips if tip is not None]
    lengths = [tip.get_size_z() - tip.fitting_depth for tip in tips]
    if not lengths or any(not math.isfinite(length) or length <= 0 for length in lengths):
      raise ValueError("Mounted tips must define a positive effective length")
    labware = self._require_itemized_parent(target) if isinstance(target, Well) else target
    slot = self.flex.deck.get_slot(labware)
    if slot is None:
      raise ValueError("Pipetting target must be assigned to the Flex deck")
    self.flex.deck.check_partial_nozzle_clearance(
      slot,
      f"{_ROW_LETTERS[primary]}1",
      len(tips),
      operation_z=position.z + min(lengths),
    )

  async def _aspirate_single_well(
    self,
    well: Well,
    volume: float,
    flow_rate: Optional[float],
    offset: Optional[Coordinate],
    liquid_height: Optional[float],
  ) -> None:
    """Aspirate one well with the mounted single nozzle using its PLR coordinates.

    Requires exactly one mounted tip (a SINGLE-layout cherry-pick); refuses a
    well the mounted anchor cannot reach, and a target whose idle nozzles would
    overhang the adjacent slot's labware -- all before any wire command. The
    well's own tracker is staged with ``volume``.
    """
    self._active_single_channel()
    parent = self._require_itemized_parent(well)
    well_name = parent.get_child_identifier(well)
    self._require_reach_in_single_layout(parent, well_name)
    staged_trackers = self._container_trackers(well)
    await self._pipette("aspirate", well, volume, flow_rate, offset, liquid_height, staged_trackers)

  async def _liquid_column_target(
    self, wells: List[Well], use_channels: Optional[Sequence[int]]
  ) -> Tuple[Well, List[VolumeTracker]]:
    """Resolve the mounted layout's primary well and check partial clearance."""
    parent = self._require_itemized_parent(wells[0])
    if self._nozzle_layout != "PARTIAL":
      await self._ensure_all_mode()
      return wells[0], self._well_trackers(wells)
    channels = (
      list(use_channels)
      if use_channels is not None
      else [i for i, tip in enumerate(self._channel_tips) if tip is not None]
    )
    self._require_use_channels_match_mounted(channels)
    if len(channels) != len(wells) or len(set(channels)) != len(channels):
      raise ValueError("Provide one target well for each mounted channel")
    primary = self._partial_primary_channel
    if primary not in channels:
      raise ValueError("The partial layout's primary channel must have a target well")
    anchor = wells[channels.index(primary)]
    anchor_position = anchor.get_absolute_location(x="c", y="c", z="cavity_bottom")
    for channel, well in zip(channels, wells):
      if well.parent is not parent:
        raise ValueError("Partial-column wells must share a parent")
      position = well.get_absolute_location(x="c", y="c", z="cavity_bottom")
      expected_y = anchor_position.y + (primary - channel) * 9
      if not (
        math.isclose(position.x, anchor_position.x, abs_tol=0.01)
        and math.isclose(position.y, expected_y, abs_tol=0.01)
        and math.isclose(position.z, anchor_position.z, abs_tol=0.01)
      ):
        raise ValueError("Target wells must match the mounted nozzles' 9 mm spacing")
    slot = self.flex.deck.get_slot(parent)
    if slot is None:
      raise ValueError("Target plate must be assigned to the Flex deck")
    well_by_channel = dict(zip(channels, wells))
    return anchor, [well_by_channel[ch].tracker for ch in sorted(channels)]

  async def _aspirate_wells(
    self,
    wells: List[Well],
    volume: float,
    use_channels: Optional[Sequence[int]],
    flow_rate: Optional[float],
    offset: Optional[Coordinate],
    liquid_height: Optional[float],
  ) -> None:
    """Aspirate a PLR-native column (a list of wells) -- one anchored ``aspirate``.

    The wells are the resources the nozzle row covers, rearmost first (as
    ``plate.column(c)`` returns them); the command anchors at the first and the
    hardware fans it to all 8 nozzles. One ``Well.tracker`` is staged per well
    whose channel holds a tip (None-skip), before the wire command.
    """
    self._require_mounted_tip()
    if not wells:
      raise ValueError("aspirate: the target well sequence is empty.")
    anchor, staged_trackers = await self._liquid_column_target(wells, use_channels)
    await self._pipette(
      "aspirate", anchor, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  async def _aspirate_column(
    self,
    plate: Plate,
    column: int,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate a column using its rearmost well as the coordinate anchor.

    Requires at least one mounted tip. Follows stage -> validate -> wire ->
    commit/rollback: ``Well.tracker`` (``remove_liquid``) is staged for
    every well whose channel actually holds a tip (None-skip; wells outside
    ``column`` are never touched -- the Case-1 regression guard) BEFORE the
    wire command, so an infeasible aspirate (e.g. ``TooLittleLiquidError``)
    raises before any hardware motion. The head primes at traversal height
    before descending to the PLR cavity floor plus clearance.
    """
    self._require_mounted_tip()
    if not isinstance(plate, Plate):
      raise OpentronsError("Invalid target", "Pipetting requires a Plate")
    _, column_wells = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    staged_trackers = self._well_trackers(column_wells)
    await self._pipette(
      "aspirate", column_wells[0], volume, flow_rate, offset, liquid_height, staged_trackers
    )

  @instrument_operation
  async def dispense(
    self,
    target: Union[Plate, Well, Sequence[Well], Container],
    volume: float,
    *,
    column: Optional[int] = None,
    use_channels: Optional[Sequence[int]] = None,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense ``volume`` uL into ``target`` using PLR coordinates and in-place dispensing.

    The ``dispense`` mirror of :meth:`aspirate`: the ``target`` type selects the
    addressing (a ``plate.column(c)`` sequence, a single ``Well``, or a
    ``Container`` cavity) and ``use_channels`` names the active nozzles. No
    ``configureNozzleLayout`` is emitted (the layout is fixed at pickup). See
    :meth:`aspirate` for the full contract; ``column`` is the same transitional
    bridge for the old ``dispense(plate, column=c)`` call.
    """
    self._require_use_channels_match_mounted(use_channels)
    if column is not None:
      await self._dispense_column(
        cast(Plate, target), column, volume, flow_rate, offset, liquid_height
      )
      return
    if isinstance(target, (list, tuple)):
      await self._dispense_wells(
        list(target), volume, use_channels, flow_rate, offset, liquid_height
      )
      return
    if isinstance(target, Well):  # Well is a Container, so check it first
      await self._dispense_single_well(target, volume, flow_rate, offset, liquid_height)
      return
    if isinstance(target, Container):
      await self.dispense_container(
        target, volume, flow_rate=flow_rate, offset=offset, liquid_height=liquid_height
      )
      return
    raise TypeError(
      f"dispense target must be a Plate column (Sequence[Well]), a single Well, or a "
      f"Container; got {type(target).__name__}."
    )

  async def _dispense_single_well(
    self,
    well: Well,
    volume: float,
    flow_rate: Optional[float],
    offset: Optional[Coordinate],
    liquid_height: Optional[float],
  ) -> None:
    """Dispense to one well with the mounted single nozzle -- the mirror of
    :meth:`_aspirate_single_well`."""
    self._active_single_channel()
    parent = self._require_itemized_parent(well)
    well_name = parent.get_child_identifier(well)
    self._require_reach_in_single_layout(parent, well_name)
    staged_trackers = self._container_trackers(well)
    await self._pipette("dispense", well, volume, flow_rate, offset, liquid_height, staged_trackers)

  async def _dispense_wells(
    self,
    wells: List[Well],
    volume: float,
    use_channels: Optional[Sequence[int]],
    flow_rate: Optional[float],
    offset: Optional[Coordinate],
    liquid_height: Optional[float],
  ) -> None:
    """Dispense a PLR-native column (a list of wells) -- one anchored ``dispense``."""
    self._require_mounted_tip()
    if not wells:
      raise ValueError("dispense: the target well sequence is empty.")
    anchor, staged_trackers = await self._liquid_column_target(wells, use_channels)
    await self._pipette(
      "dispense", anchor, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  async def _dispense_column(
    self,
    plate: Plate,
    column: int,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense a column using its rearmost well as the coordinate anchor.

    Requires at least one mounted tip. Follows stage -> validate -> wire ->
    commit/rollback: ``Well.tracker`` (``add_liquid``) is staged for every
    well whose channel actually holds a tip (None-skip) BEFORE the wire
    command, so an infeasible dispense (e.g. ``TooLittleVolumeError``)
    raises before any hardware motion.
    """
    self._require_mounted_tip()
    if not isinstance(plate, Plate):
      raise OpentronsError("Invalid target", "Pipetting requires a Plate")
    _, column_wells = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    staged_trackers = self._well_trackers(column_wells)
    await self._pipette(
      "dispense", column_wells[0], volume, flow_rate, offset, liquid_height, staged_trackers
    )

  # --- Single-cavity container (trough/reservoir) liquid handling ---

  @instrument_operation
  async def aspirate_container(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate ``volume`` uL per channel from one single-cavity container.

    PLR centers the 8-nozzle row in the cavity (trough/reservoir), adds
    ``offset`` and ``liquid_height``, then aspirates in place. Requires a mounted tip, a cavity the
    offset-shifted row still fits, and ALL nozzle mode. Each channel holding
    a tip draws ``volume``, so the container's single tracker is staged with
    the total and settled as one op.
    """
    self._require_mounted_tip()
    self._require_span_fits_container(container, 0.0, _EIGHT_CHANNEL_Y_SPAN, offset)
    await self._ensure_all_mode()
    staged_trackers = self._container_trackers(container)
    await self._pipette(
      "aspirate",
      container,
      volume,
      flow_rate,
      offset,
      liquid_height,
      staged_trackers,
    )

  @instrument_operation
  async def dispense_container(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense ``volume`` uL per channel into one single-cavity container.

    Mirrors ``aspirate_container``: same addressing, same pre-wire guards,
    and the container's single tracker staged with the total.
    """
    self._require_mounted_tip()
    self._require_span_fits_container(container, 0.0, _EIGHT_CHANNEL_Y_SPAN, offset)
    await self._ensure_all_mode()
    staged_trackers = self._container_trackers(container)
    await self._pipette(
      "dispense",
      container,
      volume,
      flow_rate,
      offset,
      liquid_height,
      staged_trackers,
    )

  @instrument_operation
  async def touch_tip(
    self,
    plate: Plate,
    column: int,
    radius: float = 1.0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Touch the mounted tips to their well walls -- one ``touchTip`` command
    anchored at the column's rearmost well.

    ``radius`` and ``offset`` behave as in ``FlexHead1.touch_tip``. Requires
    a mounted tip and a valid column, both checked before any wire command,
    plus ALL nozzle mode. No trackers are involved.
    """
    self._require_mounted_tip()
    well_name, _ = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    await self.flex._execute_command(
      "touchTip", self._touch_tip_params(labware_id, well_name, radius, offset)
    )

  @instrument_operation
  async def liquid_probe(self, plate: Plate, column: int) -> float:
    """Probe for liquid in a column -- one ``liquidProbe`` command anchored at
    its rearmost well; return the found liquid z (mm).

    Requires at least one mounted tip and a valid column (both checked
    before any wire command) and ALL nozzle mode (reset first if a
    single-tip op left the layout otherwise). Raises ``OpentronsError`` if
    no liquid is found; use ``try_liquid_probe`` for the non-raising
    variant.
    """
    self._require_mounted_tip()
    well_name, _ = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    return await self._liquid_probe_z(labware_id, well_name, f"column {column} of {plate.name!r}")

  @instrument_operation
  async def try_liquid_probe(self, plate: Plate, column: int) -> Optional[float]:
    """Like ``liquid_probe`` but return ``None`` instead of raising when no liquid is found."""
    self._require_mounted_tip()
    well_name, _ = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    return await self._probe_z("tryLiquidProbe", labware_id, well_name)

  # --- Single-tip cherry-pick ---

  @staticmethod
  def _single_nozzle_reaches(labware: ItemizedResource, well: str, nozzle: str) -> bool:
    """Whether anchoring ``nozzle`` over ``well`` keeps the pipette inside the robot.

    In a SINGLE layout the robot still carries the whole pipette: the body hangs
    95 mm forward of the mount, and the anchor nozzle sits 16 mm (A1) or 79 mm
    (H1) forward of it. Anchoring A1 over a front-row slot therefore drives the
    mount itself past the robot's front limit, and anchoring H1 at the very back
    runs into the rear limit. The same arithmetic the engine runs in
    ``pipette_movement_conflict``, which only guards Python-API protocols -- it
    never sees the run commands this driver posts, so nothing downstream would
    catch an out-of-extents anchor.
    """
    well_y: float = labware.get_item(well).get_absolute_location(y="c").y
    mount_y = well_y - _SINGLE_NOZZLE_Y[nozzle]
    return (
      mount_y + _PIPETTE_BODY_BACK_Y >= _ROBOT_FRONT_LIMIT
      and mount_y + _PIPETTE_BODY_FRONT_Y <= _ROBOT_REAR_LIMIT
    )

  @classmethod
  def reachable_single_nozzles(cls, labware: ItemizedResource, well: str) -> Tuple[str, ...]:
    """The anchor nozzles that can address ``well`` where the labware currently sits.

    Rearmost ("A1") first, since it keeps the most margin at the back of the
    deck. Empty when the well is out of reach both ways. Depends on the deck
    slot, not just the labware, so it is only meaningful once assigned.
    """
    return tuple(
      nozzle for nozzle in _SINGLE_NOZZLES if cls._single_nozzle_reaches(labware, well, nozzle)
    )

  @classmethod
  def _spots_under_idle_nozzles(
    cls, labware: ItemizedResource, well: str, nozzle: str
  ) -> List[Any]:
    """The labware positions the SEVEN idle nozzles sit over, anchored this way.

    The nozzles are ganged: they cannot move relative to each other and they
    always descend together, so every one of them engages whatever is beneath
    it. A "single" pickup is only single because the other seven have nothing
    under them. Positions past the labware's own edge are simply absent from
    the result, which is the case that makes a pick safe.
    """
    rows = labware.num_items_y
    stride = max(rows // _NUM_CHANNELS, 1)
    items = labware.get_all_items()
    target = labware.get_item(well)
    index = items.index(target)
    column, row = divmod(index, rows)
    anchor_channel = _SINGLE_NOZZLES[nozzle]

    under: List[Any] = []
    for channel in range(_NUM_CHANNELS):
      if channel == anchor_channel:
        continue
      other_row = row + (channel - anchor_channel) * stride
      if 0 <= other_row < rows:
        under.append(items[column * rows + other_row])
    return under

  @classmethod
  def _idle_nozzles_are_clear(cls, labware: ItemizedResource, well: str, nozzle: str) -> bool:
    """Whether anchoring here leaves the seven idle nozzles over empty positions."""
    return not any(spot.has_tip() for spot in cls._spots_under_idle_nozzles(labware, well, nozzle))

  @classmethod
  def _next_single_tip(cls, labware: ItemizedResource, nozzle: str) -> str:
    """The well of the next tip to take on ``nozzle``, chosen from tip tracking.

    Walks the rack in the anchor's own consumption order -- H1 back-to-front
    (A->H down each column), A1 front-to-back (H->A) -- and returns the first spot
    that still holds a tip AND whose idle seven are clear. This is how the
    Opentrons engine itself drives a single-nozzle rack. Needs tip tracking: with
    it off nothing knows what the rack holds, so the caller must name a well.
    """
    if not does_tip_tracking():
      raise ValueError(
        "pick_up_single_tip needs an explicit well when tip tracking is off: the "
        "next tip is chosen from what the rack still holds, which is unknown then. "
        "Pass well=..., or turn tip tracking on."
      )
    rows = labware.num_items_y
    items = labware.get_all_items()
    row_order = range(rows) if nozzle == "H1" else range(rows - 1, -1, -1)
    for column in range(len(items) // rows):
      for row in row_order:
        spot = items[column * rows + row]
        if spot.has_tip():
          well = labware.get_child_identifier(spot)
          if cls._idle_nozzles_are_clear(labware, well, nozzle):
            return well
    raise ValueError(
      f"No tip on '{labware.name}' can be taken on the {nozzle} anchor without the "
      f"idle nozzles engaging another: every candidate has an occupied column "
      f"neighbour. Consume a column with pick_up_tips, or empty a neighbour first."
    )

  @classmethod
  def _validate_single_anchor(cls, labware: ItemizedResource, well: str, nozzle: str) -> None:
    """Refuse an anchor that cannot reach the well, or that would take more than one tip.

    Which nozzle carries the tip fixes where the idle seven hang for the rest of
    that tip's life, so it stays the caller's choice and is never swapped for
    another one here. Clearance reads the tip trackers, so it is only checked
    while tip tracking is on: with it off nothing maintains rack contents and
    every position reads as full forever.
    """
    if nozzle not in _SINGLE_NOZZLES:
      raise ValueError(
        f"primary_nozzle={nozzle!r}: an 8-channel Flex can anchor a single-nozzle "
        f"layout only on {' or '.join(_SINGLE_NOZZLES)}."
      )
    reachable = cls.reachable_single_nozzles(labware, well)
    if nozzle not in reachable:
      alternative = (
        f" Anchor on {reachable[0]} instead."
        if reachable
        else " Neither anchor reaches it; move the labware to another slot."
      )
      raise ValueError(
        f"Anchoring the {nozzle} nozzle over '{labware.name}' well '{well}' would "
        f"carry the pipette outside the robot's reach.{alternative}"
      )
    if not does_tip_tracking():
      logger.warning(
        "tip tracking off: cannot verify the idle nozzles are clear for a %s "
        "single-nozzle pickup on '%s' well '%s'; trusting the caller.",
        nozzle,
        labware.name,
        well,
      )
      return
    if cls._idle_nozzles_are_clear(labware, well, nozzle):
      return
    other = next(n for n in _SINGLE_NOZZLES if n != nozzle)
    alternative = (
      f" Anchor on {other} to hang them off the opposite edge."
      if other in reachable and cls._idle_nozzles_are_clear(labware, well, other)
      else " Pick a position whose neighbours along the column are already empty, or use "
      "pick_up_tips for the whole column."
    )
    raise ValueError(
      f"Anchoring the {nozzle} nozzle on '{labware.name}' well '{well}' would take more than "
      f"one tip: the 8 nozzles are ganged and descend together, and the idle seven sit over "
      f"positions that still hold tips.{alternative}"
    )

  async def _ensure_anchored_on_mounted_channel(self) -> None:
    """Anchor the robot on the nozzle that is actually carrying the tip.

    The tip sits on one physical channel and every later single-nozzle op has
    to be driven from that same one. Recomputing an anchor per call and not
    sending it leaves the robot on the pickup's nozzle while this side reasons
    about a different one, which flips which side the seven idle nozzles hang
    on without anything saying so.
    """
    nozzle = _SINGLE_NOZZLE_BY_CHANNEL.get(self._active_single_channel())
    if nozzle is not None:
      await self._ensure_single_mode(nozzle)

  def _require_reach_in_single_layout(self, labware: ItemizedResource, well: str) -> None:
    """Refuse a single-nozzle move to a well the mounted anchor cannot reach.

    The pickup chose an anchor that reached the RACK; a later well on a
    front-row or back-row slot can still sit outside that same anchor's band.
    """
    if self._nozzle_layout != "SINGLE":
      return
    nozzle = _SINGLE_NOZZLE_BY_CHANNEL.get(self._active_single_channel())
    if nozzle is None or self._single_nozzle_reaches(labware, well, nozzle):
      return
    raise ValueError(
      f"The mounted tip is on the {nozzle} nozzle, and reaching '{labware.name}' well "
      f"'{well}' with it would carry the pipette outside the robot's reach. Drop the tip "
      f"and cherry-pick again from a rack the other anchor can reach."
    )

  def _active_single_channel(self) -> int:
    """Return the sole channel holding a tip in single-tip mode.

    Raises if zero or more than one channel is active -- aspirate_single/
    dispense_single/drop_single_tip only make sense with exactly one tip
    mounted.
    """
    active = [i for i, tip in enumerate(self._channel_tips) if tip is not None]
    if len(active) != 1:
      raise RuntimeError(
        f"Single-tip op requires exactly one mounted tip; found {len(active)}. "
        "Call pick_up_single_tip() first."
      )
    return active[0]

  @instrument_operation
  async def pick_up_single_tip(
    self,
    tip_rack: TipRack,
    well: Optional[str] = None,
    offset: Optional[Coordinate] = None,
    primary_nozzle: str = _DEFAULT_SINGLE_NOZZLE,
  ) -> None:
    """Pick up one tip in SINGLE nozzle mode, anchored on ``primary_nozzle``.

    Switches to SINGLE layout (``configureNozzleLayout``) before the
    ``pickUpTip`` command. The eight nozzles are ganged, so a pick is only
    "single" because the idle seven have nothing under them: the anchor, not
    the well, decides what the pick does and which channel ends up holding the
    tip. ``primary_nozzle`` names a nozzle -- "A1" is the rear one, "H1" the
    front, and an 8-channel Flex anchors on those two only. It defaults to
    ``"H1"`` (see ``_DEFAULT_SINGLE_NOZZLE``).

    ``well`` is optional. Omitted, the next tip is chosen from tip tracking in
    the anchor's consumption order (H1 back-to-front, A1 front-to-back) -- the
    way the Opentrons engine drives a single-nozzle rack, so a caller who just
    wants "a tip" never types a well/anchor pair that collides. Given, that exact
    tip is cherry-picked. Either way it is refused before any wire command if the
    anchor cannot reach the slot, if it would take more than one tip, or if its
    channel already holds one. Then stage -> validate -> wire -> verify ->
    commit/rollback, as in ``pick_up_tips``.
    """
    if primary_nozzle not in _SINGLE_NOZZLES:
      raise ValueError(
        f"primary_nozzle={primary_nozzle!r}: an 8-channel Flex can anchor a "
        f"single-nozzle layout only on {' or '.join(_SINGLE_NOZZLES)}."
      )
    if well is None:
      well = self._next_single_tip(tip_rack, primary_nozzle)
    self._validate_single_anchor(tip_rack, well, primary_nozzle)
    # The anchor only settles that the pipette stays inside the robot. The 7
    # idle nozzles still hang over the neighbouring slot, which is a crash.
    slot = self.flex.deck.get_slot(tip_rack)
    if slot is not None:
      self.flex.deck.check_single_nozzle_clearance(slot, primary_nozzle)
    channel = _SINGLE_NOZZLES[primary_nozzle]
    if self._channel_tips[channel] is not None:
      raise OpentronsError(
        "HasTipError",
        f"Channel {channel} already holds a tip; drop it before picking up another.",
      )

    await self._ensure_single_mode(primary_nozzle)

    labware_id = await self.flex._ensure_labware_loaded(tip_rack)
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well,
    }
    well_location = self._well_location([offset], [None], origin="top")
    if well_location is not None:
      params["wellLocation"] = well_location

    spot = tip_rack.get_item(well)
    tip = spot.get_tip()
    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    if tracking and not spot.tracker.is_disabled:
      spot.tracker.remove_tip()  # commit=False: stages + validates
      staged_trackers.append(spot.tracker)

    await self._execute_pickup("pickUpTip", params, staged_trackers)
    self._channel_tips[channel] = tip
    await self._retract_to_traversal_height()

  @instrument_operation
  async def aspirate_single(
    self,
    plate: Plate,
    well: str,
    volume: float,
    flow_rate: Optional[float] = None,
  ) -> None:
    """Aspirate a single well with the currently mounted single tip.

    Follows stage -> validate -> wire -> commit/rollback for the well
    tracker, same as the column ``aspirate``, and leaves any plunger priming
    to the robot for the same reason.
    """
    await self._ensure_anchored_on_mounted_channel()
    self._require_reach_in_single_layout(plate, well)
    staged_trackers = self._container_trackers(plate.get_item(well))
    await self._pipette(
      "aspirate", plate.get_item(well), volume, flow_rate, None, None, staged_trackers
    )

  @instrument_operation
  async def dispense_single(
    self,
    plate: Plate,
    well: str,
    volume: float,
    flow_rate: Optional[float] = None,
  ) -> None:
    """Dispense to a single well with the currently mounted single tip."""
    await self._ensure_anchored_on_mounted_channel()
    self._require_reach_in_single_layout(plate, well)
    staged_trackers = self._container_trackers(plate.get_item(well))
    await self._pipette(
      "dispense", plate.get_item(well), volume, flow_rate, None, None, staged_trackers
    )

  @instrument_operation
  async def drop_single_tip(self, trash: Trash) -> None:
    """Drop the single mounted tip to trash and restore ALL nozzle mode.

    After the wire drop + ``_channel_tips`` update, ``_confirm_tips_cleared()``
    checks the hardware tip-presence sensor and logs a warning (does not
    raise) if it still reports a tip.
    """
    channel = self._active_single_channel()
    await self._ensure_anchored_on_mounted_channel()
    await self._execute_trash_drop(trash)
    self._channel_tips[channel] = None
    await self._confirm_tips_cleared()
    await self._retract_to_traversal_height()
    await self._ensure_all_mode()


class FlexHead96(_FlexHead):
  """96-channel pipette head, whole-plate-addressed (anchor-well fan-out).

  All 96 nozzles are physically fixed -- there is no partial/single-tip
  mode, unlike ``FlexHead8``. Liquid operations use PLR coordinates; tip
  commands are anchored at well "A1". The Flex hardware fans each plunger command
  out to all 96 physical nozzles. Tip/volume trackers are committed only for
  the channels/wells that actually held a tip (None-skip), same as
  ``FlexHead8``'s column ops. ``_channel_tips`` has length 96, index i
  corresponding to ``plate.get_all_items()[i]`` / ``tip_rack.get_all_items()[i]``
  (PLR's column-major A1, B1, ..., H1, A2, ... order).

  Reuses the ``_FlexHead`` base's transactional stage -> wire -> verify ->
  commit/rollback flow and hardware tip-presence verification -- the same
  machinery ``FlexHead8`` uses for its column ops, applied to the whole
  plate/rack instead of one column.

  Liquid probing (``liquid_probe``/``try_liquid_probe``) is not implemented
  on this head -- only the mount heads (``FlexHead1``/``FlexHead8``)
  expose it.

  Coded but **not yet verified on real 96-channel Flex hardware** --
  Vincent's bench Flex carries an 8-channel pipette, not a 96-channel head.
  Every op logs the one-time untested-hardware notice the first time an
  instance issues it, and this docstring makes no "validated on hardware" claim.
  """

  # The Flex API's anchor well for 96-channel ALL-mode whole-plate ops; the
  # hardware fans a single command out to all 96 physical nozzles from here.
  _ANCHOR_WELL_NAME = "A1"

  def _check_full_coverage(self, itemized: ItemizedResource) -> List[Any]:
    """Return ``itemized``'s 96 items, asserting it matches this head's channel count."""
    items = itemized.get_all_items()
    if len(items) != self.channels:
      raise OpentronsError(
        "Labware size mismatch",
        f"'{itemized.name}' has {len(items)} positions; FlexHead96 addresses {self.channels}.",
      )
    return items

  @instrument_operation
  async def pick_up_tips(
    self,
    tip_rack: TipRack,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up all 96 tips: ``configureNozzleLayout`` (ALL) then ONE ``pickUpTip``.

    Anchored at well "A1"; the hardware fans the pickup motion out to all 96
    physical nozzles. Follows stage -> validate -> wire -> verify ->
    commit/rollback, same as ``FlexHead8.pick_up_tips``: tip trackers are
    staged (``commit=False``) BEFORE the wire command -- so an
    already-occupied channel or an invalid tracker state raises before any
    hardware motion -- then, after the wire command succeeds, the hardware
    tip-presence sensor is checked (``_verify_tips_seated()``); trackers and
    ``_channel_tips`` are committed only if that verification passes, and
    rolled back (with no ``_channel_tips`` mutation) if the sensor reports a
    missed pickup. Only spots that actually had a tip are staged
    (None-skip).
    """
    warnings.warn("FlexHead96.pick_up_tips has not been tested on hardware.", stacklevel=2)
    spots = self._check_full_coverage(tip_rack)

    for i, spot in enumerate(spots):
      if spot.has_tip() and self._channel_tips[i] is not None:
        raise OpentronsError(
          "HasTipError",
          f"Channel {i} already holds a tip; drop it before picking up another.",
        )

    await self._configure_nozzle_layout({"style": "ALL"})

    labware_id = await self.flex._ensure_labware_loaded(tip_rack)
    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    tips: List[Optional[Tip]] = [None] * len(spots)
    for i, spot in enumerate(spots):
      if not spot.has_tip():
        continue
      tips[i] = spot.get_tip()
      if tracking and not spot.tracker.is_disabled:
        spot.tracker.remove_tip()  # commit=False: stages + validates
        staged_trackers.append(spot.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": self._ANCHOR_WELL_NAME,
    }
    well_location = self._well_location([offset], [None], origin="top")
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_pickup("pickUpTip", params, staged_trackers)
    for i, tip in enumerate(tips):
      self._channel_tips[i] = tip
    await self._retract_to_traversal_height()

  @instrument_operation
  async def drop_tips(
    self,
    target: Union[TipRack, Trash],
  ) -> None:
    """Drop all 96 tips -- one wire command, fanned to 96 channels.

    A ``TipRack`` target returns tips (one ``dropTip`` command anchored at
    "A1"); a ``Trash`` target discards via the addressable-area drop
    sequence. Tip trackers are committed only for channels that actually
    held a tip (None-skip); trash drops never return tips to a rack
    tracker. After the wire drop + tracker commit, ``_confirm_tips_cleared()``
    checks the hardware tip-presence sensor and logs a warning (does not
    raise) if it still reports a tip.
    """
    warnings.warn("FlexHead96.drop_tips has not been tested on hardware.", stacklevel=2)

    if isinstance(target, Trash):
      await self._execute_trash_drop(target)
      self._channel_tips = [None] * self.channels
      await self._confirm_tips_cleared()
      await self._retract_to_traversal_height()
      return

    spots = self._check_full_coverage(target)
    labware_id = await self.flex._ensure_labware_loaded(target)

    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    for i, spot in enumerate(spots):
      tip = self._channel_tips[i]
      if tip is not None and tracking and not spot.tracker.is_disabled:
        spot.tracker.add_tip(tip, commit=False)  # stages + validates (HasTipError if occupied)
        staged_trackers.append(spot.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": self._ANCHOR_WELL_NAME,
    }

    await self._execute_liquid_op("dropTip", params, staged_trackers)
    for i in range(len(spots)):
      self._channel_tips[i] = None
    await self._confirm_tips_cleared()
    await self._retract_to_traversal_height()

  @instrument_operation
  async def discard_tips(self, trash: Trash) -> None:
    """Discard the mounted 96 tips into the trash."""
    await self.drop_tips(trash)

  @instrument_operation
  async def aspirate(
    self,
    target: Union[Plate, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate a whole plate or one single-cavity container using PLR coordinates and in-place aspiration.

    A ``Plate`` (which must have exactly 96 positions) is anchored at its
    "A1" well and covered one-to-one, staging ``remove_liquid`` per well
    whose channel holds a tip. A bare ``Container`` (trough/reservoir) is
    positioned with the nozzle grid centered in the PLR cavity and its
    single tracker staged with the total.
    Either way a mounted tip is required and every check runs before any wire
    command. The head primes at traversal height, descends using PLR
    coordinates, aspirates in place, and retracts vertically.
    """
    warnings.warn("FlexHead96.aspirate has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    if isinstance(target, Plate):
      anchor: Container = target.get_item(self._ANCHOR_WELL_NAME)
      staged_trackers = self._well_trackers(self._check_full_coverage(target))
    else:
      self._require_span_fits_container(
        target, _NINETY_SIX_HEAD_X_SPAN, _NINETY_SIX_HEAD_Y_SPAN, offset
      )
      anchor = target
      staged_trackers = self._container_trackers(target)
    await self._pipette(
      "aspirate", anchor, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  @instrument_operation
  async def dispense(
    self,
    target: Union[Plate, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense to a whole plate or one single-cavity container using PLR coordinates and in-place dispensing.

    Mirrors ``aspirate``: same addressing, same pre-wire guards, with
    ``add_liquid`` staged per tip-holding channel for a plate and the total
    staged against a container's single tracker.
    """
    warnings.warn("FlexHead96.dispense has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    if isinstance(target, Plate):
      anchor: Container = target.get_item(self._ANCHOR_WELL_NAME)
      staged_trackers = self._well_trackers(self._check_full_coverage(target))
    else:
      self._require_span_fits_container(
        target, _NINETY_SIX_HEAD_X_SPAN, _NINETY_SIX_HEAD_Y_SPAN, offset
      )
      anchor = target
      staged_trackers = self._container_trackers(target)
    await self._pipette(
      "dispense", anchor, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  @instrument_operation
  async def touch_tip(
    self,
    plate: Plate,
    radius: float = 1.0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Touch the mounted tips to their well walls -- one ``touchTip`` command
    anchored at "A1", fanned to all 96 channels.

    ``radius`` and ``offset`` behave as in ``FlexHead1.touch_tip``. Requires
    a mounted tip and a 96-position plate, both checked before any wire
    command. No trackers are involved.
    """
    warnings.warn("FlexHead96.touch_tip has not been tested on hardware.", stacklevel=2)
    self._require_mounted_tip()
    self._check_full_coverage(plate)
    labware_id = await self.flex._ensure_labware_loaded(plate)
    await self.flex._execute_command(
      "touchTip", self._touch_tip_params(labware_id, self._ANCHOR_WELL_NAME, radius, offset)
    )

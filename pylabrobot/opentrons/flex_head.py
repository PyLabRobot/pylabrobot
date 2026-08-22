"""Head sub-objects for :class:`~pylabrobot.opentrons.flex.OpentronsFlex`.

Each head is a plain-class sub-object (EL406/Cytation5 idiom), not a
Capability/CapabilityBackend split: it holds a back-reference to the owning
``OpentronsFlex`` device and issues commands through the shared transport via
``self.flex._execute_command``. Deck-scoped labware loading stays on
``OpentronsFlex`` (heads call ``self.flex._ensure_labware_loaded(...)``); only
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
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union, cast

from pylabrobot.opentrons.checks import traversal_z
from pylabrobot.opentrons.flex_wire import UNTESTED_HARDWARE_WARNING
from pylabrobot.opentrons.labware_definitions import container_footprint
from pylabrobot.opentrons.pipette_defaults import FlowRates, flow_rates
from pylabrobot.opentrons.robot import OpentronsCommandError, OpentronsError
from pylabrobot.resources import (
  Container,
  Plate,
  TipRack,
  TipSpot,
  Trash,
  VolumeTracker,
  Well,
  does_tip_tracking,
  does_volume_tracking,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip

if TYPE_CHECKING:
  from pylabrobot.opentrons.flex import OpentronsFlex

logger = logging.getLogger(__name__)


class _FlexHead:
  """Base class for a mount- (or 96-head-) addressed pipette on an ``OpentronsFlex``.

  Subclasses implement the liquid-handling ops appropriate to their channel
  count. This base holds the shared plumbing: the back-reference to the
  owning device, per-channel tip telemetry, and the well/labware helpers ops
  need to build robot-server command params.
  """

  # Op names confirmed on real Flex hardware; every op outside this set
  # triggers the one-time untested-hardware notice.
  _HARDWARE_VERIFIED_OPS: FrozenSet[str] = frozenset()

  def __init__(
    self,
    flex: "OpentronsFlex",
    mount: str,
    pipette_id: str,
    channels: int,
    pipette_model: str,
    max_volume: float,
  ) -> None:
    self.flex = flex
    self.mount = mount
    self.pipette_id = pipette_id
    self.channels = channels
    self.pipette_model = pipette_model
    # The pipette's own capacity, not the mounted tip's. Carried here so a caller
    # describing the head does not have to re-read /instruments to get it.
    self.max_volume = max_volume
    self._channel_tips: List[Optional[Tip]] = [None] * channels
    self._untested_hardware_warned: bool = False
    # The labware id the pipette last pipetted over, or None when its position is
    # unknown (start of run, after a jog or a trash drop). Used to arc high only
    # when a pipetting move crosses to a different slot -- see _travel_guard.
    self._current_labware_id: Optional[str] = None

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
    await self._execute(
      "moveToWell",
      {
        "pipetteId": self.pipette_id,
        "labwareId": labware_id,
        "wellName": well_name,
        "wellLocation": {"origin": "top", "offset": {"x": 0, "y": 0, "z": 0}},
        "minimumZHeight": self._traversal_height(),
      },
    )
    self._current_labware_id = labware_id

  def _warn_untested_hardware(self, op: str) -> None:
    """Log a one-time notice when an op has no real-hardware verification.

    Coverage is op-scoped: ops in ``_HARDWARE_VERIFIED_OPS`` never log; the
    first op outside that set logs once per instance.
    """
    if op in self._HARDWARE_VERIFIED_OPS or self._untested_hardware_warned:
      return
    self._untested_hardware_warned = True
    logger.warning(UNTESTED_HARDWARE_WARNING, type(self).__name__, op)

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

  async def discard_tips(self, trash: Trash) -> None:
    """Discard all mounted tips into ``trash``. Implemented by each head."""
    raise NotImplementedError

  async def blow_out(self, flow_rate: Optional[float] = None) -> None:
    """Blow out at the current position -- one ``blowOutInPlace`` command.

    Pushes the plunger past its dispense-bottom to expel residual liquid
    from the tip(s) wherever the pipette currently is (no well addressing --
    position with a dispense/move first). ``flow_rate`` (uL/s) defaults to
    the dispense default. Blowing out leaves the plunger past its dispense
    bottom, so the next draw needs priming: see ``prepare_to_aspirate``. No
    trackers are involved.
    """
    self._warn_untested_hardware("blow_out")
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().blow_out
    await self._execute("blowOutInPlace", {"pipetteId": self.pipette_id, "flowRate": rate})

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
    result = await self._execute("getTipPresence", {"pipetteId": self.pipette_id})
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
      await self._execute(command_type, params)
    except Exception:
      for tracker in staged_trackers:
        tracker.rollback()
      raise

    try:
      await self._verify_tips_seated()
    except Exception:
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

    Shared by ``dispense``/``dispense_single`` and rack-return ``drop_tips``
    (tip and volume trackers alike -- no hardware sensor check applies to
    these). Trackers must already be staged (``commit=False``) before
    calling this.
    """
    try:
      await self._travel_guard(params)
      await self._execute(command_type, params)
    except Exception:
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
      return await self._execute(command_type, params)
    except OpentronsCommandError as e:
      if e.error_type != _NOT_READY_TO_ASPIRATE:
        raise
      raise OpentronsError("NotReadyToAspirateError", _NOT_PRIMED_REMEDY) from e

  async def prepare_to_aspirate(self) -> None:
    """Move the plunger to where an aspirate starts from ("priming"). Tip OUT of the liquid.

    The plunger sits past its dispense bottom after a dispense that emptied
    the tip, a blow out, or a volume-mode change, and the robot will not draw
    from there. Priming lifts it back, which with the tip submerged draws
    that much of the well in, unmeasured: about 4 uL on a p50 in its normal
    mode, 12 uL in low-volume mode, 80 uL on a p1000. Move the tip above the
    liquid first (``move_to_well`` with a "top" origin) and prime there.

    Usually you do not need this at all. A well-addressed ``aspirate`` primes
    itself, at the well top in open air, then descends and draws. Only the
    in-place draws need it, because they name no well and so the robot cannot
    pick a safe height to prime at. Sending it when the plunger is already in
    place does nothing, so it is safe to send defensively.

    Nothing here tracks whether a prime is pending. The robot owns that flag
    and answers with it on every draw; a copy in this process would go stale
    the first time anything moved the plunger without going through this
    driver.
    """
    self._warn_untested_hardware("prepare_to_aspirate")
    self._require_mounted_tip()
    await self._execute("prepareToAspirate", {"pipetteId": self.pipette_id})

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
    await self._execute(
      "moveToAddressableAreaForDropTip",
      {
        "pipetteId": self.pipette_id,
        "addressableAreaName": self._trash_addressable_area(trash),
        "alternateDropLocation": True,
        "minimumZHeight": self._traversal_height(),
      },
    )
    await self._execute("dropTipInPlace", {"pipetteId": self.pipette_id})
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

  async def _pipette(
    self,
    verb: str,
    labware_id: str,
    well_name: str,
    volume: float,
    flow_rate: Optional[float],
    offset: Optional[Coordinate],
    liquid_height: Optional[float],
    staged_trackers: List[VolumeTracker],
  ) -> None:
    """Send one ``aspirate``/``dispense`` at a named well and settle the trackers.

    ``flow_rate`` defaults to the robot's own for the mounted tip, so it is
    resolved only when the caller left it out (asking with no tip raises).
    Naming the well is what lets the robot prime itself when it needs to, so
    no ``prepareToAspirate`` is sent here: see ``prepare_to_aspirate``.
    """
    if flow_rate is None:
      rates = self.default_flow_rates()
      flow_rate = rates.aspirate if verb == "aspirate" else rates.dispense
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "volume": volume,
      "flowRate": flow_rate,
    }
    well_location = self._well_location([offset], [liquid_height])
    if well_location is not None:
      params["wellLocation"] = well_location
    await self._execute_liquid_op(verb, params, staged_trackers)

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
    await self._execute(
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
    the absent-``z_position`` success path additionally covers transports
    that succeed without a result (e.g. ``ChatterboxTransport``). Other wire
    failures re-raise untranslated.
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

  async def _on_setup(self) -> None:
    """Hook for head-specific post-discovery setup. Default: no-op."""

  async def _on_stop(self) -> None:
    """Hook for head-specific teardown. Default: no-op."""

  async def _execute(self, command_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Issue a robot-server command through the owning device's shared transport."""
    return await self.flex._execute_command(command_type, params)

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

  @staticmethod
  def _stage_container_aspirate(container: Container, total_volume: float) -> List[VolumeTracker]:
    """Stage an aspirate's total volume against a container's single tracker.

    N channels drawing from one cavity share ONE tracker, so the summed
    volume is staged as one ``remove_liquid`` and the tracker appears once in
    the returned list. Staging per channel would orphan the earlier pending
    ops if a later channel's validation raised.
    """
    staged_trackers: List[VolumeTracker] = []
    if does_volume_tracking() and not container.tracker.is_disabled:
      container.tracker.remove_liquid(volume=total_volume)  # stages + validates
      staged_trackers.append(container.tracker)
    return staged_trackers

  @staticmethod
  def _stage_container_dispense(container: Container, total_volume: float) -> List[VolumeTracker]:
    """Stage a dispense's total volume against a container's single tracker.

    Same one-tracker rule as ``_stage_container_aspirate``.
    """
    staged_trackers: List[VolumeTracker] = []
    if does_volume_tracking() and not container.tracker.is_disabled:
      container.tracker.add_liquid(volume=total_volume)  # stages + validates
      staged_trackers.append(container.tracker)
    return staged_trackers

  def _stage_wells_aspirate(self, wells: List[Well], volume: float) -> List[VolumeTracker]:
    """Stage ``remove_liquid`` on each well whose channel holds a tip."""
    staged_trackers: List[VolumeTracker] = []
    if does_volume_tracking():
      for i, well in enumerate(wells):
        if self._channel_tips[i] is not None and not well.tracker.is_disabled:
          well.tracker.remove_liquid(volume=volume)  # stages + validates
          staged_trackers.append(well.tracker)
    return staged_trackers

  def _stage_wells_dispense(self, wells: List[Well], volume: float) -> List[VolumeTracker]:
    """Stage ``add_liquid`` on each well whose channel holds a tip."""
    staged_trackers: List[VolumeTracker] = []
    if does_volume_tracking():
      for i, well in enumerate(wells):
        if self._channel_tips[i] is not None and not well.tracker.is_disabled:
          well.tracker.add_liquid(volume=volume)  # stages + validates
          staged_trackers.append(well.tracker)
    return staged_trackers

  @staticmethod
  def _require_span_fits_container(
    container: Container,
    x_span: float,
    y_span: float,
    offset: Optional[Coordinate],
  ) -> None:
    """Raise pre-wire if the nozzle array would overhang the container.

    The engine centers the array on the cavity and the caller's ``offset``
    shifts it, so the shifted span must still fit on each axis. The footprint
    is the deck-frame one the definition carries, not the container's own
    x/y: a rotated container presents its axes to the robot swapped. It is
    the OUTER box, so an array that fits the shell but not the cavity inside
    passes -- PLR carries no cavity x/y to check against.
    """
    o = offset if offset is not None else Coordinate.zero()
    cavity_x, cavity_y = container_footprint(container)
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

  def _traversal_height(self) -> float:
    """The computed tip-safe travel plane for a lateral jog over this deck.

    ``max(labware tops) + arc margin`` (``checks.traversal_z``), tip-end framed
    (the frame ``minimumZHeight`` already uses), computed from the resource model
    -- so the arc adapts to what is actually on the deck instead of a fixed magic
    number that is both wasteful over short labware and unsafe under anything
    taller.
    """
    return traversal_z(self.flex.deck)

  async def position(self) -> Coordinate:
    """The head's current deck-frame position -- one ``savePosition`` query.

    Reports the pipette's critical point: the bottom of the mounted tip, or
    the nozzle when no tip is mounted. The Flex's robot frame coincides with
    the deck frame, so the reported position needs no conversion.
    """
    self._warn_untested_hardware("position")
    result = await self._execute("savePosition", {"pipetteId": self.pipette_id})
    pos = result["result"]["position"]
    return Coordinate(pos["x"], pos["y"], pos["z"])

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
    self._warn_untested_hardware("move_to")
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
        minimum_z_height if minimum_z_height is not None else self._traversal_height()
      ),
    }
    if speed is not None:
      params["speed"] = speed
    await self._execute("moveToCoordinates", params)
    # A raw jog leaves the pipette at an arbitrary point: the next pipetting move
    # can no longer assume it is over its last labware, so make it arc high.
    self._current_labware_id = None

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
    self._warn_untested_hardware("move_to_well")
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
        minimum_z_height if minimum_z_height is not None else self._traversal_height()
      ),
    }
    if speed is not None:
      params["speed"] = speed
    await self._execute("moveToWell", params)

  async def move_relative(self, axis: str, distance: float) -> None:
    """Jog one axis by ``distance`` mm from wherever the head is now.

    ``axis`` is "x", "y" or "z". A negative distance moves the other way.
    Relative to the head's current position, so unlike :meth:`move_to` it
    needs no reading first.
    """
    self._warn_untested_hardware("move_relative")
    if axis not in _MOVE_AXES:
      raise ValueError(f"axis must be one of {sorted(_MOVE_AXES)}, got {axis!r}")
    await self._execute(
      "moveRelative",
      {"pipetteId": self.pipette_id, "axis": axis, "distance": distance},
    )

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
    self._warn_untested_hardware("move_to_addressable_area")
    o = offset or Coordinate(0, 0, 0)
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "addressableAreaName": addressable_area_name,
      "offset": {"x": o.x, "y": o.y, "z": o.z},
      "stayAtHighestPossibleZ": stay_at_max_height,
      "minimumZHeight": (
        minimum_z_height if minimum_z_height is not None else self._traversal_height()
      ),
    }
    if speed is not None:
      params["speed"] = speed
    await self._execute("moveToAddressableArea", params)

  # --- In-place pipetting (acts where the head already is) ---

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
    self._warn_untested_hardware("aspirate_in_place")
    self._require_mounted_tip()
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().aspirate
    await self._execute_draw(
      "aspirateInPlace",
      {"pipetteId": self.pipette_id, "volume": volume, "flowRate": rate},
    )

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
    self._warn_untested_hardware("dispense_in_place")
    self._require_mounted_tip()
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().dispense
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "volume": volume,
      "flowRate": rate,
    }
    if push_out is not None:
      params["pushOut"] = push_out
    await self._execute("dispenseInPlace", params)

  async def air_gap_in_place(self, volume: float, flow_rate: Optional[float] = None) -> None:
    """Draw a ``volume`` uL air gap where the head already is -- one ``airGapInPlace`` command.

    The same plunger motion as ``aspirate_in_place``, but the robot books the
    volume as air, so park the tip above the liquid first. ``flow_rate``
    (uL/s) defaults to the aspirate default. Refuses an unprimed plunger the
    same way ``aspirate_in_place`` does. No tracker is involved.
    """
    self._warn_untested_hardware("air_gap_in_place")
    self._require_mounted_tip()
    rate = flow_rate if flow_rate is not None else self.default_flow_rates().aspirate
    await self._execute_draw(
      "airGapInPlace",
      {"pipetteId": self.pipette_id, "volume": volume, "flowRate": rate},
    )

  # --- Tip-presence sensor (command form) ---

  async def get_tip_presence(self) -> Optional[str]:
    """Read this head's tip sensor: "present", "absent" or "unknown".

    One reading per pipette, not per channel. ``has_tip_on_hardware()`` is
    the same reading as a bool. ``None`` when the command reports no status.
    """
    self._warn_untested_hardware("get_tip_presence")
    return await self._read_tip_presence()

  async def verify_tip_presence(self, expected_state: str) -> None:
    """Have the robot fail the command unless its tip sensor reads ``expected_state``.

    ``expected_state`` is "present" or "absent". Where ``get_tip_presence``
    reports and leaves the judgement to the caller, this one raises the
    mismatch from the robot side, so it reads as a checkpoint in a sequence.
    """
    self._warn_untested_hardware("verify_tip_presence")
    if expected_state not in _TIP_PRESENCE_STATES:
      raise ValueError(
        f"expected_state must be one of {sorted(_TIP_PRESENCE_STATES)}, got {expected_state!r}"
      )
    await self._execute(
      "verifyTipPresence",
      {"pipetteId": self.pipette_id, "expectedState": expected_state},
    )

  async def configure_for_volume(self, volume: float) -> None:
    """Put the pipette in the volume mode that suits ``volume`` uL.

    A Flex pipette only reaches its stated accuracy at small volumes in its
    low-volume mode, which this picks for the volume given. Call it before
    picking up tips: the robot refuses a mode change while a tip is attached.
    """
    self._warn_untested_hardware("configure_for_volume")
    await self._execute("configureForVolume", {"pipetteId": self.pipette_id, "volume": volume})

  # --- Recovery ops ---

  async def unsafe_drop_tip_in_place(self) -> None:
    """Drop the mounted tip where the head is, skipping the engine's own checks.

    The "unsafe/" commands are the recovery path: they still run once the
    engine has put the run into an error state, where the ordinary
    ``dropTipInPlace`` is refused. The tip falls wherever the head happens to
    be, so move somewhere it can be retrieved from first. Clears this head's
    per-channel tip bookkeeping; no tip tracker is touched, since the tip
    goes back to no rack.
    """
    self._warn_untested_hardware("unsafe_drop_tip_in_place")
    self._require_mounted_tip()
    await self._execute("unsafe/dropTipInPlace", {"pipetteId": self.pipette_id})
    self._channel_tips = [None] * self.channels

  async def unsafe_blow_out_in_place(self, flow_rate: float) -> None:
    """Blow out where the head is, skipping the engine's own checks.

    The recovery counterpart to ``blow_out`` (see ``unsafe_drop_tip_in_place``
    for what "unsafe/" buys). ``flow_rate`` is in uL/s and has no default
    here, the recovery path being an explicit one. Leaves the plunger past
    its dispense bottom, so the next draw needs priming.
    """
    self._warn_untested_hardware("unsafe_blow_out_in_place")
    self._require_mounted_tip()
    await self._execute(
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

# What verify_tip_presence can assert. The sensor itself can also read
# "unknown", but that is a reading, not something to check against.
_TIP_PRESENCE_STATES = frozenset({"present", "absent"})

# The 8-channel head's rows front-to-back: channel 0 = "A" (rearmost) .. 7 = "H"
# (frontmost). Used to name the corner nozzles of a partial (QUADRANT) column.
_ROW_LETTERS = "ABCDEFGH"

# The only nozzles an 8-channel Flex can anchor a SINGLE layout on ("A1" is
# the rearmost, "H1" the frontmost), mapped to the channel each one is.
_SINGLE_NOZZLES = {"A1": 0, "H1": 7}
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

  Every op sends exactly ONE robot-server command naming the single well
  (tip spot or well) it addresses -- no anchor-well fan-out, no nozzle
  layout (there is only ever one physical nozzle). ``_channel_tips`` has
  length 1; the sole channel is index 0.

  Reuses the ``_FlexHead`` base's transactional stage -> wire -> verify ->
  commit/rollback flow and hardware tip-presence verification
  (``_verify_tips_seated``/``_confirm_tips_cleared``) -- the same machinery
  ``FlexHead8`` uses for its column ops, applied to a single well instead of
  a column.

  Verified on a real single-channel Flex (p50, robot-server API 9.1.1):
  motion, tip pickup and drop, liquid probe, aspirate/dispense and volume
  mode, all against the hardware tip-presence sensor. Ops outside
  ``_HARDWARE_VERIFIED_OPS`` still log the one-time untested-hardware notice.
  """

  # Confirmed on a p50 single channel. Base-class ops are listed here rather
  # than on _FlexHead because the other heads have not been run on hardware.
  _HARDWARE_VERIFIED_OPS: FrozenSet[str] = frozenset(
    {
      "air_gap_in_place",
      "aspirate",
      "aspirate_in_place",
      "configure_for_volume",
      "dispense",
      "dispense_in_place",
      "drop_tips",
      "get_tip_presence",
      "liquid_probe",
      "move_relative",
      "move_to",
      "move_to_addressable_area",
      "move_to_well",
      "pick_up_tips",
      "position",
      "try_liquid_probe",
      "verify_tip_presence",
    }
  )

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
    self._warn_untested_hardware("pick_up_tips")
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
    self._warn_untested_hardware("drop_tips")

    if isinstance(target, Trash):
      await self._execute_trash_drop(target)
      self._channel_tips[0] = None
      await self._confirm_tips_cleared()
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

  async def discard_tips(self, trash: Trash) -> None:
    """Discard the mounted tip into the trash."""
    await self.drop_tips(trash)

  async def aspirate(
    self,
    target: Union[Well, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate from a well or single-cavity container -- one ``aspirate`` command.

    A ``Well`` is addressed through its plate parent by well name; a bare
    ``Container`` (trough/reservoir) at its own sole well. Requires a mounted
    tip. Follows stage -> validate -> wire -> commit/rollback: the tracker
    (``remove_liquid``) is staged BEFORE the wire command, so an infeasible
    aspirate raises before any hardware motion. Naming the well means the
    robot primes the plunger itself when it needs to, at the well top and
    then descending, so nothing here has to.
    """
    self._warn_untested_hardware("aspirate")
    self._require_mounted_tip()
    labware_id, well_name = await self._well_target(target)
    staged_trackers = self._stage_container_aspirate(target, volume)
    await self._pipette(
      "aspirate", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  async def dispense(
    self,
    target: Union[Well, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense to a well or single-cavity container -- one ``dispense`` command.

    Mirrors ``aspirate``: same addressing, same mounted-tip requirement, and
    stage -> validate -> wire -> commit/rollback with ``add_liquid`` staged
    BEFORE the wire command.
    """
    self._warn_untested_hardware("dispense")
    self._require_mounted_tip()
    labware_id, well_name = await self._well_target(target)
    staged_trackers = self._stage_container_dispense(target, volume)
    await self._pipette(
      "dispense", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

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
    self._warn_untested_hardware("touch_tip")
    self._require_mounted_tip()
    labware_id, well_name = await self._well_target(well)
    await self._execute("touchTip", self._touch_tip_params(labware_id, well_name, radius, offset))

  async def liquid_probe(self, well: Well) -> float:
    """Probe downward in ``well`` until the pressure sensor detects liquid; return its z (mm).

    One ``liquidProbe`` command naming ``well``. Requires a mounted tip
    (checked before any wire command). Raises ``OpentronsError`` if no
    liquid is found; use ``try_liquid_probe`` for the non-raising variant.
    """
    self._warn_untested_hardware("liquid_probe")
    self._require_mounted_tip()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    return await self._liquid_probe_z(labware_id, well_name, f"well {well.name!r}")

  async def try_liquid_probe(self, well: Well) -> Optional[float]:
    """Like ``liquid_probe`` but return ``None`` instead of raising when no liquid is found."""
    self._warn_untested_hardware("try_liquid_probe")
    self._require_mounted_tip()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    return await self._probe_z("tryLiquidProbe", labware_id, well_name)


class FlexHead8(_FlexHead):
  """8-channel pipette head, column-addressed (anchor-well fan-out).

  Every op sends exactly ONE robot-server command anchored at the rearmost
  well the nozzle row covers (on a 96-format plate, column 2 -> wellName
  "A3"); the Flex hardware fans that single command out to all 8 physical
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

  Verified on real 8-channel Flex hardware (Opentrons Flex, robot-server
  API 8.8) by the ``docs/user_guide/opentrons/flex/hello-world.ipynb`` and
  ``use_channels_smoke.ipynb`` runs: setup and homing; tip pickup in
  full-column (ALL), single-nozzle (SINGLE, H1), and partial-column (QUADRANT,
  front four) layouts, and tip drop in the full-column and single-nozzle
  layouts, confirmed against the hardware tip-presence sensor; and
  aspirate/dispense into a plate in the full-column and single-nozzle layouts.
  Ops outside that set -- container/reservoir ops, touch_tip, liquid_probe, and
  the motion surface -- are coded but not yet hardware-verified and log the
  one-time untested-hardware notice, same as the other heads.
  """

  _HARDWARE_VERIFIED_OPS: FrozenSet[str] = frozenset(
    {
      "pick_up_tips",
      "pick_up_single_tip",
      "pick_up_partial",
      "drop_tips",
      "drop_single_tip",
      "aspirate",
      "dispense",
    }
  )

  def __init__(
    self,
    flex: "OpentronsFlex",
    mount: str,
    pipette_id: str,
    channels: int,
    pipette_model: str,
    max_volume: float,
  ) -> None:
    super().__init__(flex, mount, pipette_id, channels, pipette_model, max_volume)
    self._nozzle_layout: str = "ALL"  # "ALL" | "SINGLE"

  # --- Nozzle layout guard ---

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
      cast(TipRack, parent), well_name, offset=offset, primary_nozzle=primary_nozzle
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
    self._warn_untested_hardware("pick_up_partial")
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
    self._nozzle_layout = "PARTIAL"  # non-ALL: a later column op resets via _ensure_all_mode

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
    self._warn_untested_hardware("pick_up_tips")

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
    self._warn_untested_hardware("drop_tips")

    if isinstance(target, Trash):
      await self._execute_trash_drop(target)
      self._channel_tips = [None] * self.channels
      await self._confirm_tips_cleared()
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
    """Aspirate ``volume`` uL from ``target`` -- one ``aspirate`` command.

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

  def _require_single_nozzle_clearance(self, labware: ItemizedResource) -> None:
    """Refuse a single-nozzle liquid op whose idle nozzles would overhang the
    adjacent slot's labware.

    In SINGLE layout the 7 idle nozzles trail off one end of the head, so a
    liquid op over a slot with a tip rack (or taller labware) in the trailing
    slot would drive them into it. The engine does not check this for a raw
    command, so it is refused here -- the same clearance guard
    ``pick_up_single_tip`` runs at pickup, applied to the liquid op.
    """
    nozzle = _SINGLE_NOZZLE_BY_CHANNEL.get(self._active_single_channel())
    slot = self.flex.deck.get_slot(labware)
    if nozzle is not None and slot is not None:
      self.flex.deck.check_single_nozzle_clearance(slot, nozzle)

  async def _aspirate_single_well(
    self,
    well: Well,
    volume: float,
    flow_rate: Optional[float],
    offset: Optional[Coordinate],
    liquid_height: Optional[float],
  ) -> None:
    """Aspirate one well with the mounted single nozzle -- one ``aspirate`` at it.

    Requires exactly one mounted tip (a SINGLE-layout cherry-pick); refuses a
    well the mounted anchor cannot reach, and a target whose idle nozzles would
    overhang the adjacent slot's labware -- all before any wire command. The
    well's own tracker is staged with ``volume``.
    """
    self._warn_untested_hardware("aspirate")
    self._active_single_channel()
    parent = self._require_itemized_parent(well)
    well_name = parent.get_child_identifier(well)
    self._require_reach_in_single_layout(parent, well_name)
    self._require_single_nozzle_clearance(parent)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    staged_trackers = self._stage_container_aspirate(well, volume)
    await self._pipette(
      "aspirate", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

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
    self._warn_untested_hardware("aspirate")
    self._require_mounted_tip()
    if not wells:
      raise ValueError("aspirate: the target well sequence is empty.")
    parent = self._require_itemized_parent(wells[0])
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(parent)
    anchor = parent.get_child_identifier(wells[0])
    staged_trackers = self._stage_wells_aspirate(wells, volume)
    await self._pipette(
      "aspirate", labware_id, anchor, volume, flow_rate, offset, liquid_height, staged_trackers
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
    """Aspirate a column -- one ``aspirate`` command anchored at its rearmost well.

    Requires at least one mounted tip. Follows stage -> validate -> wire ->
    commit/rollback: ``Well.tracker`` (``remove_liquid``) is staged for
    every well whose channel actually holds a tip (None-skip; wells outside
    ``column`` are never touched -- the Case-1 regression guard) BEFORE the
    wire command, so an infeasible aspirate (e.g. ``TooLittleLiquidError``)
    raises before any hardware motion. Naming the wells means the robot
    primes the plunger itself when it needs to, at the well top and then
    descending, so nothing here has to.
    """
    self._warn_untested_hardware("aspirate")
    self._require_mounted_tip()
    well_name, column_wells = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    staged_trackers = self._stage_wells_aspirate(column_wells, volume)
    await self._pipette(
      "aspirate", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

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
    """Dispense ``volume`` uL into ``target`` -- one ``dispense`` command.

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
    self._warn_untested_hardware("dispense")
    self._active_single_channel()
    parent = self._require_itemized_parent(well)
    well_name = parent.get_child_identifier(well)
    self._require_reach_in_single_layout(parent, well_name)
    self._require_single_nozzle_clearance(parent)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    staged_trackers = self._stage_container_dispense(well, volume)
    await self._pipette(
      "dispense", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

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
    self._warn_untested_hardware("dispense")
    self._require_mounted_tip()
    if not wells:
      raise ValueError("dispense: the target well sequence is empty.")
    parent = self._require_itemized_parent(wells[0])
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(parent)
    anchor = parent.get_child_identifier(wells[0])
    staged_trackers = self._stage_wells_dispense(wells, volume)
    await self._pipette(
      "dispense", labware_id, anchor, volume, flow_rate, offset, liquid_height, staged_trackers
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
    """Dispense a column -- one ``dispense`` command anchored at its rearmost well.

    Requires at least one mounted tip. Follows stage -> validate -> wire ->
    commit/rollback: ``Well.tracker`` (``add_liquid``) is staged for every
    well whose channel actually holds a tip (None-skip) BEFORE the wire
    command, so an infeasible dispense (e.g. ``TooLittleVolumeError``)
    raises before any hardware motion.
    """
    self._warn_untested_hardware("dispense")
    self._require_mounted_tip()
    well_name, column_wells = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    staged_trackers = self._stage_wells_dispense(column_wells, volume)
    await self._pipette(
      "dispense", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  # --- Single-cavity container (trough/reservoir) liquid handling ---

  async def aspirate_container(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate ``volume`` uL per channel from one single-cavity container.

    All 8 nozzles dip into the same cavity (trough/reservoir) and ONE
    ``aspirate`` command names its sole well. The engine centers the nozzle
    row in the cavity itself, so only the caller's ``offset``/
    ``liquid_height`` ride the wire. Requires a mounted tip, a cavity the
    offset-shifted row still fits, and ALL nozzle mode. Each channel holding
    a tip draws ``volume``, so the container's single tracker is staged with
    the total and settled as one op.
    """
    self._warn_untested_hardware("aspirate_container")
    self._require_mounted_tip()
    self._require_span_fits_container(container, 0.0, _EIGHT_CHANNEL_Y_SPAN, offset)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(container)
    staged_trackers = self._stage_container_aspirate(container, volume * self._mounted_count())
    await self._pipette(
      "aspirate",
      labware_id,
      _CONTAINER_WELL_NAME,
      volume,
      flow_rate,
      offset,
      liquid_height,
      staged_trackers,
    )

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
    self._warn_untested_hardware("dispense_container")
    self._require_mounted_tip()
    self._require_span_fits_container(container, 0.0, _EIGHT_CHANNEL_Y_SPAN, offset)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(container)
    staged_trackers = self._stage_container_dispense(container, volume * self._mounted_count())
    await self._pipette(
      "dispense",
      labware_id,
      _CONTAINER_WELL_NAME,
      volume,
      flow_rate,
      offset,
      liquid_height,
      staged_trackers,
    )

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
    self._warn_untested_hardware("touch_tip")
    self._require_mounted_tip()
    well_name, _ = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    await self._execute("touchTip", self._touch_tip_params(labware_id, well_name, radius, offset))

  async def liquid_probe(self, plate: Plate, column: int) -> float:
    """Probe for liquid in a column -- one ``liquidProbe`` command anchored at
    its rearmost well; return the found liquid z (mm).

    Requires at least one mounted tip and a valid column (both checked
    before any wire command) and ALL nozzle mode (reset first if a
    single-tip op left the layout otherwise). Raises ``OpentronsError`` if
    no liquid is found; use ``try_liquid_probe`` for the non-raising
    variant.
    """
    self._warn_untested_hardware("liquid_probe")
    self._require_mounted_tip()
    well_name, _ = self._column_anchor_and_items(plate, column)
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    return await self._liquid_probe_z(labware_id, well_name, f"column {column} of {plate.name!r}")

  async def try_liquid_probe(self, plate: Plate, column: int) -> Optional[float]:
    """Like ``liquid_probe`` but return ``None`` instead of raising when no liquid is found."""
    self._warn_untested_hardware("try_liquid_probe")
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
  def _anchor_for(cls, labware: ItemizedResource, well: str, primary_nozzle: Optional[str]) -> str:
    """Settle which nozzle a single-tip op anchors on, refusing what cannot reach.

    A caller's explicit choice is never silently swapped -- an unreachable one
    is refused, naming the nozzle that would work. Left to us, the well's own
    row wins when it can reach (least surprise: the tip lands on the channel
    whose row was asked for), otherwise whichever end reaches.
    """
    reachable = cls.reachable_single_nozzles(labware, well)
    if primary_nozzle is not None:
      if primary_nozzle not in _SINGLE_NOZZLES:
        raise ValueError(
          f"primary_nozzle={primary_nozzle!r}: an 8-channel Flex can anchor a single-nozzle "
          f"layout only on {' or '.join(_SINGLE_NOZZLES)}."
        )
      if primary_nozzle not in reachable:
        alternative = (
          f" Anchor on {reachable[0]} instead."
          if reachable
          else " Neither anchor reaches it; move the labware to another slot."
        )
        raise ValueError(
          f"Anchoring the {primary_nozzle} nozzle over '{labware.name}' well '{well}' would "
          f"carry the pipette outside the robot's reach.{alternative}"
        )
      return primary_nozzle
    if not reachable:
      raise ValueError(
        f"'{labware.name}' well '{well}' is out of reach of both single-nozzle anchors "
        f"({' and '.join(_SINGLE_NOZZLES)}); the pipette would leave the robot's extents "
        f"either way. Move the labware to another slot."
      )
    row_nozzle = f"{well[:1].upper()}1"
    return row_nozzle if row_nozzle in reachable else reachable[0]

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

  async def pick_up_single_tip(
    self,
    tip_rack: TipRack,
    well: str,
    offset: Optional[Coordinate] = None,
    primary_nozzle: Optional[str] = None,
  ) -> None:
    """Pick up one tip in SINGLE nozzle mode.

    Switches to SINGLE layout (``configureNozzleLayout``) before the
    ``pickUpTip`` command. In that layout the pipette drives ONE nozzle and
    the engine moves it over whatever well is named, so the nozzle, not the
    well, decides which channel ends up holding the tip. An 8-channel Flex
    can anchor on its "A1" or "H1" nozzle only; ``primary_nozzle`` picks
    between them, and left unset it is the well's own row when that end can
    reach the rack's slot, otherwise the end that can (see
    ``reachable_single_nozzles``). Raises ``OpentronsError`` if that channel
    already holds a tip -- checked, like the nozzle itself, before any wire
    command. Then stage -> validate -> wire -> verify -> commit/rollback, as
    in ``pick_up_tips``.
    """
    self._warn_untested_hardware("pick_up_single_tip")
    primary_nozzle = self._anchor_for(tip_rack, well, primary_nozzle)
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

    await self._configure_nozzle_layout({"style": "SINGLE", "primaryNozzle": primary_nozzle})
    self._nozzle_layout = "SINGLE"

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
    self._warn_untested_hardware("aspirate_single")
    self._active_single_channel()
    self._require_reach_in_single_layout(plate, well)
    self._require_single_nozzle_clearance(plate)
    labware_id = await self.flex._ensure_labware_loaded(plate)
    staged_trackers = self._stage_container_aspirate(plate.get_item(well), volume)
    await self._pipette(
      "aspirate", labware_id, well, volume, flow_rate, None, None, staged_trackers
    )

  async def dispense_single(
    self,
    plate: Plate,
    well: str,
    volume: float,
    flow_rate: Optional[float] = None,
  ) -> None:
    """Dispense to a single well with the currently mounted single tip."""
    self._warn_untested_hardware("dispense_single")
    self._active_single_channel()
    self._require_reach_in_single_layout(plate, well)
    self._require_single_nozzle_clearance(plate)
    labware_id = await self.flex._ensure_labware_loaded(plate)
    staged_trackers = self._stage_container_dispense(plate.get_item(well), volume)
    await self._pipette(
      "dispense", labware_id, well, volume, flow_rate, None, None, staged_trackers
    )

  async def drop_single_tip(self, trash: Trash) -> None:
    """Drop the single mounted tip to trash and restore ALL nozzle mode.

    After the wire drop + ``_channel_tips`` update, ``_confirm_tips_cleared()``
    checks the hardware tip-presence sensor and logs a warning (does not
    raise) if it still reports a tip.
    """
    self._warn_untested_hardware("drop_single_tip")
    channel = self._active_single_channel()
    await self._execute_trash_drop(trash)
    self._channel_tips[channel] = None
    await self._confirm_tips_cleared()
    await self._ensure_all_mode()


class FlexHead96(_FlexHead):
  """96-channel pipette head, whole-plate-addressed (anchor-well fan-out).

  All 96 nozzles are physically fixed -- there is no partial/single-tip
  mode, unlike ``FlexHead8``. Every op sends exactly ONE robot-server
  command anchored at well "A1"; the Flex hardware fans that single command
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
  A one-time ``logger.warning`` fires on the first op issued by an instance,
  and this docstring makes no "validated on hardware" claim.
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
    self._warn_untested_hardware("pick_up_tips")
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
    self._warn_untested_hardware("drop_tips")

    if isinstance(target, Trash):
      await self._execute_trash_drop(target)
      self._channel_tips = [None] * self.channels
      await self._confirm_tips_cleared()
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

  async def discard_tips(self, trash: Trash) -> None:
    """Discard the mounted 96 tips into the trash."""
    await self.drop_tips(trash)

  async def aspirate(
    self,
    target: Union[Plate, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate a whole plate or one single-cavity container -- one ``aspirate`` command.

    A ``Plate`` (which must have exactly 96 positions) is anchored at its
    "A1" well and covered one-to-one, staging ``remove_liquid`` per well
    whose channel holds a tip. A bare ``Container`` (trough/reservoir) is
    addressed at its sole well, with the engine centering the nozzle grid in
    the cavity and the container's single tracker staged with the total.
    Either way a mounted tip is required and every check runs before any wire
    command. Naming the wells means the robot primes the plunger itself when
    it needs to, at the well top and then descending, so nothing here has to.
    """
    self._warn_untested_hardware("aspirate")
    self._require_mounted_tip()
    if isinstance(target, Plate):
      labware_id = await self.flex._ensure_labware_loaded(target)
      well_name = self._ANCHOR_WELL_NAME
      staged_trackers = self._stage_wells_aspirate(self._check_full_coverage(target), volume)
    else:
      self._require_span_fits_container(
        target, _NINETY_SIX_HEAD_X_SPAN, _NINETY_SIX_HEAD_Y_SPAN, offset
      )
      labware_id = await self.flex._ensure_labware_loaded(target)
      well_name = _CONTAINER_WELL_NAME
      staged_trackers = self._stage_container_aspirate(target, volume * self._mounted_count())
    await self._pipette(
      "aspirate", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

  async def dispense(
    self,
    target: Union[Plate, Container],
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense to a whole plate or one single-cavity container -- one ``dispense`` command.

    Mirrors ``aspirate``: same addressing, same pre-wire guards, with
    ``add_liquid`` staged per tip-holding channel for a plate and the total
    staged against a container's single tracker.
    """
    self._warn_untested_hardware("dispense")
    self._require_mounted_tip()
    if isinstance(target, Plate):
      labware_id = await self.flex._ensure_labware_loaded(target)
      well_name = self._ANCHOR_WELL_NAME
      staged_trackers = self._stage_wells_dispense(self._check_full_coverage(target), volume)
    else:
      self._require_span_fits_container(
        target, _NINETY_SIX_HEAD_X_SPAN, _NINETY_SIX_HEAD_Y_SPAN, offset
      )
      labware_id = await self.flex._ensure_labware_loaded(target)
      well_name = _CONTAINER_WELL_NAME
      staged_trackers = self._stage_container_dispense(target, volume * self._mounted_count())
    await self._pipette(
      "dispense", labware_id, well_name, volume, flow_rate, offset, liquid_height, staged_trackers
    )

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
    self._warn_untested_hardware("touch_tip")
    self._require_mounted_tip()
    self._check_full_coverage(plate)
    labware_id = await self.flex._ensure_labware_loaded(plate)
    await self._execute(
      "touchTip", self._touch_tip_params(labware_id, self._ANCHOR_WELL_NAME, radius, offset)
    )

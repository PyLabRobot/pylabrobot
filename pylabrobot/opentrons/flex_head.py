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
stage->wire->verify->commit/rollback flow, hardware tip-presence
verification, and ``prepareToAspirate`` priming are factored onto the
``_FlexHead`` base (``_execute_pickup``/``_execute_liquid_op``/
``_execute_with_prepare``/``_execute_trash_drop``) so ``FlexHead1`` and
``FlexHead96`` reuse the exact machinery ``FlexHead8`` established -- only
the addressing (single well vs. column vs. whole-plate anchor) and nozzle
layout differ per head.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union, cast

from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.resources import (
  Plate,
  TipRack,
  TipSpot,
  Trash,
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

  def __init__(self, flex: "OpentronsFlex", mount: str, pipette_id: str, channels: int) -> None:
    self.flex = flex
    self.mount = mount
    self.pipette_id = pipette_id
    self.channels = channels
    self._channel_tips: List[Optional[Tip]] = [None] * channels
    # Whether the plunger has been prepared (primed) since the last tip
    # pickup. The Flex requires an explicit `prepareToAspirate` command
    # before the FIRST aspirate after a pickup (implicit on the STAR,
    # explicit on the Flex) -- True means no prepare is currently pending.
    self._prepared: bool = True
    self._untested_hardware_warned: bool = False

  def _warn_untested_hardware(self) -> None:
    """Log a one-time notice that this head is not yet verified on real hardware.

    Called by ``FlexHead1``/``FlexHead96`` at the top of every op -- guarded
    so only the FIRST call on a given instance actually logs. ``FlexHead8``
    does not call this (it has its own hardware-verification history); this
    exists specifically for the hardware-unverified heads.
    """
    if self._untested_hardware_warned:
      return
    self._untested_hardware_warned = True
    logger.warning(
      "%s ops are coded but NOT YET VERIFIED on real Opentrons Flex hardware -- "
      "tested only against ChatterboxTransport/simulated transport. Verify behavior "
      "on real hardware before relying on it in a production protocol.",
      type(self).__name__,
    )

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

  async def discard_tips(self, trash: Trash) -> None:
    """Discard all mounted tips into ``trash``. Implemented by each head."""
    raise NotImplementedError

  async def blow_out(self, flow_rate: Optional[float] = None) -> None:
    """Blow out at the current position -- one ``blowOutInPlace`` command.

    Pushes the plunger past its dispense-bottom to expel residual liquid
    from the tip(s) wherever the pipette currently is (no well addressing --
    position with a dispense/move first). ``flow_rate`` (uL/s) defaults to
    the dispense default. Blowing out leaves the plunger at the blow-out
    position, so the next aspirate is preceded by a fresh
    ``prepareToAspirate`` (same priming rule as after a tip pickup). No
    trackers are involved.
    """
    rate = flow_rate if flow_rate is not None else _DEFAULT_BLOW_OUT_FLOW_RATE
    await self._execute("blowOutInPlace", {"pipetteId": self.pipette_id, "flowRate": rate})
    self._prepared = False

  async def has_tip_on_hardware(self) -> Optional[bool]:
    """Query the Flex's hardware tip-presence sensor for THIS head's pipette.

    The Flex reports tip presence as one boolean per pipette (mount), not
    per nozzle/channel: ``GET /instruments`` -> ``data[i].state.tipDetected``.
    This is the aggregate hardware ground truth, used to verify/reconcile
    the per-channel ``_channel_tips`` bookkeeping -- it cannot tell you
    *which* channel(s) hold a tip.

    Returns:
      ``True``/``False`` if a pipette is found on ``self.mount`` and reports
      a tip-detection state, ``None`` if unknown (no ``state`` field) or no
      pipette is found on this mount.
    """
    instruments_data = await self.flex._get_instruments()
    for instrument in instruments_data.get("data", []):
      if instrument.get("instrumentType") != "pipette":
        continue
      if instrument.get("mount") != self.mount:
        continue
      state = instrument.get("state", {})
      return cast(Optional[bool], state.get("tipDetected"))
    return None

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
    ``_channel_tips`` and ``_prepared`` AFTER this returns successfully.
    """
    try:
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
      await self._execute(command_type, params)
    except Exception:
      for tracker in staged_trackers:
        tracker.rollback()
      raise
    else:
      for tracker in staged_trackers:
        tracker.commit()

  async def _execute_with_prepare(
    self,
    command_type: str,
    params: Dict[str, Any],
    staged_trackers: List[Any],
  ) -> None:
    """``prepareToAspirate`` (if pending) -> wire -> commit/rollback.

    Shared by every ``aspirate``/``aspirate_single`` variant. Sends
    ``prepareToAspirate`` first if this is the first aspirate since the last
    tip pickup (``self._prepared`` False), then the aspirate command itself.
    A successful prepare sets ``self._prepared = True`` immediately -- even
    if the following aspirate then fails and trackers roll back -- since
    priming is physical plunger state, not tracker state, and is not
    reversed by a tracker rollback.
    """
    try:
      if not self._prepared:
        await self._execute("prepareToAspirate", {"pipetteId": self.pipette_id})
        self._prepared = True
      await self._execute(command_type, params)
    except Exception:
      for tracker in staged_trackers:
        tracker.rollback()
      raise
    else:
      for tracker in staged_trackers:
        tracker.commit()

  async def _execute_trash_drop(self) -> None:
    """Send the two-command addressable-area trash-drop sequence.

    Shared by every ``discard_tips``/``drop_single_tip`` variant. No tracker
    involvement (trash has none); callers update ``_channel_tips`` and call
    ``_confirm_tips_cleared()`` themselves after this returns.
    """
    await self._execute(
      "moveToAddressableAreaForDropTip",
      {
        "pipetteId": self.pipette_id,
        "addressableAreaName": "movableTrashA3",
        "alternateDropLocation": True,
      },
    )
    await self._execute("dropTipInPlace", {"pipetteId": self.pipette_id})

  # --- Fine-pipetting shared helpers ---

  def _require_mounted_tip(self) -> None:
    """Raise if no channel holds a tip -- pre-wire guard for tip-motion ops.

    ``touch_tip``/``liquid_probe`` move the mounted tip itself into the
    well, so issuing them without a tip would drive the bare nozzle into
    the labware. Checked before any wire command is sent.
    """
    if all(tip is None for tip in self._channel_tips):
      raise OpentronsError(
        "NoTipError",
        "No tip mounted; pick up a tip first.",
      )

  def _touch_tip_params(
    self,
    labware_id: str,
    well_name: str,
    radius: float,
    offset: Optional[Coordinate],
  ) -> Dict[str, Any]:
    """Build the ``touchTip`` params dict shared by every head's ``touch_tip``.

    The ``wellLocation`` rides at origin "bottom" with a zero default
    offset -- ``touchTip`` addresses the height of the wall-touch motion,
    not a liquid position, so the liquid ops' +1mm clearance default does
    not apply. ``radius`` is the fraction of the well radius the tip moves
    toward (1.0 = the wall).
    """
    o = offset if offset is not None else Coordinate.zero()
    return {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "wellLocation": {"origin": "bottom", "offset": {"x": o.x, "y": o.y, "z": o.z}},
      "radius": radius,
    }

  async def _probe_z(self, command_type: str, labware_id: str, well_name: str) -> Optional[float]:
    """Send a ``liquidProbe``/``tryLiquidProbe`` command; return the found liquid z (mm).

    The robot-server OMITS ``z_position`` from the command result entirely
    (rather than reporting null) when no liquid is detected, so absence is
    read with ``.get()`` and surfaced as ``None`` -- callers decide whether
    that raises (``liquid_probe``) or passes through (``try_liquid_probe``).
    """
    result = await self._execute(
      command_type,
      {
        "pipetteId": self.pipette_id,
        "labwareId": labware_id,
        "wellName": well_name,
        "wellLocation": {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 0}},
      },
    )
    return cast(Optional[float], result.get("result", {}).get("z_position"))

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

    Merges an explicit x/y/z offset with ``liquid_height`` (added to z).
    ``origin`` defaults to ``"bottom"`` (aspirate/dispense); tip-pickup
    callers must pass ``origin="top"`` -- a tip-rack well's "bottom" is deep
    inside the tip, not the pickup engagement point. Returns ``None`` if
    neither offset nor liquid height is given.
    """
    offset = None
    if offsets is not None and offsets[0] is not None:
      o = offsets[0]
      offset = {"x": o.x, "y": o.y, "z": o.z}
    if liquid_height is not None and liquid_height[0] is not None:
      offset = offset or {"x": 0, "y": 0, "z": 0}
      offset["z"] += liquid_height[0]
    if offset is None:
      if origin == "bottom":
        # No explicit position given: default to just above the well bottom
        # rather than let the Protocol Engine fall back to origin "top" (the
        # rim, above the liquid). Pickup callers (origin "top") keep None.
        offset = {"x": 0, "y": 0, "z": _DEFAULT_WELL_BOTTOM_CLEARANCE}
      else:
        return None
    return {"origin": origin, "offset": offset}


# Column index -> A-row well name (the Flex API's anchor well for 8-channel
# ALL-mode column ops; the hardware fans a single command out to all 8
# physical nozzles from there).
_COLUMN_WELL_NAMES = [f"A{c + 1}" for c in range(12)]

# Row letters front-to-back as the Flex API names single nozzles ("H1" is the
# frontmost/primary nozzle, "A1" the rearmost).
_ROW_LETTERS = "ABCDEFGH"

_NUM_CHANNELS = 8

# Flex-managed positioning flow-rate defaults (uL/s), matching the
# p50_multi_v3.5 pipette defaults. Shared by FlexHead1/FlexHead8/FlexHead96 --
# the Flex applies the same defaults regardless of channel count.
_DEFAULT_ASPIRATE_FLOW_RATE = 35.0
_DEFAULT_DISPENSE_FLOW_RATE = 57.0

# The p50_multi_v3.5's default blow-out rate equals its dispense rate.
_DEFAULT_BLOW_OUT_FLOW_RATE = _DEFAULT_DISPENSE_FLOW_RATE

# Default aspirate/dispense position: 1mm above the well bottom, matching the
# Opentrons Python-API default. The raw Protocol-Engine /commands API defaults
# an OMITTED wellLocation to origin "top" (the well rim -- above the liquid),
# so a plain aspirate would draw air. We therefore always send an explicit
# bottom-referenced wellLocation for liquid ops.
_DEFAULT_WELL_BOTTOM_CLEARANCE = 1.0


class FlexHead1(_FlexHead):
  """Single-channel pipette head, well-addressed.

  Every op sends exactly ONE robot-server command naming the single well
  (tip spot or well) it addresses -- no anchor-well fan-out, no nozzle
  layout (there is only ever one physical nozzle). ``_channel_tips`` has
  length 1; the sole channel is index 0.

  Reuses the ``_FlexHead`` base's transactional stage -> wire -> verify ->
  commit/rollback flow, hardware tip-presence verification
  (``_verify_tips_seated``/``_confirm_tips_cleared``), and
  ``prepareToAspirate`` priming -- the same machinery ``FlexHead8`` uses for
  its column ops, applied to a single well instead of a column.

  Coded but **not yet verified on real single-channel Flex hardware** --
  Vincent's bench Flex carries an 8-channel pipette, not a single-channel
  one. A one-time ``logger.warning`` fires on the first op issued by an
  instance, and this docstring makes no "validated on hardware" claim.
  """

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
    self._warn_untested_hardware()
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
    self._prepared = False

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
    self._warn_untested_hardware()

    if isinstance(target, Trash):
      await self._execute_trash_drop()
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
    well: Well,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate from ``well`` -- one ``aspirate`` command naming it.

    Follows stage -> validate -> wire -> commit/rollback: ``well.tracker``
    (``remove_liquid``) is staged BEFORE the wire command, so an infeasible
    aspirate raises before any hardware motion. A ``prepareToAspirate``
    command is sent first if this is the first aspirate since the last tip
    pickup.
    """
    self._warn_untested_hardware()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    rate = flow_rate if flow_rate is not None else _DEFAULT_ASPIRATE_FLOW_RATE

    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking and not well.tracker.is_disabled:
      well.tracker.remove_liquid(volume=volume)  # stages + validates
      staged_trackers.append(well.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "volume": volume,
      "flowRate": rate,
    }
    well_location = self._well_location([offset], [liquid_height])
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_with_prepare("aspirate", params, staged_trackers)

  async def dispense(
    self,
    well: Well,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense to ``well`` -- one ``dispense`` command naming it.

    Follows stage -> validate -> wire -> commit/rollback: ``well.tracker``
    (``add_liquid``) is staged BEFORE the wire command, so an infeasible
    dispense raises before any hardware motion.
    """
    self._warn_untested_hardware()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    rate = flow_rate if flow_rate is not None else _DEFAULT_DISPENSE_FLOW_RATE

    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking and not well.tracker.is_disabled:
      well.tracker.add_liquid(volume=volume)  # stages + validates
      staged_trackers.append(well.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "volume": volume,
      "flowRate": rate,
    }
    well_location = self._well_location([offset], [liquid_height])
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_liquid_op("dispense", params, staged_trackers)

  async def touch_tip(
    self,
    well: Well,
    radius: float = 1.0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Touch the mounted tip to the sides of ``well`` -- one ``touchTip`` command.

    ``radius`` is the fraction of the well radius the tip moves toward
    (1.0 = the wall). Requires a mounted tip (checked before any wire
    command). No trackers are involved.
    """
    self._warn_untested_hardware()
    self._require_mounted_tip()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    await self._execute("touchTip", self._touch_tip_params(labware_id, well_name, radius, offset))

  async def liquid_probe(self, well: Well) -> float:
    """Probe downward in ``well`` until the pressure sensor detects liquid; return its z (mm).

    One ``liquidProbe`` command naming ``well``. Requires a mounted tip
    (checked before any wire command). Raises ``OpentronsError`` if no
    liquid is found; use ``try_liquid_probe`` for the non-raising variant.
    """
    self._warn_untested_hardware()
    self._require_mounted_tip()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    z = await self._probe_z("liquidProbe", labware_id, well_name)
    if z is None:
      raise OpentronsError(
        "LiquidNotFoundError",
        f"liquid_probe found no liquid in well {well.name!r}.",
      )
    return z

  async def try_liquid_probe(self, well: Well) -> Optional[float]:
    """Like ``liquid_probe`` but return ``None`` instead of raising when no liquid is found."""
    self._warn_untested_hardware()
    self._require_mounted_tip()
    parent = self._require_itemized_parent(well)
    labware_id = await self.flex._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(well)
    return await self._probe_z("tryLiquidProbe", labware_id, well_name)


class FlexHead8(_FlexHead):
  """8-channel pipette head, column-addressed (anchor-well fan-out).

  Every op sends exactly ONE robot-server command anchored at the column's
  A-row well (e.g. column 2 -> wellName "A3"); the Flex hardware fans that
  single command out to all 8 physical nozzles. Tip/volume trackers are
  committed only for the channels/wells actually actuated, skipping ``None``
  (inactive) channels (None-skip) -- and only after the wire command
  succeeds.

  Single-tip cherry-pick (``pick_up_single_tip``/``aspirate_single``/
  ``dispense_single``/``drop_single_tip``) switches the pipette to SINGLE
  nozzle mode first via ``configureNozzleLayout``; column ops reset back to
  ALL mode if a prior single-tip op left the layout otherwise
  (``_ensure_all_mode``).

  Verified on real 8-channel Flex hardware (Opentrons Flex, robot-server
  API 8.8): setup, homing, and column tip pickup confirmed against the
  hardware tip-presence sensor.
  """

  def __init__(self, flex: "OpentronsFlex", mount: str, pipette_id: str, channels: int) -> None:
    super().__init__(flex, mount, pipette_id, channels)
    self._nozzle_layout: str = "ALL"  # "ALL" | "SINGLE"

  # --- Nozzle layout guard ---

  async def _ensure_all_mode(self) -> None:
    """Reset to the ALL nozzle layout before a column op.

    A prior single-tip op may have left the pipette in SINGLE mode. Column
    ops always address all 8 physical channels, so they must not silently
    run under a stale single-nozzle configuration -- if the layout isn't
    already ALL, reset it first.
    """
    if self._nozzle_layout == "ALL":
      return
    await self._execute(
      "configureNozzleLayout",
      {"pipetteId": self.pipette_id, "configurationParams": {"style": "ALL"}},
    )
    self._nozzle_layout = "ALL"

  # --- Column helpers ---

  @staticmethod
  def _column_items(itemized: ItemizedResource, column: int) -> List[Any]:
    """Return the 8 column resources (TipSpots or Wells), in row order A..H.

    Mirrors the column-major slice used throughout PLR's itemized resources:
    item 0 is A1, item 1 is B1, ..., item 8 is A2, etc. -- so one column is
    ``items[column * 8 : (column + 1) * 8]``.
    """
    items = itemized.get_all_items()
    num_columns = len(items) // _NUM_CHANNELS
    if not 0 <= column < num_columns:
      raise ValueError(
        f"Column {column} out of range for resource with {num_columns} columns "
        f"(0-{num_columns - 1})."
      )
    return items[column * _NUM_CHANNELS : (column + 1) * _NUM_CHANNELS]

  # --- Column tip operations ---

  async def pick_up_tips(
    self,
    tip_rack: TipRack,
    column: int,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up a full column (8 tips) with a single ``pickUpTip`` command.

    Anchored at the column's A-row well; the hardware fans the pickup motion
    out to all 8 physical nozzles. Follows stage -> validate -> wire ->
    verify -> commit/rollback: tip trackers are staged (``commit=False``)
    BEFORE the wire command -- so an already-occupied channel (fix #4) or an
    invalid tracker state raises before any hardware motion -- then, after
    the wire command succeeds, the hardware tip-presence sensor is checked
    (``_verify_tips_seated()``); trackers and ``_channel_tips`` are committed
    only if that verification passes, and rolled back (with no
    ``_channel_tips`` mutation) if the sensor reports a missed pickup. Only
    spots that actually had a tip are staged (None-skip).
    """
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(tip_rack)
    well_name = _COLUMN_WELL_NAMES[column]
    column_spots = self._column_items(tip_rack, column)

    for i, spot in enumerate(column_spots):
      if spot.has_tip() and self._channel_tips[i] is not None:
        raise OpentronsError(
          "HasTipError",
          f"Channel {i} already holds a tip; drop it before picking up another.",
        )

    tracking = does_tip_tracking()
    staged_trackers: List[Any] = []
    tips: List[Optional[Tip]] = [None] * len(column_spots)
    for i, spot in enumerate(column_spots):
      if not spot.has_tip():
        continue
      tips[i] = spot.get_tip()
      if tracking and not spot.tracker.is_disabled:
        spot.tracker.remove_tip()  # commit=False: stages + validates
        staged_trackers.append(spot.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
    }
    well_location = self._well_location([offset], [None], origin="top")
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_pickup("pickUpTip", params, staged_trackers)
    for i, tip in enumerate(tips):
      self._channel_tips[i] = tip
    self._prepared = False

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
    """
    await self._ensure_all_mode()

    if isinstance(target, Trash):
      await self._execute_trash_drop()
      self._channel_tips = [None] * self.channels
      await self._confirm_tips_cleared()
      return

    if column is None:
      raise ValueError("column is required when dropping tips to a TipRack.")

    labware_id = await self.flex._ensure_labware_loaded(target)
    well_name = _COLUMN_WELL_NAMES[column]
    column_spots = self._column_items(target, column)

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

  # --- Column liquid handling ---

  async def aspirate(
    self,
    plate: Plate,
    column: int,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate a column -- one ``aspirate`` command anchored at the A-row well.

    Follows stage -> validate -> wire -> commit/rollback: ``Well.tracker``
    (``remove_liquid``) is staged for every well whose channel actually
    holds a tip (None-skip; wells outside ``column`` are never touched --
    the Case-1 regression guard) BEFORE the wire command, so an infeasible
    aspirate (e.g. ``TooLittleLiquidError``) raises before any hardware
    motion. A ``prepareToAspirate`` command is sent first if this is the
    first aspirate since the last tip pickup.
    """
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    well_name = _COLUMN_WELL_NAMES[column]
    rate = flow_rate if flow_rate is not None else _DEFAULT_ASPIRATE_FLOW_RATE

    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking:
      for i, well in enumerate(self._column_items(plate, column)):
        if self._channel_tips[i] is None or well.tracker.is_disabled:
          continue
        well.tracker.remove_liquid(volume=volume)  # stages + validates
        staged_trackers.append(well.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "volume": volume,
      "flowRate": rate,
    }
    well_location = self._well_location([offset], [liquid_height])
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_with_prepare("aspirate", params, staged_trackers)

  async def dispense(
    self,
    plate: Plate,
    column: int,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense a column -- one ``dispense`` command anchored at the A-row well.

    Follows stage -> validate -> wire -> commit/rollback: ``Well.tracker``
    (``add_liquid``) is staged for every well whose channel actually holds
    a tip (None-skip) BEFORE the wire command, so an infeasible dispense
    (e.g. ``TooLittleVolumeError``) raises before any hardware motion.
    """
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    well_name = _COLUMN_WELL_NAMES[column]
    rate = flow_rate if flow_rate is not None else _DEFAULT_DISPENSE_FLOW_RATE

    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking:
      for i, well in enumerate(self._column_items(plate, column)):
        if self._channel_tips[i] is None or well.tracker.is_disabled:
          continue
        well.tracker.add_liquid(volume=volume)  # stages + validates
        staged_trackers.append(well.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "volume": volume,
      "flowRate": rate,
    }
    well_location = self._well_location([offset], [liquid_height])
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_liquid_op("dispense", params, staged_trackers)

  async def touch_tip(
    self,
    plate: Plate,
    column: int,
    radius: float = 1.0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Touch the mounted tips to their well walls -- one ``touchTip`` command
    anchored at the column's A-row well.

    ``radius`` is the fraction of the well radius each tip moves toward
    (1.0 = the wall). Requires at least one mounted tip (checked before any
    wire command) and ALL nozzle mode (reset first if a single-tip op left
    the layout otherwise). No trackers are involved.
    """
    self._require_mounted_tip()
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    well_name = _COLUMN_WELL_NAMES[column]
    await self._execute("touchTip", self._touch_tip_params(labware_id, well_name, radius, offset))

  async def liquid_probe(self, plate: Plate, column: int) -> float:
    """Probe for liquid in a column -- one ``liquidProbe`` command anchored at
    the A-row well; return the found liquid z (mm).

    Requires at least one mounted tip (checked before any wire command) and
    ALL nozzle mode (reset first if a single-tip op left the layout
    otherwise). Raises ``OpentronsError`` if no liquid is found; use
    ``try_liquid_probe`` for the non-raising variant.
    """
    self._require_mounted_tip()
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    well_name = _COLUMN_WELL_NAMES[column]
    z = await self._probe_z("liquidProbe", labware_id, well_name)
    if z is None:
      raise OpentronsError(
        "LiquidNotFoundError",
        f"liquid_probe found no liquid in column {column} of {plate.name!r}.",
      )
    return z

  async def try_liquid_probe(self, plate: Plate, column: int) -> Optional[float]:
    """Like ``liquid_probe`` but return ``None`` instead of raising when no liquid is found."""
    self._require_mounted_tip()
    await self._ensure_all_mode()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    well_name = _COLUMN_WELL_NAMES[column]
    return await self._probe_z("tryLiquidProbe", labware_id, well_name)

  # --- Single-tip cherry-pick ---

  @staticmethod
  def _channel_for_well(well: str) -> int:
    """Map a well name's row letter to its physical channel index (A=0..H=7)."""
    row_letter = well[0].upper()
    try:
      return _ROW_LETTERS.index(row_letter)
    except ValueError:
      raise ValueError(f"'{well}' has no recognized row letter (expected A-H).") from None

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
  ) -> None:
    """Pick up one tip in SINGLE nozzle mode.

    Switches to SINGLE layout (``configureNozzleLayout``) before the
    ``pickUpTip`` command. The physical nozzle engaged is the one whose row
    matches ``well``'s row letter (e.g. well "H2" -> nozzle "H1" -> channel
    7); only that channel's tip state changes. Raises ``OpentronsError`` if
    that channel already holds a tip (fix #4) -- checked before any wire
    command. Tip tracker changes are staged (``commit=False``) before the
    wire command, then, after the wire command succeeds, the hardware
    tip-presence sensor is checked (``_verify_tips_seated()``) -- the
    tracker and ``_channel_tips`` are committed only if that verification
    passes, and rolled back (with no ``_channel_tips`` mutation) if the
    sensor reports a missed pickup (stage -> validate -> wire -> verify ->
    commit/rollback).
    """
    channel = self._channel_for_well(well)
    if self._channel_tips[channel] is not None:
      raise OpentronsError(
        "HasTipError",
        f"Channel {channel} already holds a tip; drop it before picking up another.",
      )

    primary_nozzle = f"{_ROW_LETTERS[channel]}1"
    await self._execute(
      "configureNozzleLayout",
      {
        "pipetteId": self.pipette_id,
        "configurationParams": {"style": "SINGLE", "primaryNozzle": primary_nozzle},
      },
    )
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
    self._prepared = False

  async def aspirate_single(
    self,
    plate: Plate,
    well: str,
    volume: float,
    flow_rate: Optional[float] = None,
  ) -> None:
    """Aspirate a single well with the currently mounted single tip.

    Sends ``prepareToAspirate`` first if this is the first aspirate since
    the last (single-tip) pickup. Follows stage -> validate -> wire ->
    commit/rollback for the well tracker, same as the column ``aspirate``.
    """
    self._active_single_channel()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    rate = flow_rate if flow_rate is not None else _DEFAULT_ASPIRATE_FLOW_RATE
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well,
      "volume": volume,
      "flowRate": rate,
      "wellLocation": {
        "origin": "bottom",
        "offset": {"x": 0, "y": 0, "z": _DEFAULT_WELL_BOTTOM_CLEARANCE},
      },
    }

    target = plate.get_item(well)
    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking and not target.tracker.is_disabled:
      target.tracker.remove_liquid(volume=volume)  # stages + validates
      staged_trackers.append(target.tracker)

    await self._execute_with_prepare("aspirate", params, staged_trackers)

  async def dispense_single(
    self,
    plate: Plate,
    well: str,
    volume: float,
    flow_rate: Optional[float] = None,
  ) -> None:
    """Dispense to a single well with the currently mounted single tip."""
    self._active_single_channel()
    labware_id = await self.flex._ensure_labware_loaded(plate)
    rate = flow_rate if flow_rate is not None else _DEFAULT_DISPENSE_FLOW_RATE
    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": well,
      "volume": volume,
      "flowRate": rate,
      "wellLocation": {
        "origin": "bottom",
        "offset": {"x": 0, "y": 0, "z": _DEFAULT_WELL_BOTTOM_CLEARANCE},
      },
    }

    target = plate.get_item(well)
    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking and not target.tracker.is_disabled:
      target.tracker.add_liquid(volume=volume)  # stages + validates
      staged_trackers.append(target.tracker)

    await self._execute_liquid_op("dispense", params, staged_trackers)

  async def drop_single_tip(self, trash: Trash) -> None:
    """Drop the single mounted tip to trash and restore ALL nozzle mode.

    After the wire drop + ``_channel_tips`` update, ``_confirm_tips_cleared()``
    checks the hardware tip-presence sensor and logs a warning (does not
    raise) if it still reports a tip.
    """
    channel = self._active_single_channel()
    await self._execute_trash_drop()
    self._channel_tips[channel] = None
    await self._confirm_tips_cleared()

    await self._execute(
      "configureNozzleLayout",
      {"pipetteId": self.pipette_id, "configurationParams": {"style": "ALL"}},
    )
    self._nozzle_layout = "ALL"


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
    self._warn_untested_hardware()
    spots = self._check_full_coverage(tip_rack)

    for i, spot in enumerate(spots):
      if spot.has_tip() and self._channel_tips[i] is not None:
        raise OpentronsError(
          "HasTipError",
          f"Channel {i} already holds a tip; drop it before picking up another.",
        )

    await self._execute(
      "configureNozzleLayout",
      {"pipetteId": self.pipette_id, "configurationParams": {"style": "ALL"}},
    )

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
    self._prepared = False

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
    self._warn_untested_hardware()

    if isinstance(target, Trash):
      await self._execute_trash_drop()
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
    plate: Plate,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Aspirate the whole plate -- one ``aspirate`` command anchored at "A1".

    Follows stage -> validate -> wire -> commit/rollback: ``Well.tracker``
    (``remove_liquid``) is staged for every well whose channel actually
    holds a tip (None-skip) BEFORE the wire command, so an infeasible
    aspirate raises before any hardware motion. A ``prepareToAspirate``
    command is sent first if this is the first aspirate since the last tip
    pickup.
    """
    self._warn_untested_hardware()
    wells = self._check_full_coverage(plate)
    labware_id = await self.flex._ensure_labware_loaded(plate)
    rate = flow_rate if flow_rate is not None else _DEFAULT_ASPIRATE_FLOW_RATE

    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking:
      for i, well in enumerate(wells):
        if self._channel_tips[i] is None or well.tracker.is_disabled:
          continue
        well.tracker.remove_liquid(volume=volume)  # stages + validates
        staged_trackers.append(well.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": self._ANCHOR_WELL_NAME,
      "volume": volume,
      "flowRate": rate,
    }
    well_location = self._well_location([offset], [liquid_height])
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_with_prepare("aspirate", params, staged_trackers)

  async def dispense(
    self,
    plate: Plate,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
  ) -> None:
    """Dispense to the whole plate -- one ``dispense`` command anchored at "A1".

    Follows stage -> validate -> wire -> commit/rollback: ``Well.tracker``
    (``add_liquid``) is staged for every well whose channel actually holds a
    tip (None-skip) BEFORE the wire command, so an infeasible dispense
    raises before any hardware motion.
    """
    self._warn_untested_hardware()
    wells = self._check_full_coverage(plate)
    labware_id = await self.flex._ensure_labware_loaded(plate)
    rate = flow_rate if flow_rate is not None else _DEFAULT_DISPENSE_FLOW_RATE

    tracking = does_volume_tracking()
    staged_trackers: List[Any] = []
    if tracking:
      for i, well in enumerate(wells):
        if self._channel_tips[i] is None or well.tracker.is_disabled:
          continue
        well.tracker.add_liquid(volume=volume)  # stages + validates
        staged_trackers.append(well.tracker)

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "labwareId": labware_id,
      "wellName": self._ANCHOR_WELL_NAME,
      "volume": volume,
      "flowRate": rate,
    }
    well_location = self._well_location([offset], [liquid_height])
    if well_location is not None:
      params["wellLocation"] = well_location

    await self._execute_liquid_op("dispense", params, staged_trackers)

  async def touch_tip(
    self,
    plate: Plate,
    radius: float = 1.0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Touch the mounted tips to their well walls -- one ``touchTip`` command
    anchored at "A1", fanned to all 96 channels.

    ``radius`` is the fraction of the well radius each tip moves toward
    (1.0 = the wall). Requires at least one mounted tip and a 96-position
    plate (both checked before any wire command). No trackers are involved.
    """
    self._warn_untested_hardware()
    self._require_mounted_tip()
    self._check_full_coverage(plate)
    labware_id = await self.flex._ensure_labware_loaded(plate)
    await self._execute(
      "touchTip", self._touch_tip_params(labware_id, self._ANCHOR_WELL_NAME, radius, offset)
    )

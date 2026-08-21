"""High-level Bravo device facade.

Wraps a :class:`~.controllers.base.BravoController`, the state-machine
engine, deck state, taught positions, and head/tip selection state behind a
single async API. A caller constructs the controller (and, for real
hardware, the transport it uses) and injects both here; this module does no
controller-type dispatch and loads no configuration file of its own.

Every step failure raises directly out of the awaited call: this facade
deliberately never calls :meth:`~.state_machine.engine.StateMachineEngine.set_error_handler`,
so a task here never pauses waiting for an operator's retry/ignore/abort
choice -- :class:`~.state_machine.engine.TaskStatus.ABORTED` is therefore
unreachable through this facade. The engine's abort/retry/ignore machinery
still exists and works exactly as documented on
:class:`~.state_machine.engine.StateMachineEngine`; a caller that wants
that interactive recovery loop registers its own error handler on
:attr:`Bravo.engine` (or drives the state-machine tasks directly) rather
than getting it from this facade.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from .axis_config import DEFAULT_SPEEDS
from .config import BravoMachineConfig
from .controllers.base import AxisMoveInfo, BravoController, JogParams
from .deck.geometry import well_center_offset_from_teachpoint_mm, well_geometry_from_metadata
from .deck.labware import DeckState, Labware
from .deck.teachpoints import Teachpoints
from .head_mode import (
  HeadGeometry,
  HeadMode,
  PlateSelection,
  TipSelection,
  head_geometry_for_type,
  head_mode_offsets_mm,
  is_legal_plate_anchor,
  is_legal_tipbox_anchor,
  legal_plate_anchors,
  legal_tipbox_anchors,
  normalize_head_mode,
  plate_selection,
  selected_tip_wells,
  tipbox_selection,
)
from .state_machine.engine import StateMachineEngine
from .state_machine.tasks import (
  AspirateTask,
  DispenseTask,
  HomeTask,
  InitializeTask,
  MixTask,
  MoveToLocationTask,
  PickPlaceTask,
  TipsOffTask,
  TipsOnTask,
  _assert_neighbor_clearance,
  _gripper_head_offsets,
)
from .tip_offsets import ResolvedTipOffsets, get_tip_offset_table
from .tips import get_default_tip_id_for_head, get_tip_id_for_capacity, get_tip_length_mm
from .transport._bridge import AsyncTransportBase
from .types import ALL_AXES, Axis, HeadType, SpeedLevel, safe_home_order

if TYPE_CHECKING:
  from .deck.resource import BravoDeck

logger = logging.getLogger(__name__)

_NEIGHBOR_CLEARANCE_SAFETY_MM = 2.0
"""Millimetres of margin subtracted from a neighbor-clearance check's allowed
top plane, so a selection is rejected before it would just barely clip a
neighbor."""


class _GripperPickPhase(PickPlaceTask):
  """Runs only the pick-and-lift half of :class:`PickPlaceTask`.

  Constructed with the source location standing in for both endpoints, so
  the pick geometry (:meth:`PickPlaceTask._calculate_positions`) is solved
  purely from what is known at pick time: the source stack. The lift target
  this reaches is therefore only guaranteed to clear the source stack, not
  any particular destination -- :class:`_GripperPlacePhase` re-solves and,
  if necessary, climbs further once the real destination is known.
  """

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    deck: DeckState,
    location: int,
    speed: SpeedLevel = "med",
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to move against.
      deck: The deck state to pick from.
      location: The deck location to pick up from.
      speed: The speed profile for XYZ/Zg motion.
    """
    PickPlaceTask.__init__(
      self,
      controller,
      teachpoints,
      config,
      deck,
      from_location=location,
      to_location=location,
      speed=speed,
    )
    self.name = f"GripperPick_{location}"

  def get_steps(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
    """Return the pick-and-lift steps, stopping short of any lateral travel."""
    return [
      ("move_to_safe_pick_start", self._move_to_safe_pick_start),
      ("move_gripper_to_nesting", self._move_gripper_to_nesting),
      ("move_xy_to_pick", self._move_xy_to_pick),
      ("move_to_pick_height", self._move_to_pick_height),
      ("grip_plate", self._grip_plate),
      ("move_to_carry_height", self._move_to_carry_height),
    ]


class _GripperPlacePhase(PickPlaceTask):
  """Runs only the lower-and-release half of :class:`PickPlaceTask`.

  Constructed with the real source and destination, so
  :meth:`PickPlaceTask._calculate_positions` solves the full, obstacle- and
  destination-aware carry and place geometry -- unlike
  :class:`_GripperPickPhase`, which only knew the source. Its first step
  re-runs the carry-height move with these corrected values: since the
  corrected carry height is always greater than or equal to the
  source-only value :class:`_GripperPickPhase` already reached (the
  destination and obstacle terms in that ``max()`` can only add height,
  never remove it), this only ever climbs further before it moves
  laterally, never descends into something along the way.

  The grip itself already happened in a prior :class:`_GripperPickPhase`
  run, so this is constructed with ``plate_already_gripped=True`` --
  :class:`PickPlaceTask`'s own supported way to seed the state a completed
  grip would have left, rather than gripping again.
  """

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    deck: DeckState,
    from_location: int,
    to_location: int,
    speed: SpeedLevel = "med",
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to move against.
      deck: The deck state, updated on a successful place.
      from_location: The deck location the resource was picked up from.
      to_location: The deck location to place at.
      speed: The speed profile for XYZ/Zg motion.
    """
    PickPlaceTask.__init__(
      self,
      controller,
      teachpoints,
      config,
      deck,
      from_location=from_location,
      to_location=to_location,
      speed=speed,
      plate_already_gripped=True,
    )
    self.name = f"GripperPlace_{from_location}_{to_location}"

  def get_steps(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
    """Return the carry, place, and release steps."""
    return [
      ("move_to_carry_height", self._move_to_carry_height),
      ("move_xy_to_place", self._move_xy_to_place),
      ("move_to_place_height", self._move_to_place_height),
      ("release_plate", self._release_plate),
      ("return_gripper_to_nesting", self._return_gripper_to_nesting),
    ]


class Bravo:
  """High-level interface for the Agilent Bravo liquid handler.

  Owns the controller, the state-machine engine, deck state, taught
  positions, and head/tip selection state. Every operation builds a task
  and runs it through the engine, or for the small handful of single
  commands with no failure-recovery surface, calls the controller directly.
  """

  def __init__(
    self,
    controller: BravoController,
    transport: Optional[AsyncTransportBase] = None,
    config: Optional[BravoMachineConfig] = None,
    deck: Optional["BravoDeck"] = None,
  ) -> None:
    """Initialize the facade.

    Args:
      controller: The controller to operate. Already constructed by the
        caller; this class does no controller-type dispatch.
      transport: The transport the controller communicates over, if any.
        ``None`` for a controller that performs no I/O (such as
        :class:`~.controllers.simulation.SimulationController`).
      config: The machine configuration to operate against. Defaults to
        :class:`BravoMachineConfig`'s own defaults when omitted.
      deck: The deck model to source taught positions from. When given,
        its :attr:`~.deck.resource.BravoDeck.teachpoints` is the single
        source of truth both this facade and the deck model's site
        origins agree on. Omitted (e.g. in a test against
        :class:`~.controllers.simulation.SimulationController` with no PLR
        deck wired up yet), a default set of teachpoints for the
        configured head type is built instead.
    """
    self._controller = controller
    self._transport = transport
    self._config = config if config is not None else BravoMachineConfig()
    if deck is not None:
      self._teachpoints = deck.teachpoints
    else:
      self._teachpoints = Teachpoints()
      self._teachpoints.set_default_teachpoints(self._config.head.head_type)
    self._engine = StateMachineEngine()
    self._deck_state = DeckState()
    self._homed_axes: set[Axis] = set()
    self._head_mode = normalize_head_mode(self._config.head.head_type, "all_barrels", "back_left")
    self._tips_on_head = False
    self._tip_definition_id = ""
    self._attached_tip_length_mm: Optional[float] = None
    self._tips_on_head_mode: Optional[HeadMode] = None
    self._tip_selection: Optional[TipSelection] = None
    self._plate_selection: dict[int, PlateSelection] = {}
    self._tipbox_occupancy: dict[int, set[tuple[int, int]]] = {}
    self._tipbox_untracked: set[int] = set()
    self._gripper_held_task: Optional[PickPlaceTask] = None
    self._gripper_pick_location: Optional[int] = None

  @property
  def controller(self) -> BravoController:
    """The controller this facade operates."""
    return self._controller

  @property
  def engine(self) -> StateMachineEngine:
    """The state-machine engine this facade runs every task through.

    No error handler is registered on it by this facade (see the module
    docstring): a caller that wants the engine's interactive abort/retry/
    ignore recovery loop instead of a raised exception registers its own
    handler here with
    :meth:`~.state_machine.engine.StateMachineEngine.set_error_handler`.
    """
    return self._engine

  # -- Lifecycle --

  async def setup(self) -> None:
    """Bring the transport (if any) and controller online.

    Sets up the transport, then calls the controller's own
    :meth:`~.controllers.base.BravoController.initialize`. Deliberately
    low-level and non-interactive: it does not home any axis and cannot
    raise waiting on an operator's answer to a prompt, so a caller such as
    PyLabRobot's ``LiquidHandler.setup()`` can await it without risking a
    hang. Call :meth:`home` to home the axes, or :meth:`initialize` for
    the fuller cold-start sequence, once the connection is up. Also syncs
    the software-tracked homed-axes cache from the controller once, so a
    controller that starts pre-homed (like
    :class:`~.controllers.simulation.SimulationController`) is reflected
    immediately rather than only after the next :meth:`home` call.
    """
    if self._transport is not None:
      await self._transport.setup()
    self._controller.initialize()
    self._homed_axes = {axis for axis in ALL_AXES if self._controller.is_axis_homed(axis)}

  async def stop(self) -> None:
    """Take the controller and transport (if any) offline.

    Calls the controller's :meth:`~.controllers.base.BravoController.deinitialize`,
    then stops the transport.
    """
    self._controller.deinitialize()
    if self._transport is not None:
      await self._transport.stop()
    self._homed_axes.clear()

  async def initialize(self) -> None:
    """Run the full cold-start sequence: detect the head/gripper and home.

    Runs :class:`~.state_machine.tasks.InitializeTask` -- ping, detect the
    gripper and head, clear faults, and home every axis not already
    homed, in the order that keeps the head and gripper clear of the deck.
    Call :meth:`setup` first to bring the connection up.

    This is not part of :meth:`setup` because, with the default
    configuration, it may need to ask an operator whether it is safe to
    home the W axis (fluid may be in the tips). This facade registers no
    engine error handler, so a step that would normally pause for that
    answer raises a :class:`RuntimeError` carrying the prompt's message
    instead -- it never blocks waiting for a response. The same applies
    to the gripper-detection and plate-in-gripper confirmation steps.
    Set ``config.safety.prompt_home_w = False`` to skip the W-axis
    question entirely.

    Raises:
      RuntimeError: If any step fails, including an operator-confirmation
        step whose question was never answered.
    """
    task = InitializeTask(self._controller, self._config)
    await self._engine.execute(task)
    self._homed_axes = {axis for axis in ALL_AXES if self._controller.is_axis_homed(axis)}

  # -- Homing --

  async def home(self, axes: "Optional[list[Axis]]" = None, *, force: bool = False) -> "list[Axis]":
    """Home the machine.

    Args:
      axes: The axes to home. Defaults to X/Y/Z, plus W unless the
        configuration ignores it, plus G/Zg when the controller has a
        gripper and the configuration has axis entries for them.
      force: Re-runs the routine on axes that already report themselves
        homed. An explicit operator "home this axis" request should pass
        ``True``: nothing moving because the axes "look" homed is never
        the right answer for a deliberate request.

    Returns:
      The axes homed, in the order they were homed.
    """
    if axes is None:
      axes = ["x", "y", "z"]
      if not self._config.safety.ignore_w_axis:
        axes.append("w")
      if self._controller.has_gripper and "g" in self._config.axes and "zg" in self._config.axes:
        axes.extend(["g", "zg"])
    ordered_axes = safe_home_order(axes)
    task = HomeTask(
      self._controller,
      self._config,
      ordered_axes,
      safe_z_position=self._config.safety.z_safe_position,
      force=force,
    )
    await self._engine.execute(task)
    self._homed_axes.update(ordered_axes)
    return list(ordered_axes)

  async def home_single_axis(self, axis: Axis) -> None:
    """Home one axis on explicit request, forcing the routine unconditionally.

    Bypasses the state machine: this is a direct request for one axis, not
    a full cold-start sequence. W is parked back at 0 after homing, matching
    what a fresh W homes to.

    Args:
      axis: The axis to home.
    """
    self._controller.home_axes([axis], force=True)
    if axis == "w":
      current = float(self._controller.get_position("w"))
      if abs(current) > 0.07:
        self._controller.move([AxisMoveInfo(axis="w", position=0.0)], wait=True)
    self._homed_axes.add(axis)

  def is_axis_homed(self, axis: Axis) -> bool:
    """Return whether *axis* is homed, from the software-tracked cache.

    Tracked in software rather than polled on every call: a wire read per
    axis is not worth paying on every check. The cache is refreshed once at
    :meth:`setup` and updated by every homing call; a power cycle behind
    this facade's back would leave it stale until the next of those.
    """
    return axis in self._homed_axes

  # -- Motion --

  async def move_axis(
    self, axis: Axis, position: float, velocity: float = 0.0, acceleration: float = 0.0
  ) -> None:
    """Move one axis to an absolute position.

    Args:
      axis: The axis to move.
      position: The target position, in millimetres (or microlitres for W).
      velocity: Move velocity. ``0`` leaves the controller's current setting.
      acceleration: Move acceleration. ``0`` leaves the controller's current
        setting.
    """
    self._controller.move(
      [AxisMoveInfo(axis=axis, position=position, velocity=velocity, acceleration=acceleration)]
    )

  async def jog_axis(
    self,
    axis: Axis,
    step: float,
    speed: SpeedLevel = "med",
    peak_current: Optional[float] = None,
  ) -> float:
    """Jog one axis by a relative step and return its new position.

    Args:
      axis: The axis to jog.
      step: The relative distance to move, in millimetres (or microlitres
        for W).
      speed: The speed profile to jog at.
      peak_current: If given, jogs with a current limit instead of a fixed
        distance, stopping when the motor current exceeds the limit --
        used to verify current feedback before a tips-on press.

    Returns:
      The axis's position after the jog.
    """
    velocity, acceleration = self._speed_profile(axis, speed)
    if peak_current is not None:
      current_pos = self._controller.get_position(axis)
      target = current_pos + step
      try:
        return self._controller.jog(
          JogParams(
            axis=axis,
            velocity=velocity,
            acceleration=acceleration,
            max_position=target,
            tolerance=2.0,
            peak_current=peak_current,
          )
        )
      except Exception as exc:
        logger.warning("Force-limited jog on %s stopped short: %s", axis, exc)
        return self._controller.get_position(axis)
    move = AxisMoveInfo(
      axis=axis, position=step, velocity=velocity, acceleration=acceleration, absolute=False
    )
    self._controller.move([move])
    return self._controller.get_position(axis)

  def _speed_profile(self, axis: Axis, level: SpeedLevel) -> tuple[float, float]:
    """Return the (velocity, acceleration) pair for *axis* at *level*."""
    cfg = self._config.axes.get(axis)
    if cfg is not None and level in cfg.speeds:
      profile = cfg.speeds[level]
      return profile.velocity, profile.acceleration
    fallback = DEFAULT_SPEEDS.get(axis, {}).get(level)
    if fallback is None:
      return 0.0, 0.0
    return fallback.velocity, fallback.acceleration

  async def move_to_location(
    self,
    location: int,
    approach_height: float = 0.0,
    only_move_z: bool = False,
    speed: SpeedLevel = "med",
  ) -> None:
    """Move the head to a deck location using its taught position.

    Args:
      location: The deck location to move to.
      approach_height: Millimetres to stop above the taught Z before the
        final lowering move. ``0`` lowers straight to the taught position.
      only_move_z: Move only Z, to the safe position, skipping the lateral
        move and the final lowering.
      speed: The speed profile for the move.
    """
    lateral_axes: "tuple[Axis, Axis, Axis]" = ("x", "y", "z")
    task = MoveToLocationTask(
      self._controller,
      self._teachpoints,
      location,
      safe_z_position=self._config.safety.z_safe_position,
      approach_height=approach_height,
      only_move_z=only_move_z,
      speed_profiles={axis: self._speed_profile(axis, speed) for axis in lateral_axes},
    )
    await self._engine.execute(task)

  async def move_to_safe_z(self, speed: SpeedLevel = "med") -> None:
    """Retract Z to the configured safe position.

    Args:
      speed: The speed profile for the move.
    """
    velocity, acceleration = self._speed_profile("z", speed)
    self._controller.move(
      [
        AxisMoveInfo(
          axis="z",
          position=self._config.safety.z_safe_position,
          velocity=velocity,
          acceleration=acceleration,
        )
      ],
      wait=True,
    )

  def get_position(self, axis: Axis) -> float:
    """Return the current position of *axis*, in engineering units."""
    return self._controller.get_position(axis)

  def get_all_positions(self) -> dict[str, float]:
    """Return the current position of every axis, keyed by axis name."""
    return {axis: self._controller.get_position(axis) for axis in ALL_AXES}

  def enable_motor(self, axis: Axis) -> None:
    """Enable the motor drive for *axis*."""
    self._controller.enable_motor(axis)

  def disable_motor(self, axis: Axis) -> None:
    """Disable the motor drive for *axis*."""
    self._controller.disable_motor(axis)

  # -- Liquid handling --

  def _assert_well_access(self, location: int) -> None:
    """Raise if the labware at *location* cannot currently accept a well access."""
    labware = self._deck_state.get_stack(location).top
    if labware is None:
      return
    if labware.is_lidded:
      raise RuntimeError(
        f"Cannot access wells at location {location}: plate '{labware.name}' has a lid on it."
      )
    if labware.is_sealed:
      raise RuntimeError(
        f"Cannot access wells at location {location}: plate '{labware.name}' is sealed."
      )

  async def aspirate(
    self,
    location: int,
    volume: float,
    pre_aspirate: float = 0.0,
    post_aspirate: float = 0.0,
    distance_from_bottom: float = 1.0,
    dynamic_tip_extension: float = 0.0,
    tip_touch: bool = False,
    liquid_class: "Optional[dict[str, Any]]" = None,
    pipette_technique: "Optional[dict[str, Any]]" = None,
  ) -> None:
    """Aspirate a volume at a deck location.

    Args:
      location: The deck location to aspirate at.
      volume: The volume to aspirate, in microlitres.
      pre_aspirate: An air-gap volume drawn before descending into liquid,
        in microlitres.
      post_aspirate: An air-gap volume drawn after retracting, in
        microlitres.
      distance_from_bottom: Clearance above the well bottom, in
        millimetres.
      dynamic_tip_extension: Millimetres the head lowers per corrected
        microlitre aspirated. ``0`` disables it.
      tip_touch: Whether to touch the tip against the well wall after
        aspirating.
      liquid_class: Pre-resolved per-operation motion parameters, if any.
      pipette_technique: Pre-resolved swirl-technique parameters, if any.
    """
    self._assert_well_access(location)
    labware = self._deck_state.get_stack(location).top
    task = AspirateTask(
      self._controller,
      self._teachpoints,
      location,
      volume,
      pre_aspirate_volume=pre_aspirate,
      post_aspirate_volume=post_aspirate,
      distance_from_bottom=distance_from_bottom,
      safe_z_position=self._config.safety.z_safe_position,
      labware=labware,
      head_type=self._config.head.head_type,
      head_mode=self._head_mode,
      plate_selection=self._effective_plate_selection(location, labware, self._head_mode),
      dynamic_tip_extension=dynamic_tip_extension,
      tip_touch=tip_touch,
      liquid_class=liquid_class,
      pipette_technique=pipette_technique,
      deck=self._deck_state,
      teach_tip_length_mm=self._config.head.teach_tip_length_mm,
      attached_tip_length_mm=self._attached_tip_length_mm,
      tips_on_head=self._tips_on_head,
    )
    await self._engine.execute(task)

  async def dispense(
    self,
    location: int,
    volume: float,
    blowout: float = 0.0,
    distance_from_bottom: float = 1.0,
    empty_tips: bool = False,
    dynamic_tip_retraction: float = 0.0,
    tip_touch: bool = False,
    liquid_class: "Optional[dict[str, Any]]" = None,
    pipette_technique: "Optional[dict[str, Any]]" = None,
  ) -> None:
    """Dispense a volume at a deck location.

    Args:
      location: The deck location to dispense at.
      volume: The volume to dispense, in microlitres.
      blowout: An extra volume dispensed beyond ``volume`` to clear the
        tip, in microlitres.
      distance_from_bottom: Clearance above the well bottom, in
        millimetres.
      empty_tips: Dispense the tip's entire remaining contents instead of a
        fixed volume.
      dynamic_tip_retraction: Millimetres the head raises per corrected
        microlitre dispensed. ``0`` disables it.
      tip_touch: Whether to touch the tip against the well wall after
        dispensing.
      liquid_class: Pre-resolved per-operation motion parameters, if any.
      pipette_technique: Pre-resolved swirl-technique parameters, if any.
    """
    self._assert_well_access(location)
    labware = self._deck_state.get_stack(location).top
    task = DispenseTask(
      self._controller,
      self._teachpoints,
      location,
      volume=volume,
      blowout_volume=blowout,
      distance_from_bottom=distance_from_bottom,
      safe_z_position=self._config.safety.z_safe_position,
      labware=labware,
      head_type=self._config.head.head_type,
      head_mode=self._head_mode,
      plate_selection=self._effective_plate_selection(location, labware, self._head_mode),
      empty_tips=empty_tips,
      dynamic_tip_retraction=dynamic_tip_retraction,
      tip_touch=tip_touch,
      liquid_class=liquid_class,
      pipette_technique=pipette_technique,
      deck=self._deck_state,
      teach_tip_length_mm=self._config.head.teach_tip_length_mm,
      attached_tip_length_mm=self._attached_tip_length_mm,
      tips_on_head=self._tips_on_head,
    )
    await self._engine.execute(task)

  async def mix(
    self,
    location: int,
    volume: float,
    pre_aspirate: float = 0.0,
    blowout: float = 0.0,
    mix_cycles: int = 3,
    aspirate_distance: float = 1.0,
    dispense_at_different_distance: bool = False,
    dispense_distance: float = 1.0,
    dynamic_tip_extension: float = 0.0,
    tip_touch: bool = False,
    liquid_class: "Optional[dict[str, Any]]" = None,
    pipette_technique: "Optional[dict[str, Any]]" = None,
  ) -> None:
    """Mix liquid at a deck location using repeated aspirate/dispense strokes.

    Args:
      location: The deck location to mix at.
      volume: The volume drawn and expelled on each stroke, in microlitres.
      pre_aspirate: An extra air-gap volume drawn on every aspirate stroke,
        in microlitres.
      blowout: An extra volume dispensed beyond ``volume`` on every
        dispense stroke, in microlitres.
      mix_cycles: Number of aspirate/dispense strokes to perform.
      aspirate_distance: Clearance above the well bottom for the aspirate
        stroke, in millimetres.
      dispense_at_different_distance: Move to ``dispense_distance`` before
        each dispense stroke instead of staying at ``aspirate_distance``.
      dispense_distance: Clearance above the well bottom for the dispense
        stroke, in millimetres, when ``dispense_at_different_distance``.
      dynamic_tip_extension: Millimetres the head lowers per corrected
        microlitre aspirated on each stroke. ``0`` disables it.
      tip_touch: Whether to touch the tip against the well wall after the
        final stroke.
      liquid_class: Pre-resolved per-operation motion parameters, if any.
      pipette_technique: Pre-resolved swirl-technique parameters, if any.
    """
    self._assert_well_access(location)
    labware = self._deck_state.get_stack(location).top
    task = MixTask(
      self._controller,
      self._teachpoints,
      location,
      volume=volume,
      pre_aspirate_volume=pre_aspirate,
      blowout_volume=blowout,
      mix_cycles=mix_cycles,
      aspirate_distance=aspirate_distance,
      dispense_distance=dispense_distance,
      dispense_at_different_distance=dispense_at_different_distance,
      safe_z_position=self._config.safety.z_safe_position,
      labware=labware,
      head_type=self._config.head.head_type,
      head_mode=self._head_mode,
      plate_selection=self._effective_plate_selection(location, labware, self._head_mode),
      dynamic_tip_extension=dynamic_tip_extension,
      tip_touch=tip_touch,
      liquid_class=liquid_class,
      pipette_technique=pipette_technique,
      deck=self._deck_state,
      teach_tip_length_mm=self._config.head.teach_tip_length_mm,
      attached_tip_length_mm=self._attached_tip_length_mm,
      tips_on_head=self._tips_on_head,
    )
    await self._engine.execute(task)

  # -- Tips --

  @staticmethod
  def _labware_base_class(labware: Labware) -> str:
    """Return a labware's normalized ``base_class`` metadata value."""
    return str((labware.metadata or {}).get("base_class") or "").strip().lower()

  @staticmethod
  def _labware_kind(labware: Labware) -> str:
    """Return a labware's normalized ``kind`` metadata value."""
    return str((labware.metadata or {}).get("kind") or "").strip().lower()

  def _labware_at_location(self, location: int) -> Labware:
    """Return the labware at *location*, raising if none is assigned."""
    labware = self._deck_state.get_stack(location).top
    if labware is None:
      raise RuntimeError(f"No labware assigned to location {location}")
    return labware

  def _require_tip_box(self, location: int, *, operation: str) -> Labware:
    """Return the tip box labware at *location*, raising if it is something else."""
    labware = self._labware_at_location(location)
    if self._labware_base_class(labware) != "tip_box" and self._labware_kind(labware) != "tip_box":
      raise RuntimeError(f"{operation} requires a tip box at location {location}")
    return labware

  def _require_well_labware(self, location: int, *, operation: str) -> Labware:
    """Return the well-based labware at *location*, raising if it is a tip box/trash."""
    labware = self._labware_at_location(location)
    base_class = self._labware_base_class(labware)
    kind = self._labware_kind(labware)
    if base_class in {"tip_box", "tip_trash"} or kind in {"tip_box", "tip_trash"}:
      raise RuntimeError(f"{operation} requires plate-style labware at location {location}")
    geometry = well_geometry_from_metadata(labware.metadata)
    if geometry.rows <= 0 or geometry.cols <= 0:
      raise RuntimeError(f"{operation} requires well-based labware at location {location}")
    return labware

  def _require_tip_receptacle(self, location: int, *, operation: str) -> Labware:
    """Return the tip box or tip trash labware at *location*."""
    labware = self._labware_at_location(location)
    base_class = self._labware_base_class(labware)
    kind = self._labware_kind(labware)
    if base_class not in {"tip_box", "tip_trash"} and kind not in {"tip_box", "tip_trash"}:
      raise RuntimeError(f"{operation} requires a tip box or tip trash at location {location}")
    return labware

  def _tip_id_for_labware(self, labware: Labware) -> str:
    """Return the tip catalogue id a tip box's tips resolve to."""
    metadata = labware.metadata or {}
    tip_id = str(metadata.get("tip_definition_id") or "").strip()
    if tip_id:
      return tip_id
    capacity = metadata.get("disposable_tip_capacity_ul")
    inferred = get_tip_id_for_capacity(self._config.head.head_type, capacity)
    if inferred:
      return inferred
    return get_default_tip_id_for_head(self._config.head.head_type) or ""

  def _tip_length_for_labware(self, labware: Labware) -> float:
    """Return the measured length of the tips in a tip box, in millimetres."""
    tip_id = self._tip_id_for_labware(labware)
    length = get_tip_length_mm(self._config.head.head_type, tip_id)
    if length is None:
      length = get_tip_length_mm(
        self._config.head.head_type,
        self._config.head.default_tip_id or self._config.head.default_tip_capacity,
      )
    if length is None:
      raise RuntimeError(f"Tip length is not configured for {self._config.head.head_type}")
    return float(length)

  def _resolve_tip_offsets(self, labware: Labware) -> ResolvedTipOffsets:
    """Resolve per-(head, tip box) Tips On/Off offsets for *labware*."""
    safety = self._config.safety
    return get_tip_offset_table().resolve(
      self._config.head.head_type,
      tipbox_name=labware.name,
      tipbox_id=labware.definition_id or labware.id,
      default_z_offset=float(safety.tips_off_z_offset),
      default_w_position=float(safety.tips_off_w_position),
    )

  @staticmethod
  def _tipbox_dimensions(labware: Labware) -> tuple[int, int]:
    """Return the (rows, cols) of a tip box, from metadata or well count."""
    metadata = labware.metadata or {}
    rows = int(metadata.get("rows") or 0)
    cols = int(metadata.get("cols") or 0)
    if rows > 0 and cols > 0:
      return rows, cols
    wells = int(metadata.get("wells") or 0)
    if wells == 96:
      return 8, 12
    if wells == 384:
      return 16, 24
    if wells == 1536:
      return 32, 48
    return rows, cols

  def _initialize_tipbox_occupancy(
    self, location: int, labware: Labware, *, fill_state: str = "full"
  ) -> None:
    """(Re)populate the occupancy set for a tip box at *location*."""
    if self._labware_base_class(labware) != "tip_box" and self._labware_kind(labware) != "tip_box":
      self._tipbox_occupancy.pop(location, None)
      return
    rows, cols = self._tipbox_dimensions(labware)
    if rows <= 0 or cols <= 0:
      self._tipbox_occupancy.pop(location, None)
      return
    normalized = str(fill_state or "full").strip().lower()
    if normalized == "preserve" and location in self._tipbox_occupancy:
      return
    if normalized == "empty":
      self._tipbox_occupancy[location] = set()
      return
    self._tipbox_occupancy[location] = {(row, col) for row in range(rows) for col in range(cols)}

  def _ensure_tipbox_occupancy(self, location: int, labware: Labware) -> None:
    """Populate the occupancy set for *location* if it has none yet."""
    if location not in self._tipbox_occupancy:
      self._initialize_tipbox_occupancy(location, labware)

  def _occupied_tip_wells(self, location: int) -> set[tuple[int, int]]:
    """Return the currently tip-occupied cells at *location*."""
    return set(self._tipbox_occupancy.get(location, set()))

  def _legal_tip_anchors(
    self, location: int, labware: Labware, head_mode: HeadMode, *, purpose: str
  ) -> list[dict[str, int | str]]:
    """Return every legal tip anchor at *location* for pickup or return."""
    rows, cols = self._tipbox_dimensions(labware)
    if rows <= 0 or cols <= 0:
      return []
    if location in self._tipbox_untracked:
      occupied: set[tuple[int, int]] = (
        {(r, c) for r in range(rows) for c in range(cols)} if purpose == "pickup" else set()
      )
    else:
      self._ensure_tipbox_occupancy(location, labware)
      occupied = self._occupied_tip_wells(location)
    anchors = legal_tipbox_anchors(rows, cols, head_mode, occupied, purpose=purpose)
    return [
      anchor.to_dict()
      for anchor in anchors
      if self._is_tip_anchor_reachable(location, labware, head_mode, anchor.row, anchor.col)
    ]

  def _axis_xy_range(self) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return the ((x_min, x_max), (y_min, y_max)) travel range."""
    x_cfg = self._config.axes.get("x")
    y_cfg = self._config.axes.get("y")
    if x_cfg is None or y_cfg is None:
      raise RuntimeError("Missing X/Y axis configuration")
    return (
      (float(x_cfg.range.min_pos), float(x_cfg.range.max_pos)),
      (float(y_cfg.range.min_pos), float(y_cfg.range.max_pos)),
    )

  def _tip_xy_target(
    self, location: int, labware: Labware, head_mode: HeadMode, anchor_row: int, anchor_col: int
  ) -> tuple[float, float]:
    """Return the (x, y) target for a tip box anchor cell."""
    teach_x = self._teachpoints.get_teachpoint(location, "x")
    teach_y = self._teachpoints.get_teachpoint(location, "y")
    head_offset_x, head_offset_y = head_mode_offsets_mm(self._config.head.head_type, head_mode)
    selection = tipbox_selection(location, anchor_row, anchor_col, head_mode)
    offset_x, offset_y = well_center_offset_from_teachpoint_mm(
      labware.metadata, row=selection.row, col=selection.col
    )
    return teach_x + offset_x - head_offset_x, teach_y + offset_y - head_offset_y

  def _is_tip_anchor_reachable(
    self, location: int, labware: Labware, head_mode: HeadMode, anchor_row: int, anchor_col: int
  ) -> bool:
    """Return whether a tip box anchor's XY target is within axis travel."""
    try:
      target_x, target_y = self._tip_xy_target(location, labware, head_mode, anchor_row, anchor_col)
      (x_min, x_max), (y_min, y_max) = self._axis_xy_range()
    except Exception:
      return False
    epsilon = 1e-6
    return (x_min - epsilon) <= target_x <= (x_max + epsilon) and (y_min - epsilon) <= target_y <= (
      y_max + epsilon
    )

  def _validated_tip_wells(
    self, labware: Labware, head_mode: HeadMode, selection: TipSelection, *, purpose: str = "pickup"
  ) -> list[tuple[int, int]]:
    """Return the tip box cells *selection* covers, raising if it is illegal."""
    rows, cols = self._tipbox_dimensions(labware)
    if rows <= 0 or cols <= 0:
      raise RuntimeError("Tip box metadata is missing rows/cols")
    wells = selected_tip_wells(rows, cols, selection)
    if not wells:
      raise RuntimeError("No tips are selected for the current head mode")
    if self._labware_base_class(labware) == "tip_box" or self._labware_kind(labware) == "tip_box":
      if selection.location not in self._tipbox_untracked:
        self._ensure_tipbox_occupancy(selection.location, labware)
        occupied = self._occupied_tip_wells(selection.location)
        if not is_legal_tipbox_anchor(
          rows, cols, head_mode, occupied, selection.row, selection.col, purpose=purpose
        ):
          raise RuntimeError(
            f"Tip selection ({selection.row}, {selection.col}) is not accessible for {purpose}"
          )
    return wells

  def _effective_tip_selection(
    self, location: int, labware: Labware, head_mode: HeadMode, *, purpose: str = "pickup"
  ) -> TipSelection:
    """Return the tip selection to use at *location*: the operator's choice if
    still legal, otherwise the first legal anchor."""
    if self._tip_selection is not None and self._tip_selection.location == location:
      try:
        self._validated_tip_wells(labware, head_mode, self._tip_selection, purpose=purpose)
        return self._tip_selection
      except RuntimeError:
        pass
    rows, cols = self._tipbox_dimensions(labware)
    if rows <= 0 or cols <= 0:
      raise RuntimeError("Tip box metadata is missing rows/cols")
    anchors = self._legal_tip_anchors(location, labware, head_mode, purpose=purpose)
    if not anchors:
      raise RuntimeError(f"No legal tip anchors are available for {purpose} at location {location}")
    selection = tipbox_selection(
      location, int(anchors[0]["row"]), int(anchors[0]["col"]), head_mode
    )
    self._validated_tip_wells(labware, head_mode, selection, purpose=purpose)
    self._tip_selection = selection
    return selection

  def _selection_from_clicked_tip(
    self,
    location: int,
    labware: Labware,
    head_mode: HeadMode,
    clicked_row: int,
    clicked_col: int,
    *,
    purpose: str,
  ) -> TipSelection:
    """Return the tip selection covering a specific clicked cell, if legal."""
    anchors = self._legal_tip_anchors(location, labware, head_mode, purpose=purpose)
    for anchor in anchors:
      row_start, col_start = int(anchor["row"]), int(anchor["col"])
      row_count, col_count = int(anchor["row_count"]), int(anchor["column_count"])
      if (
        row_start <= clicked_row < row_start + row_count
        and col_start <= clicked_col < col_start + col_count
      ):
        selection = tipbox_selection(location, row_start, col_start, head_mode)
        self._validated_tip_wells(labware, head_mode, selection, purpose=purpose)
        return selection
    selection = tipbox_selection(location, clicked_row, clicked_col, head_mode)
    self._validated_tip_wells(labware, head_mode, selection, purpose=purpose)
    return selection

  def _apply_tipbox_selection(
    self, location: int, head_mode: HeadMode, selection: TipSelection, *, purpose: str
  ) -> None:
    """Update tip occupancy at *location* after a pickup or return."""
    labware = self._require_tip_box(location, operation="Tip inventory update")
    wells = self._validated_tip_wells(labware, head_mode, selection, purpose=purpose)
    if location in self._tipbox_untracked:
      return
    occupied = self._occupied_tip_wells(location)
    if purpose == "pickup":
      occupied.difference_update(wells)
    elif purpose == "return":
      box_tip_id = self._tip_id_for_labware(labware)
      if self._tip_definition_id and box_tip_id and box_tip_id != self._tip_definition_id:
        raise RuntimeError(
          f"Tip return requires matching tip definitions "
          f"({self._tip_definition_id} on head, {box_tip_id} in box)"
        )
      occupied.update(wells)
    else:
      raise ValueError(f"Unknown tip inventory purpose: {purpose}")
    self._tipbox_occupancy[location] = occupied

  def _set_tip_state(
    self,
    *,
    tip_definition_id: str,
    tip_length_mm: float,
    head_mode: HeadMode,
    tip_selection: TipSelection,
  ) -> None:
    """Record that tips are now on the head."""
    self._tips_on_head = True
    self._tip_definition_id = tip_definition_id
    self._attached_tip_length_mm = float(tip_length_mm)
    self._tips_on_head_mode = head_mode
    self._tip_selection = tip_selection

  def _clear_tip_state(self) -> None:
    """Record that tips are no longer on the head."""
    self._tips_on_head = False
    self._tip_definition_id = ""
    self._attached_tip_length_mm = None
    self._tips_on_head_mode = None

  async def tips_on(self, location: int) -> None:
    """Pick up tips from a tip box.

    Args:
      location: The deck location of the tip box.
    """
    if self._tips_on_head:
      raise RuntimeError("Tips are already on the head")
    labware = self._require_tip_box(location, operation="Tips on")
    tip_selection = self._effective_tip_selection(location, labware, self._head_mode)
    tip_length_mm = self._tip_length_for_labware(labware)
    tip_offsets = self._resolve_tip_offsets(labware)
    task = TipsOnTask(
      self._controller,
      self._teachpoints,
      self._config,
      labware,
      self._head_mode,
      tip_selection,
      location,
      tip_length_mm,
      safe_z_position=self._config.safety.z_safe_position,
      deck=self._deck_state,
      tip_offsets=tip_offsets,
    )
    await self._engine.execute(task)
    self._apply_tipbox_selection(location, self._head_mode, tip_selection, purpose="pickup")
    self._set_tip_state(
      tip_definition_id=self._tip_id_for_labware(labware),
      tip_length_mm=tip_length_mm,
      head_mode=self._head_mode,
      tip_selection=tip_selection,
    )

  async def tips_off(self, location: int) -> None:
    """Eject tips at a tip box or tip trash location.

    Args:
      location: The deck location to eject at.
    """
    tips_are_tracked = self._tips_on_head
    tip_length = self._attached_tip_length_mm or 9.0
    labware = self._require_tip_receptacle(location, operation="Tips off")
    target_selection: Optional[TipSelection] = None
    effective_mode = self._tips_on_head_mode or self._head_mode
    if self._labware_base_class(labware) == "tip_box" or self._labware_kind(labware) == "tip_box":
      target_selection = self._effective_tip_selection(
        location, labware, effective_mode, purpose="return"
      )
    tip_offsets = self._resolve_tip_offsets(labware)
    task = TipsOffTask(
      self._controller,
      self._teachpoints,
      self._config,
      labware,
      effective_mode,
      target_selection,
      location,
      attached_tip_length_mm=tip_length,
      safe_z_position=self._config.safety.z_safe_position,
      deck=self._deck_state,
      tips_are_tracked=tips_are_tracked,
      tip_offsets=tip_offsets,
    )
    await self._engine.execute(task)
    if target_selection is not None:
      self._apply_tipbox_selection(location, effective_mode, target_selection, purpose="return")
    self._clear_tip_state()

  def set_tip_selection(self, location: int, row: int, col: int) -> TipSelection:
    """Manually select a tip box anchor cell.

    Args:
      location: The deck location of the tip box.
      row: Zero-based row of the clicked cell.
      col: Zero-based column of the clicked cell.

    Returns:
      The resolved selection covering the clicked cell.
    """
    labware = self._require_tip_box(location, operation="Tip selection")
    rows, cols = self._tipbox_dimensions(labware)
    if rows <= 0 or cols <= 0:
      raise RuntimeError(f"Tip box at location {location} has no row/column metadata")
    if row < 0 or row >= rows or col < 0 or col >= cols:
      raise RuntimeError(
        f"Tip selection ({row}, {col}) is outside the tip box at location {location}"
      )
    purpose = "return" if self._tips_on_head else "pickup"
    active_mode = (
      (self._tips_on_head_mode or self._head_mode) if self._tips_on_head else self._head_mode
    )
    selection = self._selection_from_clicked_tip(
      location, labware, active_mode, row, col, purpose=purpose
    )
    self._tip_selection = selection
    return selection

  # -- Plate selection --

  def _plate_xy_target(
    self, location: int, labware: Labware, head_mode: HeadMode, selection: PlateSelection
  ) -> tuple[float, float]:
    """Return the (x, y) target for a plate anchor well."""
    teach_x = self._teachpoints.get_teachpoint(location, "x")
    teach_y = self._teachpoints.get_teachpoint(location, "y")
    offset_x, offset_y = well_center_offset_from_teachpoint_mm(
      labware.metadata, row=selection.row, col=selection.col
    )
    head_offset_x, head_offset_y = head_mode_offsets_mm(self._config.head.head_type, head_mode)
    return teach_x + offset_x - head_offset_x, teach_y + offset_y - head_offset_y

  def _is_plate_anchor_reachable(
    self, location: int, labware: Labware, head_mode: HeadMode, row: int, col: int
  ) -> bool:
    """Return whether a plate anchor's XY target is within axis travel."""
    try:
      selection = plate_selection(location, row, col)
      target_x, target_y = self._plate_xy_target(location, labware, head_mode, selection)
      (x_min, x_max), (y_min, y_max) = self._axis_xy_range()
    except Exception:
      return False
    epsilon = 1e-6
    return (x_min - epsilon) <= target_x <= (x_max + epsilon) and (y_min - epsilon) <= target_y <= (
      y_max + epsilon
    )

  def _is_legal_plate_anchor(
    self, labware: Labware, head_mode: HeadMode, row: int, col: int
  ) -> bool:
    """Return whether the head mode's footprint fits the plate anchored at a cell."""
    geometry = well_geometry_from_metadata(labware.metadata)
    if geometry.rows <= 0 or geometry.cols <= 0:
      return False
    return is_legal_plate_anchor(
      self._config.head.head_type,
      head_mode,
      geometry.rows,
      geometry.cols,
      geometry.pitch_x_mm,
      geometry.pitch_y_mm,
      row,
      col,
    )

  def _assert_plate_anchor_clearance(
    self,
    location: int,
    labware: Labware,
    head_mode: HeadMode,
    row: int,
    col: int,
    *,
    command_name: str = "Plate selection",
  ) -> None:
    """Raise if a plate anchor's footprint would clip a taller neighbor."""
    selection = plate_selection(location, row, col)
    target_x, target_y = self._plate_xy_target(location, labware, head_mode, selection)
    target_height = self._deck_state.get_height(location)
    tip_length = self._attached_tip_length_mm or 0.0
    allowed_top_plane = target_height + tip_length - _NEIGHBOR_CLEARANCE_SAFETY_MM
    _assert_neighbor_clearance(
      command_name=command_name,
      teachpoints=self._teachpoints,
      deck=self._deck_state,
      head_type=self._config.head.head_type,
      head_mode=head_mode,
      target_location=location,
      target_x=target_x,
      target_y=target_y,
      allowed_top_plane_mm=allowed_top_plane,
      gripper_present=self._controller.has_gripper,
    )

  def _is_plate_anchor_selectable(
    self, location: int, labware: Labware, head_mode: HeadMode, row: int, col: int
  ) -> bool:
    """Return whether a plate anchor is reachable, legal, and clear of neighbors."""
    if not self._is_plate_anchor_reachable(location, labware, head_mode, row, col):
      return False
    if not self._is_legal_plate_anchor(labware, head_mode, row, col):
      return False
    try:
      self._assert_plate_anchor_clearance(location, labware, head_mode, row, col)
    except RuntimeError:
      return False
    return True

  def _candidate_plate_anchors(
    self, location: int, labware: Labware, head_mode: HeadMode
  ) -> "list[dict[str, int]]":
    """Return every reachable plate anchor cell, regardless of clearance."""
    geometry = well_geometry_from_metadata(labware.metadata)
    anchors = legal_plate_anchors(
      self._config.head.head_type,
      head_mode,
      geometry.rows,
      geometry.cols,
      geometry.pitch_x_mm,
      geometry.pitch_y_mm,
    )
    return [
      {"row": anchor.row, "col": anchor.col}
      for anchor in anchors
      if self._is_plate_anchor_reachable(location, labware, head_mode, anchor.row, anchor.col)
    ]

  def _legal_plate_anchors(
    self, location: int, labware: Labware, head_mode: HeadMode
  ) -> "list[dict[str, int]]":
    """Return every plate anchor that is reachable, legal, and clear of neighbors."""
    return [
      anchor
      for anchor in self._candidate_plate_anchors(location, labware, head_mode)
      if self._is_plate_anchor_selectable(
        location, labware, head_mode, anchor["row"], anchor["col"]
      )
    ]

  def _effective_plate_selection(
    self, location: int, labware: Optional[Labware], head_mode: HeadMode
  ) -> Optional[PlateSelection]:
    """Return the plate selection to use at *location*, or ``None`` for a tip box/no labware."""
    if labware is None:
      return None
    if self._labware_base_class(labware) in {"tip_box", "tip_trash"} or self._labware_kind(
      labware
    ) in {"tip_box", "tip_trash"}:
      return None
    geometry = well_geometry_from_metadata(labware.metadata)
    if geometry.rows <= 0 or geometry.cols <= 0:
      return None
    current = self._plate_selection.get(location)
    if current is not None:
      try:
        if self._is_plate_anchor_selectable(location, labware, head_mode, current.row, current.col):
          return current
      except Exception:
        pass
    candidates = self._candidate_plate_anchors(location, labware, head_mode)
    legal = [
      anchor
      for anchor in candidates
      if self._is_plate_anchor_selectable(
        location, labware, head_mode, anchor["row"], anchor["col"]
      )
    ]
    if not legal:
      if candidates:
        first = candidates[0]
        self._assert_plate_anchor_clearance(
          location, labware, head_mode, first["row"], first["col"]
        )
      raise RuntimeError(
        f"No legal plate anchors are available at location {location} for the current head mode"
      )
    selection = plate_selection(location, legal[0]["row"], legal[0]["col"])
    self._plate_selection[location] = selection
    return selection

  def set_plate_selection(self, location: int, row: int, col: int) -> PlateSelection:
    """Manually select a plate anchor well.

    Args:
      location: The deck location of the plate.
      row: Zero-based row of the anchor well.
      col: Zero-based column of the anchor well.

    Returns:
      The selection.
    """
    labware = self._require_well_labware(location, operation="Plate selection")
    geometry = well_geometry_from_metadata(labware.metadata)
    if row < 0 or row >= geometry.rows or col < 0 or col >= geometry.cols:
      raise RuntimeError(
        f"Plate selection ({row}, {col}) is outside the labware at location {location}"
      )
    if not self._is_plate_anchor_reachable(location, labware, self._head_mode, row, col):
      raise RuntimeError(f"Plate selection ({row}, {col}) is not reachable at location {location}")
    if not self._is_legal_plate_anchor(labware, self._head_mode, row, col):
      raise RuntimeError(f"Plate selection ({row}, {col}) is not legal for the current head mode")
    self._assert_plate_anchor_clearance(location, labware, self._head_mode, row, col)
    selection = plate_selection(location, row, col)
    self._plate_selection[location] = selection
    return selection

  # -- Gripper --

  async def gripper_pick(self, location: int, speed: SpeedLevel = "med") -> None:
    """Pick up the labware at *location* with the gripper.

    Lifts to a height that clears the source stack. The lift does not yet
    account for the eventual destination -- :meth:`gripper_place` climbs
    further first if the destination needs more clearance.

    Args:
      location: The deck location to pick up from.
      speed: The speed profile for XYZ/Zg motion.

    Raises:
      RuntimeError: If the gripper is already holding a resource, or no
        labware is assigned at *location*.
    """
    if self._gripper_held_task is not None:
      raise RuntimeError("The gripper is already holding a resource")
    task = _GripperPickPhase(
      self._controller, self._teachpoints, self._config, self._deck_state, location, speed=speed
    )
    await self._engine.execute(task)
    self._gripper_held_task = task
    self._gripper_pick_location = location

  async def gripper_move(self, location: int) -> None:
    """Translate the held resource, at its current height, to hover over *location*.

    Args:
      location: The deck location to hover over.

    Raises:
      RuntimeError: If the gripper is not currently holding a resource.
    """
    task = self._gripper_held_task
    if task is None:
      raise RuntimeError("The gripper is not holding a resource")
    _, head_y_offset = _gripper_head_offsets(self._config.head.head_type)
    x = self._teachpoints.get_teachpoint(location, "x")
    y = (
      self._teachpoints.get_teachpoint(location, "y")
      + self._config.gripper.y_offset
      + head_y_offset
    )
    self._controller.move(
      [AxisMoveInfo(axis="x", position=x), AxisMoveInfo(axis="y", position=y)], wait=True
    )

  async def gripper_place(self, location: int, speed: SpeedLevel = "med") -> None:
    """Place the held resource at *location* and release the gripper.

    Args:
      location: The deck location to place at.
      speed: The speed profile for XYZ/Zg motion.

    Raises:
      RuntimeError: If the gripper is not currently holding a resource.
    """
    if self._gripper_held_task is None or self._gripper_pick_location is None:
      raise RuntimeError("The gripper is not holding a resource")
    task = _GripperPlacePhase(
      self._controller,
      self._teachpoints,
      self._config,
      self._deck_state,
      self._gripper_pick_location,
      location,
      speed=speed,
    )
    await self._engine.execute(task)
    self._gripper_held_task = None
    self._gripper_pick_location = None

  # -- Deck --

  def set_labware(
    self,
    location: int,
    labware: Labware,
    *,
    tipbox_fill_state: str = "full",
    track_tips: bool = True,
  ) -> None:
    """Assign *labware* to a deck location, replacing whatever was there.

    This facade has no built-in catalogue: *labware* must already be a
    fully-built :class:`~.deck.labware.Labware`. A caller bridging from
    PyLabRobot resources gets one from
    :meth:`~.deck.resource.BravoDeck.labware_for_site` (or the underlying
    :func:`~.deck.resource.labware_from_resource`), which translates
    whatever :class:`~pylabrobot.resources.resource.Resource` is assigned
    to a :class:`~.deck.resource.BravoDeck` site into this type.

    Args:
      location: The deck location to assign to.
      labware: The labware to place there.
      tipbox_fill_state: For a tip box, its initial occupancy: ``"full"``,
        ``"empty"``, or ``"preserve"`` (keep whatever occupancy the
        location already tracked).
      track_tips: Whether tip occupancy at this location should be tracked
        at all. ``False`` treats every cell as always available for pickup
        and always available for return, skipping legality checks.
    """
    self._deck_state.set_single(location, labware)
    self._plate_selection.pop(location, None)
    self._initialize_tipbox_occupancy(location, labware, fill_state=tipbox_fill_state)
    if track_tips:
      self._tipbox_untracked.discard(location)
    else:
      self._tipbox_untracked.add(location)

  def clear_labware(self, location: int) -> None:
    """Remove whatever labware is assigned to a deck location.

    Args:
      location: The deck location to clear.
    """
    self._deck_state.clear(location)
    self._plate_selection.pop(location, None)
    self._tipbox_occupancy.pop(location, None)

  def get_labware(self, location: int) -> Optional[Labware]:
    """Return the labware currently assigned to a deck location, if any."""
    return self._deck_state.get_stack(location).top

  # -- Head mode --

  @property
  def head_mode(self) -> HeadMode:
    """The active head mode/subset."""
    return self._head_mode

  def set_head_mode(
    self,
    subset_type: Optional[str],
    subset_config: Optional[str],
    row_count: Optional[int] = None,
    column_count: Optional[int] = None,
  ) -> HeadMode:
    """Set the active head mode/subset.

    Args:
      subset_type: The requested subset kind, e.g. ``"row"``, ``"column"``,
        ``"rectangle"``, ``"single_barrel"``, or ``"all_barrels"``.
      subset_config: The requested anchor corner.
      row_count: Requested active row count, for ``"row"``/``"rectangle"``.
      column_count: Requested active column count, for
        ``"column"``/``"rectangle"``.

    Returns:
      The normalized head mode now active.
    """
    self._head_mode = normalize_head_mode(
      self._config.head.head_type, subset_type, subset_config, row_count, column_count
    )
    return self._head_mode

  # -- Head/instrument identity --

  @property
  def head_type(self) -> HeadType:
    """The configured head type this facade operates against.

    This is the value every task built by this facade is given as its own
    ``head_type`` argument (see :meth:`aspirate`, :meth:`tips_on`, and
    friends), so it is the authoritative "installed head" for any caller
    that needs to reason about geometry before an operation reaches the
    engine -- for example, rejecting a request the installed head cannot
    physically perform rather than letting :func:`~.head_mode.normalize_head_mode`
    silently clamp it to a smaller shape. ``"unknown"`` means no head has
    been identified yet; :attr:`head_geometry` and :attr:`head_capacity`
    still return the 96-channel default for it, so a caller that needs to
    guarantee a real head is installed should check for ``"unknown"``
    explicitly rather than trust those.
    """
    return self._config.head.head_type

  @property
  def head_geometry(self) -> HeadGeometry:
    """The physical barrel grid of the installed head."""
    return head_geometry_for_type(self._config.head.head_type)

  @property
  def head_capacity(self) -> int:
    """The total number of physical channels on the installed head."""
    geometry = self.head_geometry
    return geometry.rows * geometry.columns

  @property
  def has_gripper(self) -> bool:
    """Whether the controller's hardware has a gripper accessory."""
    return self._controller.has_gripper

  @property
  def model_name(self) -> str:
    """The controller's human-readable model name."""
    return self._controller.model_name

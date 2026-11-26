"""One pod: an arm on the shared X rail, its ACE module, and the axes that module drives."""

import asyncio
import contextlib
import itertools
import logging
from dataclasses import dataclass, field
from typing import (
  TYPE_CHECKING,
  Any,
  Callable,
  Dict,
  Iterable,
  Iterator,
  List,
  Literal,
  Mapping,
  Optional,
  Sequence,
  Set,
  Tuple,
  Union,
  cast,
)

from pylabrobot.beckman_coulter.biomek_i7 import targeting, units
from pylabrobot.beckman_coulter.biomek_i7.driver import motion
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  ModuleConfiguration,
  PodConfiguration,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import AceError, NotHomedError, english
from pylabrobot.beckman_coulter.biomek_i7.driver.features.gripper import Gripper
from pylabrobot.beckman_coulter.biomek_i7.driver.features.head96 import Head96
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pipettes import Pipettes
from pylabrobot.beckman_coulter.biomek_i7.driver.module import (
  read_analog_inputs,
  read_module,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.motors import Axis, AxisMove, MotorTables
from pylabrobot.beckman_coulter.biomek_i7.driver.planning import (
  check,
  destinations,
  find_path,
  merge,
  obstacles,
  pod_model,
  traverse,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.homing import (
  ENABLE_ORDER,
  ENABLE_PREFIX,
  HOME_RECIPES,
  Homer,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.x_rail import (
  RailCollisionError,
  RailPose,
  XRail,
)

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
  from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)

# How Biomek numbers its pods. Pod1 is the left pod and Pod2 the right on the instrument this
# driver was written against; whether that holds on every build is not established.
POD_NUMBERS = {"left": 1, "right": 2}
COMPONENT_PREFIXES = {"multichannel": "MC", "span8": "SPAN8"}
# What each module type carries besides the pod's own axes, by the feature's name on `Pod`. The
# module type is what the board reports (Get Module ID, `protocol.MODULE_TYPES`); a new pod type is
# a new entry here and in the motor tables. The gripper is not here: it is fitted where the declared
# configuration describes one.
MODULE_FEATURES: Dict[str, Tuple[str, ...]] = {
  "multichannel": ("head96",),
  "span8": ("pipettes",),
}
# The axes that change what a pod takes up along the X rail.
RAIL_AXES = ("X", "GR", "GG")
# The gripper's axes; a move of these, or of the channels' Y or Z, is checked against the pod's own
# parts (`planning.check`).
OWN_COLLISION_AXES = {"GY", "GZ", "GR", "GG"}


# A planned move (axis targets, counts) and the deck positions it leaves straight up (its way out of
# them, made with them exempt); nothing for every other move.
PlanStep = Tuple[Dict[str, int], Tuple[str, ...]]

# Get Homed State answers. 0x01 was read from every axis at a cold start, 0x00 from every axis of
# both pods after Biomek's Home All (H1, 2026-10-24).
HOMED_STATES = {0x00: "homed", 0x01: "not_homed"}

# The timeouts the Master Controller gives the boards: a single axis's move, and a Move All.
MOVE_TIMEOUT_MS = 30000
MOVE_ALL_TIMEOUT_MS = 5000


# Degrees within which GR is taken to be at an angle (`_at_angle`): well under a count's worth of
# difference matters nowhere. A whole turn away is another angle, as Biomek's GR is a line.
GR_SAME_ANGLE = 0.01

# The arm board's analog input carrying the interlocked motor supply ("+36V Interlock").
DRIVE_SUPPLY_INPUT = "+36V Interlock"

# Set State ENABLED on a CiA 402 drive resets a fault, then steps the drive through shutdown, switch
# on and enable operation, waiting up to 3 s for each. Whether a board enables
# a batch one drive at a time is not known, so a batch is given that per axis; it is a bound for
# a drive that does not come up, not how long enabling takes.
ENABLE_SECONDS_PER_AXIS = 9.0


@dataclass(frozen=True)
class MoveResult:
  """What a coordinated move did: the motions it sent (empty when every axis was already there),
  and each axis's report that was not a plain finish. A sensor stop says where
  (`MotionComplete.position`)."""

  motions: List[motion.Motion] = field(default_factory=list)
  completions: Dict[str, p.MotionComplete] = field(default_factory=dict)

  def stopped_at(self, axis: str) -> Optional[int]:
    """Where a sensor stopped `axis`, in counts; None if none did."""
    complete = self.completions.get(axis)
    return complete.position if complete is not None else None


# How long this driver waits for a board to answer a Home, in seconds. The longest homing seen
# took 9 s; the boards' own homing timeouts (Configure "Homing Timeout") are up to 60 s.
HOME_REPLY_TIMEOUT = 65.0


class Pod:
  """One pod. Reached as `driver.left_x_arm` / `driver.right_x_arm`: it fills the slot of STAR's
  `XArm`, though more of the instrument hangs on it. One board drives its X, Y and Z, its head
  or channels and its gripper, through one position record.

  Holds what every pod has: one board and its axes, moved together (`move_axes`, the Master
  Controller's `AM`), the position record the Master Controller keeps, homing, and the pod's pose
  on the shared X rail. `module_type` says what else the pod carries, as features
  (`MODULE_FEATURES`): `multichannel` a 96-channel head (`head96`), `span8` eight independent
  channels (`pipettes`); either a `gripper` where the declared configuration describes one.
  """

  def __init__(
    self,
    board: AceBoard,
    side: Literal["left", "right"],
    module_type: str,
    motor_tables: MotorTables,
    declared: Optional[PodConfiguration] = None,
  ):
    self.board = board
    self.side = side
    self.module_type = module_type
    self.axes: List[Axis] = motor_tables.axes(module_type)
    self.configuration: Optional[ModuleConfiguration] = None
    # What the instrument's declared configuration says about this pod: its conversions, travel
    # limits and geometry. None when nothing is declared for this side.
    self.declared = declared
    # The driver this pod belongs to, set at discovery: what the features reach the deck and the
    # declared deck positions through. None for a pod used on its own.
    self.driver: Optional["BiomekI7Driver"] = None
    # The shared X rail, set by the driver once both pods are found. Every move that changes what
    # the pod takes up along it (X, the gripper's GR or GG) goes through it.
    self.rail: Optional[XRail] = None
    # The pod's model in the resource tree, its bridge (STAR's `x_arm.resource`): set by the device
    # that draws it (`BiomekI7`), which keeps it in step with the pod's axes; None without one.
    self.resource: Optional["Resource"] = None
    # What the gripper holds, as the rail counts it: set through `gripper.set_held_labware`; the
    # pod cannot sense it.
    self._held_labware: Optional[g.HeldLabware] = None
    # What it held at its last move checked on the rail: the first move after it takes or lets go
    # of labware (its range changed with it) is checked there, whatever axes it moves.
    self._rail_held: Optional[g.HeldLabware] = None
    # The deck positions the operation under way works in (`working_at`): the deck check leaves
    # their boxes out, whole, as Biomek's moves name their target position.
    self._working_at: Tuple[str, ...] = ()
    # Held for each move (`move_axes`): one move at a time on the pod's board, whichever task
    # sends it.
    self._motion = asyncio.Lock()
    # Where this driver last put each axis, as the Master Controller keeps it (`cpositions`):
    # (position, adjusted); position None until read. Starts as after homing.
    self._cpositions: Dict[str, motion.CPos] = {a.name: (None, a.home_position) for a in self.axes}
    # The pumps' state, kept with the positions as the Master Controller keeps it in the Span-8's
    # state record, because every pump move carries it; set through `pipettes`. Each syringe
    # pump's valve as last set (None until then), and the cutoff velocity (`CV`), counts/s,
    # clamped to each pump's range when used.
    self._valves: Dict[str, Optional[p.Valve]] = {a.name: None for a in self.axes if a.is_pump}
    self._cutoff_velocity = 0
    # Set while the interlock is tripped, shared by the driver (`BiomekI7Driver.interlock_tripped`);
    # None for a pod used without one. Waits that the Master Controller abandons on a trip watch it.
    self.interlock_tripped: Optional[asyncio.Event] = None
    # How each tool (the channels, the gripper) travels by default (`Head96`/`Pipettes`/`Gripper`
    # `default_minimum_traverse_height`): "safe", Biomek's safe height over its way (`_safe_heights`);
    # "max", as high as it goes; or a height, mm of its lowest point above the deck. From the
    # pod's `AlwaysMaxZ`: "max" for every tool when set, else "safe".
    start: traverse.TraverseDefault = (
      "max" if declared is not None and declared.settings["AlwaysMaxZ"] else "safe"
    )
    self.traverse_defaults: Dict[str, traverse.TraverseDefault] = {
      "channels": start,
      "gripper": start,
    }
    # Who follows the position record (`add_position_listener`).
    self._listeners: List[Callable[["Pod"], None]] = []
    # Who follows the pod's motion (`add_motion_listener`).
    self._motion_listeners: List[Callable[["Pod", str, Any], None]] = []
    # What the pod carries (STAR's arm features): what its module type has (`MODULE_FEATURES`), and
    # a gripper where the declared configuration describes one, as the rail counts one only then.
    features = MODULE_FEATURES.get(module_type, ())
    self.head96: Optional[Head96] = Head96(self) if "head96" in features else None
    self.pipettes: Optional[Pipettes] = Pipettes(self) if "pipettes" in features else None
    self.gripper: Optional[Gripper] = (
      Gripper(self) if declared is not None and declared.gripper_model else None
    )

  @property
  def pod_number(self) -> int:
    return POD_NUMBERS[self.side]

  def __str__(self) -> str:
    return f"{self.side} pod (Pod{self.pod_number}, {self.module_type})"

  @property
  def component(self) -> str:
    """The name Biomek gives this pod's component in its logs (`host_command.component`):
    `MC_1`, `SPAN8_2`. The driver's own log lines for host commands use it too, so a session can
    be lined up with one of Biomek's."""
    return f"{COMPONENT_PREFIXES[self.module_type]}_{self.pod_number}"

  def axis(self, name: str) -> Axis:
    for axis in self.axes:
      if axis.name == name:
        return axis
    raise ValueError(f"{self} has no axis {name!r}; it has {[a.name for a in self.axes]}")

  async def discover(self) -> ModuleConfiguration:
    """Read what the pod's module says about itself. Moves nothing."""
    self.configuration = await read_module(self.board)
    return self.configuration

  async def request_positions(self) -> Dict[str, int]:
    """Every axis's current position, in counts, as the drives report it."""
    replies = await self.board.send_many(
      [p.get_current_position(axis.device, axis.index) for axis in self.axes]
    )
    return {axis.name: p.parse_s32(r.payload) for axis, r in zip(self.axes, replies)}

  async def request_axis_states(self) -> Dict[str, p.AxisStateName]:
    """Every axis's state (motors and pumps), sent together: `enabled` (it may move), `disabled`
    (configured, not enabled), `not_configured` (as at power-up) or `fault`. Moves nothing.

    Raises:
      ValueError: for a state byte the firmware does not set.
    """
    replies = await self.board.send_many([p.get_state(a.device, a.index) for a in self.axes])
    return {
      a.name: cast(p.AxisStateName, p.parse_axis_state(r.payload).name.lower())
      for a, r in zip(self.axes, replies)
    }

  async def request_drive_supply(self) -> float:
    """The pod's interlocked motor supply, in volts: about 36 with the interlock armed, about 0.1
    tripped. Moves nothing.

    Raises:
      RuntimeError: if the pod has no input named for it.
    """
    if self.configuration is None:
      raise RuntimeError(f"{self} has not been read; call `discover()` or `setup()` first")
    for index, name in self.configuration.analog_inputs.items():
      if name.startswith(DRIVE_SUPPLY_INPUT):
        reply = await self.board.send(p.read_input_output(p.Device.ANALOG_INPUT, index))
        return p.parse_float(reply.payload)
    raise RuntimeError(f"{self} has no {DRIVE_SUPPLY_INPUT!r} analog input")

  async def request_homed_state(self, axis_name: str) -> str:
    """Whether an axis (a motor or a pump) has been homed: `homed`, `not_homed`, or the raw code
    of a state not yet seen."""
    axis = self.axis(axis_name)
    code = (await self.board.send(p.get_homed_state(axis.device, axis.index))).payload[0]
    return HOMED_STATES.get(code, f"0x{code:02X}")

  async def request_accuframe_offsets(self) -> Tuple[float, float, float]:
    """The X, Y and Z offsets the pod's AccuFrame board holds."""
    return p.parse_accuframe_offsets((await self.board.send(p.get_accuframe_offsets())).payload)

  async def request_analog_inputs(self) -> Dict[str, float]:
    if self.configuration is None:
      raise RuntimeError(f"{self} has not been read; call `discover()` or `setup()` first")
    return await read_analog_inputs(self.board, self.configuration)

  # -- configuration and homing ------------------------------------------------
  # These write to the board and move the arm. Nothing in this driver calls them on its own.

  async def configure(self) -> None:
    """Load every axis's motor table into the board, then Stop Module Execution, as after a
    power-up. The tables come from the motor recording; the board cannot be asked for them.

    Configuring an axis that is configured is accepted, and leaves it disabled and not homed, so
    this un-homes the pod."""
    logger.info("%s configure (%d axes)", self.component, len(self.axes))
    self._set_home_positions(a.name for a in self.axes)
    for axis in self.axes:
      await self._unchecked_fw_configure(axis)
    await self._unchecked_fw_stop_module_execution()

  async def enable_axes(self) -> Dict[str, int]:
    """Stop motion and enable every axis, as after the interlock is armed (its drive power was
    off), and read where each is, as the Master Controller resumes: the positions cleared, then
    each axis enabled and read. Returns every axis's position."""
    axes = [self.axis(name) for name in ENABLE_ORDER[self.module_type]]
    self._forget_positions()
    await self._unchecked_fw_enable(ENABLE_PREFIX[self.module_type])
    await self._unchecked_fw_stop_motion()
    await self._unchecked_fw_enable(ENABLE_ORDER[self.module_type])
    replies = await self.board.send_many([p.get_current_position(a.device, a.index) for a in axes])
    positions = {a.name: p.parse_s32(r.payload) for a, r in zip(axes, replies)}
    # As the Master Controller does, each read is the axis's position.
    for axis in axes:
      self._cpositions[axis.name] = motion.update_cpos(
        self._cpositions[axis.name], positions[axis.name], axis.hysteresis
      )
    self._positions_changed()
    return positions

  async def home(self, force: bool = False) -> None:
    """Home every axis but the plunger (or syringe pumps), in the Master Controller's `HM` order.

    Axes not homed are homed. Axes already homed are moved to where a Home would leave them, or,
    with `force`, moved to their approach positions and homed again as `HM` does. Each ends where
    Biomek leaves it."""
    logger.info("%s HM%s", self.component, "" if force else " (homed axes kept)")
    try:
      await Homer(self, force=force).run(HOME_RECIPES[self.module_type])
    finally:
      self._set_home_positions(Homer.axes(HOME_RECIPES[self.module_type]))

  # -- coordinated moves -------------------------------------------------------
  # The Master Controller's `AM`, with its position bookkeeping: see `motion.py`.

  @property
  def cpositions(self) -> Dict[str, motion.CPos]:
    """Each axis's (position, adjusted), as this driver keeps them; position None until read."""
    return dict(self._cpositions)

  def _set_home_positions(self, names: Iterable[str]) -> None:
    """After homing: position unknown, adjusted at the axis's home."""
    for name in names:
      self._cpositions[name] = (None, self.axis(name).home_position)
    self._positions_changed()

  def _forget_positions(
    self,
    names: Optional[Iterable[str]] = None,
    adjusted: Optional[Mapping[str, motion.CPos]] = None,
  ) -> None:
    """Mark positions unknown, so the next move reads them first; adjusted
    positions stay, or are taken from `adjusted`."""
    for name in list(self._cpositions) if names is None else names:
      self._cpositions[name] = (None, (adjusted or self._cpositions)[name][1])
    self._positions_changed()

  async def _read_unknown_positions(self, pumps: bool = False) -> None:
    """As the Master Controller does before every move: if any axis's position is
    unknown, Stop Motion Service, then enable and read each such axis. Syringe pumps are included
    only when the move uses them (`pumps`)."""
    unknown = [
      a for a in self.axes if (pumps or not a.is_pump) and self._cpositions[a.name][0] is None
    ]
    if not unknown:
      return
    await self._unchecked_fw_stop_motion()
    await self._unchecked_fw_enable([a.name for a in unknown])
    replies = await self.board.send_many(
      [p.get_current_position(a.device, a.index) for a in unknown]
    )
    for axis, reply in zip(unknown, replies):
      position = p.parse_s32(reply.payload)
      self._cpositions[axis.name] = motion.update_cpos(
        self._cpositions[axis.name], position, axis.hysteresis
      )
    self._positions_changed()

  async def request_x_position(self) -> int:
    """Where the pod's X is, in counts, as its drive reports it. Moves nothing."""
    x = self.axis("X")
    return p.parse_s32((await self.board.send(p.get_current_position(x.device, x.index))).payload)

  # -- the pod against itself ----------------------------------------------------

  def _pose(
    self, positions: Optional[Mapping[str, motion.CPos]] = None
  ) -> Optional[pod_model.PodState]:
    """The pod's pose in Biomek's units from `positions` (the position record by default): X, the
    gripper's (None on a pod without one) and the pipettor's; None while any axis is unknown."""
    d = self.declared
    if d is None:
      return None
    positions = self._cpositions if positions is None else positions
    model = pod_model.PodModel.of(d)
    counts = {n: positions[n][0] for n in model.pose_axes}
    if any(c is None for c in counts.values()):
      return None
    lengths = self._carried_tip_lengths()
    block = self.head96._tip_block() if self.head96 is not None and lengths else None
    return model.state(cast(Dict[str, int], counts), self._held_labware, lengths, block)

  def _check_own_collision(
    self,
    start: Mapping[str, motion.CPos],
    end: Mapping[str, motion.CPos],
    moving: Set[str],
  ) -> None:
    """Refuse a move whose path brings the gripper (or what it holds) into the pipettor.

    Raises:
      PodCollisionError: naming the parts that would meet; nothing has been sent.
    """
    if not moving & OWN_COLLISION_AXES and not any(a[:1] in "YZ" for a in moving):
      return
    a, b = self._pose(start), self._pose(end)
    if a is None or b is None or self.declared is None:
      return
    found = check.own_collisions(self.declared, a, b)
    if found:
      parts = ", ".join(f"the {x} and {y}" for x, y in found[:3])
      raise check.PodCollisionError(f"{self}: the move would bring together {parts}")

  # -- the pod against the deck and the instrument --------------------------------

  @contextlib.contextmanager
  def working_at(self, *positions: Union[str, "Resource"]) -> Iterator[None]:
    """Within it, the deck check leaves the deck positions given (names, or resources on them)
    out, whole: the ones the operation works in, whose labware it goes into by design, as each of
    Biomek's moves names its target position. Their own obstacles (a trash's walls) stay in."""
    names = []
    for where in positions:
      if isinstance(where, str):
        names.append(where)
        continue
      deck = self.driver.deck if self.driver is not None else None
      position = targeting.deck_position_of(deck, where) if deck is not None else None
      if position is not None:
        names.append(position.name)
        continue
      # On no position: the labware it is in has a box of its own (`obstacles` keeps only names
      # that are boxes).
      node: Optional["Resource"] = where
      while node is not None:
        names.append(node.name)
        node = node.parent
    before = self._working_at
    self._working_at = (*before, *names)
    try:
      yield
    finally:
      self._working_at = before

  def _check_world_collision(
    self, start: Mapping[str, motion.CPos], end: Mapping[str, motion.CPos]
  ) -> None:
    """Refuse a move whose path brings any of the pod's parts (or what its gripper holds) into the
    deck's positions, their obstacles or the instrument's structure (the driver's `obstacles`).
    Nothing without them.

    Raises:
      DeckCollisionError: naming the parts that would meet; nothing has been sent.
    """
    if self.declared is None:
      return
    a, b = self._pose(start), self._pose(end)
    if a is None or b is None or a == b:
      return  # unknown, or no part moves (a pump's stroke)
    world = self._world(self.pod_number, self._working_at)
    if world is None:
      return
    found = check.deck_collisions(self.declared, a, b, world)
    if found:
      parts = ", ".join(f"the {x} into {y}" for x, y in found[:3])
      raise check.DeckCollisionError(f"{self}: the move would bring {parts}")

  async def _minimize_span(self, speed: int = 1000) -> None:
    """Before a gripper approach on a Span-8, as Biomek's: the probes' span to its minimum (probe
    1's Y kept), a checked move; if it is not clear, the probes as high as they go first, then the
    span again. Where the probes' Y goes is left to the planned approach. Nothing on a 96-head pod,
    or with the span at its minimum.

    Raises:
      PodCollisionError: if the span cannot be narrowed clear even with the probes at the top.
    """
    if self.module_type != "span8":
      return
    d = self._require_declared()
    narrow = {"S": d.to_counts("S", d.limits["Span"][0])}
    ys = [f"Y{i}" for i in range(1, 9)]
    planned = self._planned(self._cpositions, narrow)
    if all(planned[y] == self._cpositions[y] for y in ys):
      return
    if not self._clear([narrow]):
      tops = self._max_traverse_heights()
      await self._rise({n: tops[n] for n in self.channel_z_axes}, speed)
      if not self._clear([narrow]):
        raise check.PodCollisionError(
          f"{self}: unable to narrow the probes' span for the gripper: it would collide"
        )
    await self.move_axes(narrow, speed)

  async def _raise_gripper(self, speed: int = 1000) -> None:
    """Raise the gripper to the height it travels at from here (`_traverse_heights`); nothing when
    it is already there or above."""
    d = self._require_declared()
    gz = d.to_counts("GZ", self._traverse_heights()["GZ"])
    if gz > self._cpositions["GZ"][1]:
      await self.move_axes({"GZ": gz}, speed)

  def _planned(
    self, start: Mapping[str, motion.CPos], targets: Mapping[str, int]
  ) -> Dict[str, motion.CPos]:
    """Where every axis ends from `start` as a move to `targets` would plan it (a Span-8's probes
    from its Y and span, as `move_axes` sets them)."""
    wanted = dict(targets)
    if self.module_type == "span8" and ("Y" in wanted or "S" in wanted):
      y, span = wanted.pop("Y", None), wanted.pop("S", None)
      wanted.update(motion.span_targets(start, y, span))
    _, _, after = motion.plan_move(self.axes, start, wanted, 1000, self._cutoff_velocity)
    return after

  def _world(self, pod_number: int, exempt: Sequence[str] = ()) -> Optional[obstacles.Obstacles]:
    """What pod `pod_number`'s moves must clear (the driver's `obstacles`, on that pod's
    calibration of the deck) with the boxes of the `exempt` positions left out; None without a
    device to give them."""
    if self.driver is None or self.driver.obstacles is None:
      return None
    return self.driver.obstacles(pod_number).without(exempt)

  def _planning_world(self) -> Optional[obstacles.Obstacles]:
    """What a planned move is checked against: the deck's obstacles (`_world`), nothing exempt.
    The other pod is the rail guard's (`XRail`)."""
    return self._world(self.pod_number)

  def _clear(
    self,
    moves: Sequence[merge.Move],
    start: Optional[Mapping[str, motion.CPos]] = None,
  ) -> bool:
    """Whether each of `moves` (axis targets, counts), made in order from `start` (where the pod
    is by default), passes the pod's own check and the deck check, as a sweep; planning exempts no
    deck position, as Biomek's does (`_planning_world`). A move
    whose start or end is not known is left to `move_axes`."""
    d = self._require_declared()
    world = self._planning_world()
    positions: Dict[str, motion.CPos] = dict(self._cpositions if start is None else start)
    for move in moves:
      try:
        after = self._planned(positions, move)
      except ValueError:
        return False
      a, b = self._pose(positions), self._pose(after)
      if a is None or b is None:
        return True
      if not check.move_clear(d, a, b, world):
        return False
      positions = after
    return True

  def _at_angle(self, counts: int) -> bool:
    """Whether GR is at the angle of `counts` (not a whole turn away: Biomek's GR is a line)."""
    d = self._require_declared()
    now = self._cpositions["GR"][0]
    if now is None:
      return False
    return abs(d.to_value("GR", now) - d.to_value("GR", counts)) < GR_SAME_ANGLE

  async def _travel_gripper(
    self,
    targets: Mapping[str, int],
    speed: int = 1000,
    minimum_traverse_height: Optional[float] = None,
  ) -> MoveResult:
    """Take the gripper to `targets` (counts: X, GY, GR) as Biomek's gripper approach does: on a
    Span-8 the probes' span narrowed first (`_minimize_span`), then the planned travel
    (`_move_over`): the channels and the gripper at their travel heights, the gripper turned to
    the angle given. Planned: no deck position exempt (`_planning`)."""
    await self._read_unknown_positions()
    with self._planning():
      await self._minimize_span(speed)
      return await self._move_over(targets, speed, minimum_traverse_height=minimum_traverse_height)

  # -- the shared X rail ---------------------------------------------------------

  def _rail_pose(
    self,
    x: int,
    gr: Optional[int],
    gg: Optional[int],
    held: Union[g.HeldLabware, None, Literal["checked"]] = "checked",
  ) -> RailPose:
    """A pose in counts as the rail takes it: deck X, and the gripper in degrees and cm holding
    `held`; by default ("checked") what it held at its last move checked on the rail
    (`_rail_held`): what the other pod is checked against changes only through a checked move of
    this one."""
    d = self._require_declared()
    gripper = None
    if gr is not None and gg is not None:
      what = self._rail_held if held == "checked" else held
      gripper = g.GripperPose(d.to_value("GR", gr), d.to_value("GG", gg), what)
    return RailPose(d.to_value("X", x), gripper)

  def _require_declared(self) -> PodConfiguration:
    if self.declared is None:
      raise RuntimeError(f"{self} has no declared configuration")
    return self.declared

  def add_position_listener(self, listener: Callable[["Pod"], None]) -> Callable[[], None]:
    """Call `listener(pod)` whenever the position record changes: after a move, a read, homing,
    or a position becoming unknown. Returns what removes it. For what follows the pod, such as its
    model in a resource tree (`BiomekI7`); a listener that raises is logged and kept."""
    self._listeners.append(listener)
    return lambda: self._listeners.remove(listener)

  def add_motion_listener(self, listener: Callable[["Pod", str, Any], None]) -> Callable[[], None]:
    """Call `listener(pod, event, detail)` as the pod moves: `"move"` with (targets, speed) for each
    `move_axes` (before it is checked), `"rise out of"` with the deck positions a planned
    operation leaves straight up, `"step aside"` with the X zone (cm) the other pod's move needs.
    Returns what removes it. A listener that raises is logged and kept."""
    self._motion_listeners.append(listener)
    return lambda: self._motion_listeners.remove(listener)

  def _tell(self, event: str, detail: Any) -> None:
    for listener in list(self._motion_listeners):
      try:
        listener(self, event, detail)
      except Exception:
        logger.exception("%s: motion listener %r failed", self, listener)

  def _positions_changed(self) -> None:
    self._tell_rail()
    for listener in list(self._listeners):
      try:
        listener(self)
      except Exception:
        logger.exception("%s: position listener %r failed", self, listener)

  def _tell_rail(self) -> None:
    """Give the rail this pod's pose from the position record (unknown parts as unknown)."""
    if self.rail is None or self.declared is None:
      return
    x = self._cpositions["X"][0]
    pose = None
    if x is not None:
      pose = self._rail_pose(x, self._cpositions["GR"][0], self._cpositions["GG"][0])
    self.rail.update(self.side, pose)

  async def request_rail_pose(self) -> RailPose:
    """Where the pod is along the rail, as its drives report X, GR and GG. Moves nothing."""
    axes = [self.axis(n) for n in ("X", "GR", "GG")]
    replies = await self.board.send_many([p.get_current_position(a.device, a.index) for a in axes])
    x, gr, gg = (p.parse_s32(r.payload) for r in replies)
    return self._rail_pose(x, gr, gg)

  async def move_axes(
    self,
    targets: Mapping[str, int],
    speed: int = 1000,
    forces: Optional[Mapping[str, float]] = None,
    sensors: Optional[Mapping[str, Tuple[int, int]]] = None,
  ) -> MoveResult:
    """Move several axes together so they arrive at once: Biomek's `AM` (with `SF` force limits and
    sensor stops). The slowest axis moves at its limits x
    speed / 1000, the others scaled to match; axes already at their target stay put.

    Targets are in counts. On the Span-8 Z channels they are adjusted positions (the channel's
    hysteresis is added to downward moves, as Biomek does). Syringe pumps move through the valve
    each was last set to (`pipettes.set_valves`), at the cutoff velocity last set (an aspirate's
    or dispense's `cutoff_velocity`), as the Master Controller carries both.

    A move of X, GR or GG, or the first move since the gripper took or let go of labware, is
    checked against the other pod first (`driver/x_rail.py`). When it would run into the other
    pod it is refused, and nothing is sent. One
    crossing only the path of the other pod's move in flight waits for that move to finish.
    Whether the pod may move now (interlock armed) is the caller's to decide.

    Args:
      targets: target per axis name. On a Span-8, also `Y` (channel 1) and `S` (the span from
        channel 1 to 8), as Biomek sends them: they set Y1-Y8 as the Master Controller does
        (`motion.span_targets`), and cannot be given with Y1-Y8.
      speed: 1..1000, Biomek's speed field.
      forces: force limit per axis, in the Master Controller's units (its `SF`; sent x1000). The
        axis stops where that force stalls it (a grip, a tip pressed on); its position is then
        not known until read, which the next move does.
      sensors: per axis, the (sensor, trigger) that stops it where the sensor fires (a Span-8 Z
        sensing liquid). Where it stopped is in the result and becomes the axis's position, as
        the Master Controller keeps it. Whether liquid not sensed or a clot is an
        error is the caller's to decide (the Master Controller fails the `AM` for the first and
        reports the second).

    Returns:
      The motions sent and how the axes ended.

    Raises:
      ValueError: for an unknown axis, a target outside an axis's limits, a force or sensor on an
        axis without a target, or a speed outside 1..1000; nothing is sent.
      RuntimeError: for a pump whose valve this driver has not set; nothing is sent.
      NotHomedError: if an axis has not been homed; its pending moves are dropped.
      RailCollisionError: if X, GR or GG would move into the other pod's range; nothing is sent.
      PodCollisionError: if the gripper (or what it holds) would meet the pod's own pipettor on
        the way (`planning.check`); nothing is sent.
    """
    self._tell("move", (dict(targets), speed))
    async with self._motion:  # one move at a time, whichever task sends it
      return await self._move(targets, speed, forces, sensors)

  async def _move(
    self,
    targets: Mapping[str, int],
    speed: int,
    forces: Optional[Mapping[str, float]],
    sensors: Optional[Mapping[str, Tuple[int, int]]],
  ) -> MoveResult:
    """`move_axes`, under the pod's move lock (`_motion`)."""
    forces, sensors = dict(forces or {}), dict(sensors or {})
    given, targets = dict(targets), dict(targets)
    if self.module_type == "span8" and ("Y" in targets or "S" in targets):
      channels = sorted(n for n in targets if n in {f"Y{i}" for i in range(1, 9)})
      if channels:
        raise ValueError(f"{self}: Y or S with {channels}; give one or the other")
      y, span = targets.pop("Y", None), targets.pop("S", None)
      targets.update(motion.span_targets(self._cpositions, y, span))
    if not 1 <= speed <= 1000:
      raise ValueError(f"speed {speed} is outside 1..1000")
    for name, target in targets.items():
      axis = self.axis(name)
      axis.check_position(target)
      if axis.is_pump and self._valves[name] is None:
        raise RuntimeError(
          f"{self}: {name}'s valve has not been set; call `pipettes.set_valves` first"
        )
    stray = sorted({*forces, *sensors} - set(targets))
    if stray:
      raise ValueError(f"{self}: a force or sensor on {stray}, which have no target")
    for name, force in forces.items():
      if self.axis(name).is_pump or not 0 <= force <= p.MAX_FORCE:
        raise ValueError(
          f"{self}: force {force} on {name}: not a motor, or outside 0..{p.MAX_FORCE}"
        )
    uses_pumps = any(self.axis(n).is_pump for n in targets)
    try:
      await self._read_unknown_positions(pumps=uses_pumps)
      motions, timeout_ms, after = motion.plan_move(
        self.axes, self._cpositions, targets, speed, self._cutoff_velocity
      )
      logger.info(
        "%s AM %s speed %d%s%s: %d axes move, Move All timeout %d ms",
        self.component,
        " ".join(f"{n}={t}" for n, t in given.items()),
        speed,
        "".join(f" SF {n} {f}" for n, f in forces.items()),
        "".join(f" sensor {n} {s} {t}" for n, (s, t) in sensors.items()),
        len(motions),
        timeout_ms,
      )
      if not motions:
        return MoveResult()
      self._check_own_collision(self._cpositions, after, {m.axis for m in motions})
      self._check_world_collision(self._cpositions, after)
      held = self._held_labware
      if self.rail is not None and (
        any(m.axis in RAIL_AXES for m in motions) or held != self._rail_held
      ):
        x0, gr0, gg0 = (self._cpositions[n][0] or 0 for n in RAIL_AXES)
        start = self._rail_pose(x0, gr0, gg0, held)
        x1, gr1, gg1 = (after[n][0] or 0 for n in RAIL_AXES)
        if "GG" in forces:  # stops on what it grips, short of its target: count the wider end
          gg1 = max(gg1, gg0)
        end = self._rail_pose(x1, gr1, gg1, held)
        async with self.rail.moving(self.side, start, end):
          completions = await self._send_motions(motions, timeout_ms, forces, sensors)
        self._rail_held = held
      else:
        completions = await self._send_motions(motions, timeout_ms, forces, sensors)
    except (RailCollisionError, check.PodCollisionError):
      raise  # refused before anything was sent: every position is as it was
    except BaseException as e:
      # As the Master Controller after a failed command, and its reply, so a
      # session's log reads as Biomek's: "#AM The left X motion failed: ...".
      self._forget_positions()
      if isinstance(e, AceError):
        names = {(a.device, a.index): f"{self.side} {a.name}" for a in self.axes}
        logger.error("%s #AM %s", self.component, english(e, names))
      raise
    before = self._cpositions
    self._cpositions = after
    # Force stopped these short of where they were sent: as the Master Controller does, their
    # positions become unknown (adjusted as before), read before the next move.
    if forces:
      self._forget_positions(forces, before)
    # A sensor stop reports where it stopped; the Master Controller takes that as the position.
    for name, complete in completions.items():
      if complete.position is not None and name not in forces:
        axis = self.axis(name)
        self._cpositions[name] = motion.update_cpos(
          self._cpositions[name], complete.position, axis.hysteresis
        )
    self._positions_changed()
    return MoveResult(list(motions), completions)

  # -- travel over the deck (design.md I12) -------------------------------------------

  @property
  def channel_z_axes(self) -> List[str]:
    """The axes whose height is the channels' bottom: `Z` on a Multichannel, `Z1`-`Z8` on a Span-8."""
    return [
      a.name for a in self.axes if a.name == "Z" or (a.name[0] == "Z" and a.name[1:].isdigit())
    ]

  def _traverse_heights(
    self,
    targets: Optional[Mapping[str, int]] = None,
    tip_lengths: Optional[Mapping[str, float]] = None,
  ) -> Dict[str, float]:
    """The height each Z-type axis (the channels' and the gripper's GZ) travels at by default on
    the way to `targets` (counts; None: where it stands), cm of its bottom above the deck: per
    tool, its `traverse_defaults` ("safe": `_safe_heights`, and as high as it goes without a deck
    to reckon from; "max": `_max_traverse_heights`; a height: its lowest point there), never above
    its `_max_traverse_heights`. `tip_lengths`: per channel, cm; the pod's record when None."""
    lengths = self._carried_tip_lengths() if tip_lengths is None else dict(tip_lengths)
    tops = self._max_traverse_heights()
    tools = {"channels": self.channel_z_axes, "gripper": [a for a in ("GZ",) if a in tops]}
    safe: Optional[Dict[str, float]] = None
    out: Dict[str, float] = {}
    for tool, axes in tools.items():
      mode = self.traverse_defaults[tool]
      if mode == "safe" and safe is None:
        safe = self._safe_heights(targets, lengths) or {}
      for axis in axes:
        if mode == "max":
          height = tops[axis]
        elif mode == "safe":
          height = (safe or {}).get(axis, tops[axis])
        else:
          height = units.to_cm(float(mode)) + (
            lengths.get(axis, 0.0) if axis != "GZ" else self._gripper_below()
          )
        out[axis] = min(height, tops[axis])
    return out

  def _gripper_below(self) -> float:
    """How far below GZ (cm) the gripper's lowest point is: its fingers, or what it holds."""
    pose = self._pose()
    if self.declared is None or pose is None or pose[1] is None:
      return 0.0
    return pod_model.gripper_below(self.declared, pose[0], pose[1])

  def _safe_heights(
    self,
    targets: Optional[Mapping[str, int]] = None,
    tip_lengths: Optional[Mapping[str, float]] = None,
  ) -> Optional[Dict[str, float]]:
    """Biomek's safe height for each Z-type axis on the way to `targets` (counts; None: where it
    stands), cm: the tallest deck item (`traverse.deck_height`) under each tool's footprint swept
    from here to there, floored by `GlobalMinSafeHeight` and the axis's lowest reach, plus the
    tool's own: a channel's tip (or the 96-head's `SubMandrelSafeHeight` without one) and
    `AdditionalSafeHeight`; the gripper's offset to its lowest point (what it holds included) and
    `AdditionalSafeHeightGripper`; `AvoidanceMargin` on every one. None without a deck to reckon
    from (no device, or an unknown position)."""
    d = self.declared
    own = self._world(self.pod_number)
    start = self._pose()
    if d is None or own is None or start is None:
      return None
    end = self._pose(self._planned(self._cpositions, targets or {}))
    if end is None:
      return None
    # What the way covers, and how tall it is, from the deck check's own boxes for this pod (its
    # own calibration of each position, the frame its parts are in), exempting none: a travel at
    # this height is one the check passes.
    lengths = self._carried_tip_lengths() if tip_lengths is None else tip_lengths
    return traverse.safe_heights(d, start, end, own, lengths, self.channel_z_axes)

  def _max_traverse_heights(self) -> Dict[str, float]:
    """The highest each Z-type axis travels, cm of its bottom above the deck: the top of travel less
    the park margin (`ParkLocation` `ZMargin`, `GZMargin`), as Biomek parks them (the
    Multichannel's rest Z, 39.763 cm, is -256 counts). A Span-8 probe goes no higher than its top
    less `DiscardOffset`: above that its stripper pushes a tip off (Biomek travels carrying probes
    there, as its logs show)."""
    return traverse.max_traverse_heights(self._require_declared(), self.channel_z_axes)

  async def park(self) -> None:
    """Biomek's park (`_park`): the channels and the gripper raised, the pod to its park X and Y,
    the gripper turned and closed to its park. A Span-8 parks over its wash station
    (`pipettes.park`)."""
    await self._park()

  async def _park(self, over: Optional[Mapping[str, int]] = None) -> None:
    """Biomek's park (`ParkLocation`): the channels and the gripper raised to their ceilings
    (`_max_traverse_heights`), the pod to the park X (`XMargin` in from its own end of the rail) and
    `Y`, the gripper turned and closed to its park (`GR`, `GY`, then `GG`). As logged after a
    prime (2026-10-09), to the count. `over`: X/Y targets (counts) for a pod whose park declares
    none; a Span-8 parks over its wash station (`pipettes.park`). Without either, it parks where
    it stands.

    Raises:
      RuntimeError: without a declared configuration.
    """
    d = self.declared
    if d is None:
      raise RuntimeError(f"{self} needs a declared configuration")
    if self._held_labware is not None:
      raise RuntimeError(f"{self}: the gripper holds labware; place it before parking")
    targets = destinations.park_targets(d, self.side, over)
    # Biomek's park raises the channels and the gripper as high as they go first, whatever the
    # travel defaults (`traverse_defaults`): it is where the pod rests, not a travel.
    await self._read_unknown_positions()
    await self._rise(self._max_traverse_heights(), 1000)
    await self._move_over(targets)
    rest = destinations.rest_heights(d, self.channel_z_axes)
    if any(self.cpositions[z][1] != c for z, c in rest.items()):
      await self.move_axes(rest)
    await self._close_gripper()

  async def _close_gripper(self) -> None:
    """Close the empty gripper to its park width (`GG`), under the grip force, as Biomek's park
    closes it (it may stop on its own fingers). Nothing without a park width."""
    d = self._require_declared()
    if "GG" not in d.park:
      return
    force = d.settings["GripperSqueezeForce"]
    await self.move_axes({"GG": d.to_counts("GG", d.park["GG"])}, forces={"GG": force})
    # The force leaves GG's position unknown, and the rail counts an unknown gripper as every
    # pose it could take, in the other pod's way: read it back now.
    await self._read_unknown_positions()

  async def move_aside(self) -> None:
    """Fold the gripper (drawn in to its park GY, then turned to its park GR with what it holds,
    and closed to its park width when empty), then take the pod to the end of its X travel on its
    own side (the left pod to its lowest X, the right pod to its highest), leaving the other pod
    the most of the rail. Biomek moves a pod out of the way itself; the rail guard here refuses
    instead (`x_rail.py`), so a caller makes room with this.

    Raises:
      RuntimeError: without a declared configuration.
    """
    d = self.declared
    if d is None:
      raise RuntimeError(f"{self} needs a declared configuration")
    # Drawn in first, so what it holds turns close to the pod.
    for axis in ("GY", "GR"):
      if d.gripper_model and axis in d.park:
        target = d.to_counts(axis, d.park[axis])
        if not (self._at_angle(target) if axis == "GR" else self._cpositions[axis][1] == target):
          await self._travel_gripper({axis: target})
    if self._held_labware is None and "GG" in d.park and d.gripper_model:
      if self._cpositions["GG"][1] > d.to_counts("GG", d.park["GG"]):
        await self._close_gripper()  # open fingers reach further: closed, as parked
    low, high = d.limits["X"]
    await self._move_over({"X": d.to_counts("X", low if self.side == "left" else high)})

  async def _move_over(
    self,
    targets: Mapping[str, int],
    speed: int = 1000,
    minimum_traverse_height: Optional[float] = None,
    z_final: Optional[float] = None,
    tip_lengths: Optional[Mapping[str, float]] = None,
    gripper_height: Optional[float] = None,
  ) -> MoveResult:
    """Travel to `targets` (X, Y and the like, in counts, as `move_axes` takes them) clear of the
    deck: lift the channels and the gripper to a traverse height, move, then optionally set the
    channels to `z_final`. STAR's `minimum_traverse_height_at_beginning_of_a_command` and
    `z_final`, and Biomek's Safe Height; design.md I12. Heights here are Biomek's frame, cm above
    the deck surface, and are each channel's lowest point, as STAR's are: the end of the tip it
    carries (`tip_lengths`), else its bottom. An axis already above its traverse height is not
    lowered.

    Args:
      targets: the travel's targets; channel Z axes in them are refused (use `z_final`).
      speed: as `move_axes`, for every step.
      minimum_traverse_height: the channels' lowest points while traveling. Default: their tool's
        `traverse_defaults` (`_traverse_heights`). The gripper travels at its own default.
      z_final: where to set the channels' bottoms once there; None leaves them at the traverse
        height.
      tip_lengths: per channel Z axis, how far below its bottom the tip it carries ends, cm. The
        pod's own record when None (`_carried_tip_lengths`).
      gripper_height: GZ (cm) to travel at, lowered to it first if it is above. Default: its
        traverse height (`_traverse_heights`).

    Raises:
      ValueError: for a channel Z axis in `targets`, or a height a channel cannot lift its tip to
        (under its `_max_traverse_heights`). Nothing is sent.
      DeckCollisionError: for a travel the deck check refuses (too low over something, or through
        a wall). Nothing is sent.
    """
    self._require_declared()
    stray = sorted(set(targets) & set(self.channel_z_axes))
    if stray:
      raise ValueError(f"{self}: {stray} in a travel's targets; use z_final")
    lengths = self._carried_tip_lengths() if tip_lengths is None else dict(tip_lengths)
    await self._read_unknown_positions()
    with self._planning():
      return await self._travel_over(
        targets, speed, minimum_traverse_height, z_final, lengths, gripper_height
      )  # fmt: skip

  async def _travel_over(
    self,
    targets: Mapping[str, int],
    speed: int,
    minimum_traverse_height: Optional[float],
    z_final: Optional[float],
    lengths: Mapping[str, float],
    gripper_height: Optional[float],
  ) -> MoveResult:
    """`_move_over`'s travel: every stage planned (the gripper lowered to `gripper_height`, the way
    out of any position the pod stands in, the rise, the travel (`_travel_moves`), the descent to
    `z_final`), each move checked as `move_axes` checks it, then executed; a stage that is not
    clear refuses the whole travel and nothing is sent. Returns the travel's last move's result."""
    d = self._require_declared()
    heights, _, _ = self._traverse_targets(minimum_traverse_height, lengths, targets)
    at: Dict[str, motion.CPos] = dict(self._cpositions)
    plan: List[PlanStep] = []
    if gripper_height is not None:
      heights["GZ"] = gripper_height
      lower = {"GZ": d.to_counts("GZ", gripper_height)}
      if lower["GZ"] < at["GZ"][1]:
        plan.append((lower, ()))
        at = self._planned(at, lower)
    escape, at = self._plan_escape(heights, at)
    plan += escape
    path = self._plan_lift(heights, at)
    for move in path:
      at = self._planned(at, move)
    travel = self._travel_moves(targets, at)
    for move in travel:
      at = self._planned(at, move)
    path += travel
    if z_final is not None:
      final = {n: d.to_counts(n, z_final) for n in self.channel_z_axes}
      path += self._direct(final, "the descent", at)
    # The travel's result: its last move that moves a target's axis.
    last_travel = max(
      (len(plan) + i for i, move in enumerate(path) if set(move) & set(targets)), default=-1
    )
    plan += [(move, ()) for move in path]
    result = MoveResult()
    for i, step in enumerate(plan):
      done = await self._run_step(step, speed)
      if i == last_travel:
        result = done
    return result

  async def move_over(
    self,
    targets: Mapping[str, float],
    speed_fraction: float = 1.0,
    minimum_traverse_height: Optional[float] = None,
    z_final: Optional[float] = None,
    tip_lengths: Optional[Mapping[str, float]] = None,
    gripper_height: Optional[float] = None,
  ) -> MoveResult:
    """Travel to `targets` clear of the deck: lift the channels and the gripper to a traverse
    height, move, then optionally set the channels to `z_final` (STAR's
    `minimum_traverse_height_at_beginning_of_a_command` and `z_final`; Biomek's Safe Height).
    Heights are mm above the deck of each channel's lowest point, as STAR's are: the end of the
    tip it carries, else its bottom. An axis already above its traverse height is not lowered.

    Args:
      targets: per axis, in PyLabRobot's units (`units.axis_to_biomek`): X and Y as deck x / y
        (mm) of the point the pod's X and Y name (mandrel A1, probe 1), S (the Span-8's span) and
        GG in mm, GR in degrees, pumps in uL. Channel Z axes are refused (use `z_final`).
      speed_fraction: the share of each axis's maximum velocity every step moves at, (0, 1]: one
        for the whole move, as the instrument has.
      minimum_traverse_height: the channels' lowest points while traveling, mm. Default: each at
        `traverse_heights`.
      z_final: where to set the channels' bottoms once there, mm; None leaves them at the traverse
        height.
      tip_lengths: per channel Z axis, how far below its bottom the tip it carries ends, mm. The
        pod's own record when None (`carried_tip_lengths`).
      gripper_height: GZ, mm, to travel at, lowered to it first if it is above. Default: its
        traverse height (`traverse_heights`).

    Raises:
      ValueError: as `_move_over`, its Biomek-unit counterpart; for a `speed_fraction` outside
        (0, 1]. Nothing is sent.
    """
    return await self._move_over(
      {axis: units.axis_to_counts(self, axis, value) for axis, value in targets.items()},
      units.fraction_to_field(speed_fraction),
      minimum_traverse_height=units.optional_cm(minimum_traverse_height),
      z_final=units.optional_cm(z_final),
      tip_lengths=(
        None if tip_lengths is None else {a: units.to_cm(v) for a, v in tip_lengths.items()}
      ),
      gripper_height=units.optional_cm(gripper_height),
    )

  def carried_tip_lengths(self) -> Dict[str, float]:
    """Per channel Z axis carrying a tip, how far below the channel the tip ends, mm.

    Raises:
      RuntimeError: for a carried tip whose length is not known.
    """
    return {axis: units.to_mm(v) for axis, v in self._carried_tip_lengths().items()}

  def traverse_heights(self) -> Dict[str, float]:
    """The height each Z-type axis (the channels' and the gripper's GZ) travels at by default
    where it stands, mm of its bottom above the deck: per tool, its `traverse_defaults`
    (`_traverse_heights`)."""
    return {axis: units.to_mm(v) for axis, v in self._traverse_heights().items()}

  def max_traverse_heights(self) -> Dict[str, float]:
    """As high as each Z-type axis travels, mm of its bottom above the deck
    (`_max_traverse_heights`)."""
    return {axis: units.to_mm(v) for axis, v in self._max_traverse_heights().items()}

  def _carried_tip_lengths(self) -> Dict[str, float]:
    """Per channel Z axis carrying a tip, how far below the channel the tip ends, cm: from the
    head's or the probes' tip record.

    Raises:
      RuntimeError: for a carried tip whose length is not known.
    """
    feature = self.head96 if self.head96 is not None else self.pipettes
    return {} if feature is None else feature._carried_tip_lengths()

  def _traverse_targets(
    self,
    minimum_traverse_height: Optional[float],
    tip_lengths: Mapping[str, float],
    targets: Optional[Mapping[str, int]] = None,
  ) -> Tuple[Dict[str, float], float, float]:
    """Where each Z-type axis's bottom goes to travel, cm; the highest every channel's lowest point
    reaches; and how high the lowest one travels. By default each at `_traverse_heights`
    (defined on the bottoms: the tips do not change where they go); given a height, the channels'
    lowest points there (a channel's bottom at the height + its tip's length, `tip_lengths`).

    Raises:
      ValueError: for a height a channel cannot lift its tip to.
    """
    channels = self.channel_z_axes
    lengths = {n: tip_lengths.get(n, 0.0) for n in channels}
    heights = self._traverse_heights(targets, lengths)
    ceilings = self._max_traverse_heights()
    reach = min(ceilings[n] - lengths[n] for n in channels)
    if minimum_traverse_height is None:
      return heights, reach, min(heights[n] - lengths[n] for n in channels)
    short = {
      n: round(ceilings[n] - lengths[n], 4)
      for n in channels
      if minimum_traverse_height + lengths[n] > ceilings[n] + 1e-9
    }
    if short:
      raise ValueError(
        f"{self}: traverse height {minimum_traverse_height:.3f} cm is above what the channels"
        f" reach with their tips: {short}"
      )
    heights.update({n: minimum_traverse_height + lengths[n] for n in channels})
    return heights, reach, minimum_traverse_height

  async def _lift_channels(
    self,
    minimum_traverse_height: Optional[float] = None,
    tip_lengths: Optional[Mapping[str, float]] = None,
    speed: int = 1000,
  ) -> None:
    """Raise the channels (not the gripper) where they stand to a traverse height, as `_move_over`
    does before traveling (the same meaning of heights, the same tips); never lower.

    Raises:
      ValueError: for a height a channel cannot lift its tip to. Nothing is sent.
    """
    lengths = self._carried_tip_lengths() if tip_lengths is None else tip_lengths
    heights, _, _ = self._traverse_targets(minimum_traverse_height, lengths)
    await self._read_unknown_positions()
    await self._raise_to({n: heights[n] for n in self.channel_z_axes}, speed)

  async def _raise_to(self, heights: Mapping[str, float], speed: int) -> None:
    """Move the axes below their `heights` (cm) up to them, together: a direct, checked move (an
    operation's own rise, out of the positions it works in)."""
    lift = self._lift_targets(heights)
    if lift:
      await self.move_axes(lift, speed)

  def _lift_targets(
    self, heights: Mapping[str, float], at: Optional[Mapping[str, motion.CPos]] = None
  ) -> Dict[str, int]:
    """The axes below their `heights` (cm) in `at` (the position record by default), with those
    heights in counts."""
    d = self._require_declared()
    positions = self._cpositions if at is None else at
    lift = {}
    for name, height in heights.items():
      counts = d.to_counts(name, height)
      if positions[name][1] < counts:  # counts grow with height on every Z-type axis
        lift[name] = counts
    return lift

  @contextlib.contextmanager
  def _planning(self) -> Iterator[None]:
    """Within it, a planned operation (a travel, an approach, a move to safe heights, park): no
    deck position is exempt from the deck check, as Biomek's planner exempts none, whatever
    operation it is part of."""
    before = self._working_at
    self._working_at = ()
    try:
      yield
    finally:
      self._working_at = before

  async def _rise(self, heights: Mapping[str, float], speed: int) -> None:
    """The planned move to safe heights: the way out of any position the pod stands in
    (`_plan_escape`), then the axes below their `heights` (cm) up to them, planned whole then
    executed, nothing exempt (`_planning`).

    Raises:
      DeckCollisionError: as `_plan_escape` and `_plan_lift`. Nothing is sent.
    """
    with self._planning():
      plan, at = self._plan_escape(heights, self._cpositions)
      plan += [(move, ()) for move in self._plan_lift(heights, at)]
      for step in plan:
        await self._run_step(step, speed)

  def _plan_escape(
    self, heights: Mapping[str, float], start: Mapping[str, motion.CPos]
  ) -> Tuple[List[PlanStep], Dict[str, motion.CPos]]:
    """The way out of the deck positions a pod at `start` stands in (left low in one by the
    operation before), as Biomek's planner leaves them when its start is in collision; and where
    it leaves the pod. Each part inside them (a channel, a Span-8's probes one by one, or the
    gripper) straight up until its bottom is at their tops, or to its height in `heights` (cm) if
    that is higher, the others staying; one step, checked against everything but those positions
    (the step names them), its end clear of everything. None for a pod inside nothing.

    Raises:
      DeckCollisionError: if the pod stands inside something other than a deck position's box
        (a trash's walls, the instrument), or cannot rise out within its axes' travel.
    """
    d = self._require_declared()
    at: Dict[str, motion.CPos] = dict(start)
    plan: List[PlanStep] = []
    pose = self._pose(at)
    world = self._planning_world()
    if pose is not None and world is not None:
      # The planner's model: the gripper padded below (`pod_model.planning_pad`).
      pad = pod_model.planning_pad(d)
      out = check.escape(d, pose, world, pad)
      if out is None:
        found = check.deck_collisions_at(d, pose, world, pad)
        parts = ", ".join(f"the {x} in {y}" for x, y in found[:3])
        raise check.DeckCollisionError(f"{self}: no way out planned for {parts}")
      if out.holders:
        targets = find_path.escape_targets(d, pose, out, heights, self.channel_z_axes)
        beyond = [n for n, t in targets.items() if t > self.axis(n).limits.max_position]
        if beyond:
          raise check.DeckCollisionError(f"{self}: {beyond} cannot rise out of {list(out.holders)}")
        after = self._planned(at, targets)
        end = self._pose(after)
        assert end is not None
        if check.deck_collisions_at(d, end, world, pad):
          raise check.DeckCollisionError(f"{self}: rising out of {list(out.holders)} is not clear")
        plan.append((targets, out.holders))
        at = after
    return plan, at

  def _plan_lift(
    self, heights: Mapping[str, float], start: Mapping[str, motion.CPos]
  ) -> List[merge.Move]:
    """The rise from `start`: the axes below their `heights` (cm) up to them, straight up.

    Raises:
      DeckCollisionError: when that is not clear; nothing is sent.
    """
    lift = self._lift_targets(heights, start)
    if not lift:
      return []
    return self._direct(lift, "the rise", start)

  async def _run_step(self, step: PlanStep, speed: int) -> MoveResult:
    """One planned move; one that leaves the positions it names (the way out of them) is made with
    those positions exempt."""
    move, leaving = step
    if not leaving:
      return await self.move_axes(move, speed)
    self._tell("rise out of", leaving)
    logger.info("%s leaves %s straight up", self.component, ", ".join(leaving))
    with self.working_at(*leaving):
      return await self.move_axes(move, speed)

  def _travel_moves(
    self, targets: Mapping[str, int], start: Mapping[str, motion.CPos]
  ) -> List[merge.Move]:
    """The travel to `targets` from `start`, the rise made: one move when it is clear; else
    Biomek's direct candidate, one axis at a time (`merge.direct_candidate`), in the first order of
    its moves that is clear (a gripper turned before it reaches out, say).

    Raises:
      DeckCollisionError: when no order is clear; nothing is sent.
    """
    if self._clear([dict(targets)], start):
      return [dict(targets)]
    for order in itertools.permutations(merge.direct_candidate(targets)):
      if self._clear(order, start):
        return list(order)
    raise check.DeckCollisionError(f"{self}: no order of the travel's moves is clear")

  def _direct(
    self, move: merge.Move, what: str, start: Mapping[str, motion.CPos]
  ) -> List[merge.Move]:
    """`move` from `start`, when it is clear (`_clear`).

    Raises:
      DeckCollisionError: when it is not; nothing is sent.
    """
    if not self._clear([move], start):
      raise check.DeckCollisionError(f"{self}: {what} is not clear")
    return [move]

  async def _send_motions(
    self,
    motions: Sequence[motion.Motion],
    timeout_ms: int,
    forces: Mapping[str, float],
    sensors: Mapping[str, Tuple[int, int]],
  ) -> Dict[str, p.MotionComplete]:
    """Set each motion's parameters, then start them together and wait for them to finish.
    Returns, per axis, its report when it was not a plain finish.

    The parameters go out in the order the Master Controller's requests reach the board
    (`motion.spawn_order`), motors and pumps together, so a session's traffic lines up with
    Biomek's request for request."""
    moves = []
    for m in motion.spawn_order(motions):
      sensor, trigger = sensors.get(m.axis, (0, 0))
      moves.append(
        AxisMove(
          m.axis,
          m.position,
          m.velocity,
          m.acceleration,
          m.jerk or 0,
          round(forces.get(m.axis, 0.0) * 1000),
          sensor,
          trigger,
        )
      )
    accepted = await self._unchecked_fw_set_move_parameters(moves)
    if not all(accepted):
      await self._unchecked_fw_stop_motion()
      refused = [m.axis for m, ok in zip(moves, accepted) if not ok]
      raise NotHomedError(0x32, None, f"{self}: {refused} not homed")
    reports = await self._unchecked_fw_move_all(timeout_ms)
    names = {(a.device, a.index): a.name for a in self.axes}
    return {
      names[(c.device, c.index)]: c
      for c in reports
      if (c.device, c.index) in names and c.outcome != "done"
    }

  # -- drive commands ----------------------------------------------------------
  # The only way this driver commands a drive: one request, or one batch sent together, each. They
  # check targets against the axis's limits and nothing else. Whether an axis may move now (homed,
  # interlock armed, clear of the other pod) is the caller's to decide, as homing does by asking
  # the board.

  async def _unchecked_fw_enable(self, axes: Sequence[str]) -> None:
    """Set State ENABLED on each axis, sent together. On a drive in fault this is also the fault
    reset; one that cannot come up answers an error and stays in fault."""
    if axes:
      targets = [self.axis(name) for name in axes]
      await self.board.send_many(
        [p.set_state_enabled(a.device, a.index) for a in targets],
        timeout=5.0 + ENABLE_SECONDS_PER_AXIS * len(targets),
      )

  async def _unchecked_fw_set_move_parameters(self, moves: Sequence[AxisMove]) -> List[bool]:
    """Set each axis's next move, sent together in the order given. Returns, per move, whether the
    board took it (True) or refused it because the axis has not been homed since power-up (False).

    A syringe pump's move is sent as the Span-8's `move` does: the pump's minimum start velocity,
    the move's velocity and acceleration, the cutoff velocity clamped to the pump's range, and the
    valve as last set; its jerk, force and sensor are not sent.

    Raises:
      ValueError: for a target outside the axis's limits; nothing is sent.
      RuntimeError: for a pump whose valve has not been set; nothing is sent.
      AceError: for any refusal other than "not homed".
    """
    requests = []
    for move in moves:
      axis = self.axis(move.axis)
      axis.check_position(move.position)
      limits = axis.limits
      velocity = limits.max_velocity if move.velocity is None else move.velocity
      acceleration = limits.max_acceleration if move.acceleration is None else move.acceleration
      if axis.is_pump:
        valve = self._valves[move.axis]
        if valve is None:
          raise RuntimeError(f"{self}: {move.axis}'s valve has not been set")
        pump = axis.pump_limits
        cutoff = int(
          motion.range_(pump.min_cutoff_velocity, self._cutoff_velocity, pump.max_cutoff_velocity)
        )
        requests.append(
          p.set_pump_move_parameters(
            axis.index,
            move.position,
            pump.min_start_velocity,
            velocity,
            cutoff,
            acceleration,
            valve,
          )
        )
        continue
      requests.append(
        p.set_motor_move_parameters(
          axis.index,
          move.position,
          velocity,
          acceleration,
          limits.max_jerk if move.jerk is None else move.jerk,
          move.force,
          move.sensor,
          move.trigger,
        )
      )
    replies = await self.board.send_many(requests, raise_errors=False)
    accepted = []
    for move, reply in zip(moves, replies):
      try:
        reply.raise_for_error(f"{self}: Set {move.axis} Move Parameters")
        accepted.append(True)
      except NotHomedError:
        accepted.append(False)
    return accepted

  async def _unchecked_fw_move(self, axis: str, timeout_ms: int = MOVE_TIMEOUT_MS) -> None:
    """Start one axis's move to the parameters set for it, and wait for it to finish."""
    await self.board.send(
      p.move(self.axis(axis).index, timeout_ms), timeout=timeout_ms / 1000 + 5.0
    )

  async def _unchecked_fw_move_all(
    self, timeout_ms: int = MOVE_ALL_TIMEOUT_MS
  ) -> List[p.MotionComplete]:
    """Start every axis whose move parameters were set since its last move, together, and wait for
    all of them to finish. Returns the reports the axes sent under the Move All."""
    reply = await self.board.send(p.move_all(timeout_ms), timeout=timeout_ms / 1000 + 5.0)
    return [p.parse_motion_complete(r) for r in reply.related]

  async def _unchecked_fw_home(self, axes: Sequence[str]) -> None:
    """Home the axes, sent together, and wait for every one to finish."""
    targets = [self.axis(name) for name in axes]
    await self.board.send_many(
      [p.home(a.device, a.index) for a in targets], timeout=HOME_REPLY_TIMEOUT
    )

  async def _unchecked_fw_stop_motion(self) -> None:
    """Stop Motion Service on this pod's board: stops every axis and drops pending moves."""
    await self.board.send(p.stop_motion_service())

  async def _unchecked_fw_configure(self, axis: Axis) -> None:
    """Load one axis's motor table (Configure). Leaves it disabled and not homed."""
    await self.board.send(p.configure(axis.device, axis.index, axis.configure))

  async def _unchecked_fw_stop_module_execution(self) -> None:
    await self.board.send(p.stop_module_execution())

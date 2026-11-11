"""One pod: an arm on the shared X rail, its ACE module, and the axes that module drives."""

import logging
import math
from dataclasses import dataclass, field
from typing import (
  Callable,
  Dict,
  Iterable,
  List,
  Literal,
  Mapping,
  Optional,
  Sequence,
  Set,
  Tuple,
  cast,
)

from pylabrobot.beckman_coulter.biomek_i7 import units
from pylabrobot.beckman_coulter.biomek_i7.driver import motion
from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  ModuleConfiguration,
  PodConfiguration,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.module import (
  read_analog_inputs,
  read_module,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.homing import (
  ENABLE_ORDER,
  ENABLE_PREFIX,
  HOME_RECIPES,
  Homer,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import AceError, NotHomedError, english
from pylabrobot.beckman_coulter.biomek_i7.driver.motors import Axis, AxisMove, MotorTables
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_collision as pc
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.x_rail import PodPose, RailCollisionError, XRail

logger = logging.getLogger(__name__)

# How Biomek numbers its pods. Pod1 is the left pod and Pod2 the right on the instrument this
# driver was written against; whether that holds on every build is not established.
POD_NUMBERS = {"left": 1, "right": 2}
COMPONENT_PREFIXES = {"multichannel": "MC", "span8": "SPAN8"}
# The axes that change what a pod takes up along the X rail.
RAIL_AXES = ("X", "GR", "GG")
# The gripper's axes; a move of these, or of the channels' Y or Z, is checked against the pod's own
# parts (`pod_collision`).
OWN_COLLISION_AXES = {"GY", "GZ", "GR", "GG"}
GRIPPER_HEIGHT_STEP = 0.1  # cm: how finely the gripper's travel height is searched

# Get Homed State answers. 0x01 was read from every axis at a cold start, 0x00 from every axis of
# both pods after Biomek's Home All (H1, 2026-10-24).
HOMED_STATES = {0x00: "homed", 0x01: "not_homed"}

# The timeouts the Master Controller gives the boards: a single axis's move, and a Move All.
MOVE_TIMEOUT_MS = 30000
MOVE_ALL_TIMEOUT_MS = 5000

# Biomek's "Safe Height" (`GlobalMinSafeHeight`) at or below this is unset (it stores -100000).
UNSET_SAFE_HEIGHT = -1000.0

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
  on the shared X rail. `module_type` is what the board reports itself to be: `multichannel` (a
  96-channel head) or `span8` (eight independent channels).
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
    # The shared X rail, set by the driver once both pods are found. Every move that changes what
    # the pod takes up along it (X, the gripper's GR or GG) goes through it.
    self.rail: Optional[XRail] = None
    # What the gripper holds, as the rail counts it; the pod cannot sense it.
    self._held_labware: Optional[g.HeldLabware] = None
    # Where this driver last put each axis, as the Master Controller keeps it (`cpositions`):
    # (position, adjusted); position None until read. Starts as after homing.
    self._cpositions: Dict[str, motion.CPos] = {a.name: (None, a.home_position) for a in self.axes}
    # The pumps' state, kept with the positions as the Master Controller keeps it in the Span-8's
    # state record, because every pump move carries it. Each syringe
    # pump's valve as last set (None until then), and the cutoff velocity (`CV`), counts/s,
    # clamped to each pump's range when used.
    self._valves: Dict[str, Optional[p.Valve]] = {a.name: None for a in self.axes if a.is_pump}
    self._cutoff_velocity = 0
    # The height the channels rise to before traveling over the deck, mm of the channels' bottom
    # above the deck: STAR's `default_minimum_traverse_height`. Seeded from Biomek's "Safe Height"
    # (`GlobalMinSafeHeight`) when it is set; None, as on an instrument where it is not (-100000),
    # means the top of travel less the park margin, which is what Biomek does with Safe Height set
    # to the top.
    self.default_minimum_traverse_height: Optional[float] = None
    if declared is not None:
      safe = declared.settings.get("GlobalMinSafeHeight")
      if isinstance(safe, (int, float)) and safe > UNSET_SAFE_HEIGHT:
        self.default_minimum_traverse_height = units.to_mm(float(safe))
    # Who follows the position record (`add_position_listener`).
    self._listeners: List[Callable[["Pod"], None]] = []

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

  def _own_state(
    self, positions: Optional[Mapping[str, motion.CPos]] = None
  ) -> Optional[Tuple[float, pc.GripperState, pc.PipettorState]]:
    """The pose the pod's own collision check takes (`pod_collision`), in Biomek's units, from
    `positions` (the position record by default); None while any of its axes is unknown."""
    d = self.declared
    if d is None or not d.gripper_model:
      return None
    positions = self._cpositions if positions is None else positions
    channels = self.channel_z_axes
    ys = [f"Y{i}" for i in range(1, 9)] if self.module_type == "span8" else ["Y"]
    names = ["X", "GY", "GZ", "GR", "GG", *ys, *channels]
    counts = {n: positions[n][0] for n in names}
    if any(c is None for c in counts.values()):
      return None
    v = {n: d.to_value("Y" if n in ys else n, c) for n, c in counts.items()}  # type: ignore[arg-type]
    if self.module_type == "span8":
      # Each probe's Y counts from its own narrowest place (`probe_y`), not probe 1's.
      v.update({n: d.probe_y(i, counts[n]) for i, n in enumerate(ys, 1)})  # type: ignore[arg-type]
    lengths: Dict[str, float] = {}  # no channel carries a tip
    gripper = pc.GripperState(v["GY"], v["GZ"], v["GR"], v["GG"], self._held_labware)
    pipettor = pc.PipettorState(
      tuple(v[n] for n in ys),
      tuple(v[z] for z in channels),
      tuple(lengths.get(z, 0.0) for z in channels),
    )
    return v["X"], gripper, pipettor

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
    a, b = self._own_state(start), self._own_state(end)
    if a is None or b is None or self.declared is None:
      return
    found = pc.swept_collisions(self.declared, (a[0], b[0]), (a[1], b[1]), (a[2], b[2]))
    if found:
      parts = ", ".join(f"the {x} and {y}" for x, y in found[:3])
      raise pc.PodCollisionError(f"{self}: the move would bring together {parts}")

  def _gripper_traverse_height(self, gr: float) -> float:
    """The GZ (cm) the gripper travels at to turn from where it is to GR `gr` (degrees): its
    ceiling (`_traverse_ceilings`), or as far below it as keeps it (and what it holds) clear of the
    pod's own pipettor at its traverse heights on the way, in steps of `GRIPPER_HEIGHT_STEP`.

    Raises:
      PodCollisionError: if no height down to the grip's travel limit is clear.
    """
    d = self._require_declared()
    state = self._own_state()
    ceiling = self._traverse_ceilings()["GZ"]
    if state is None:
      return ceiling
    x, gripper, pipettor = state
    heights = self._traverse_heights()
    raised = pc.PipettorState(
      pipettor.ys,
      tuple(max(z, heights[n]) for z, n in zip(pipettor.zs, self.channel_z_axes)),
      pipettor.tips,
    )
    low = d.limits["GZ"][0]
    gz = ceiling
    while gz >= low:
      a = pc.GripperState(gripper.gy, gz, gripper.gr, gripper.gg, gripper.held)
      b = pc.GripperState(gripper.gy, gz, gr, gripper.gg, gripper.held)
      if not pc.swept_collisions(d, (x, x), (a, b), (raised, raised)):
        return gz
      gz -= GRIPPER_HEIGHT_STEP
    raise pc.PodCollisionError(f"{self}: no gripper height clears the pod's own parts at GR {gr}")

  async def _make_room_for_gripper(self, targets: Mapping[str, int]) -> None:
    """Before the gripper travels to `targets` (counts: X, GY, GR): on a Span-8 whose probes
    would be in its way there, narrow them to the smallest span and move them to the end of their
    Y travel away from the gripper (as Biomek's path search does when it minimises the pod's Y
    span). Nothing on a 96-head pod, or with the probes already clear.

    Raises:
      PodCollisionError: if the probes cannot be moved clear.
    """
    if self.module_type != "span8":
      return
    d = self._require_declared()
    await self._read_unknown_positions()
    state = self._own_state()
    if state is None:
      return
    x, gripper, pipettor = state
    gy = d.to_value("GY", targets.get("GY", self._cpositions["GY"][1]))
    gr = d.to_value("GR", targets.get("GR", self._cpositions["GR"][1]))
    gz = self._gripper_traverse_height(gr) if gr != gripper.gr else gripper.gz
    end = pc.GripperState(gy, gz, gr, gripper.gg, gripper.held)
    if not pc.collisions(d, x, end, pipettor):
      return
    low, high = d.limits["Y"]
    span = d.limits["Span"][0]
    y = low if gy >= (low + high) / 2 else high - span
    ys = tuple(y + i * span / (len(pipettor.ys) - 1) for i in range(len(pipettor.ys)))
    if pc.collisions(d, x, end, pc.PipettorState(ys, pipettor.zs, pipettor.tips)):
      raise pc.PodCollisionError(f"{self}: the probes cannot be moved clear of the gripper")
    await self._lift_channels()
    await self.move_axes({"Y": d.to_counts("Y", y), "S": d.to_counts("S", span)})

  async def _travel_gripper(
    self,
    targets: Mapping[str, int],
    speed: int = 1000,
    minimum_traverse_height: Optional[float] = None,
  ) -> MoveResult:
    """Take the gripper to `targets` (counts: X, GY, GR) clear of the deck and of the pod's own
    pipettor: the channels raised (`_move_over`), the probes moved out of its way
    (`_make_room_for_gripper`), the gripper at the highest height that clears the pod's own parts
    for the turn (`_gripper_traverse_height`), lowered to it first if it is above."""
    d = self._require_declared()
    await self._read_unknown_positions()
    await self._make_room_for_gripper(targets)
    gr = d.to_value("GR", targets.get("GR", self._cpositions["GR"][1]))
    gz = self._gripper_traverse_height(gr)
    if d.to_counts("GZ", gz) < self._cpositions["GZ"][1]:
      await self.move_axes({"GZ": d.to_counts("GZ", gz)}, speed)
    return await self._move_over(
      targets, speed, minimum_traverse_height=minimum_traverse_height, gripper_height=gz
    )

  # -- the shared X rail ---------------------------------------------------------

  def _rail_pose(self, x: int, gr: Optional[int], gg: Optional[int]) -> PodPose:
    """A pose in counts as the rail takes it: deck X, and the gripper in degrees and cm."""
    d = self._require_declared()
    gripper = None
    if gr is not None and gg is not None:
      gripper = g.GripperPose(d.to_value("GR", gr), d.to_value("GG", gg), self._held_labware)
    return PodPose(d.to_value("X", x), gripper)

  def _require_declared(self) -> PodConfiguration:
    if self.declared is None:
      raise RuntimeError(f"{self} has no declared configuration")
    return self.declared

  def add_position_listener(self, listener: Callable[["Pod"], None]) -> Callable[[], None]:
    """Call `listener(pod)` whenever the position record changes: after a move, a read, homing,
    or a position becoming unknown. Returns what removes it. For what follows the pod, such as its
    model in a resource tree; a listener that raises is logged and kept."""
    self._listeners.append(listener)
    return lambda: self._listeners.remove(listener)

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

  async def request_rail_pose(self) -> PodPose:
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
    each was last set to, at the cutoff velocity last set, as the Master Controller carries both.

    A move of X, GR or GG is checked against the other pod first (`driver/x_rail.py`). One that
    would run into it is refused, and nothing is sent. One crossing only the path of the other
    pod's move in flight waits for that move to finish. Whether the pod may move now (interlock
    armed) is the caller's to decide.

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
        the way (`pod_collision`); nothing is sent.
    """
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
        raise RuntimeError(f"{self}: {name}'s valve has not been set")
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
      if self.rail is not None and any(m.axis in RAIL_AXES for m in motions):
        start = self._rail_pose(*(self._cpositions[n][0] or 0 for n in RAIL_AXES))
        end_counts = [after[n][0] or 0 for n in RAIL_AXES]
        if "GG" in forces:  # stops on what it grips, short of its target: count the wider end
          end_counts[2] = max(end_counts[2], self._cpositions["GG"][0] or 0)
        end = self._rail_pose(*end_counts)
        async with self.rail.moving(self.side, start, end):
          completions = await self._send_motions(motions, timeout_ms, forces, sensors)
      else:
        completions = await self._send_motions(motions, timeout_ms, forces, sensors)
    except (RailCollisionError, pc.PodCollisionError):
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

  def _traverse_heights(self) -> Dict[str, float]:
    """The height each Z-type axis (the channels' and the gripper's GZ) travels at by default, cm
    of its bottom above the deck: its ceiling (`_traverse_ceilings`), or
    `default_minimum_traverse_height` where that is lower."""
    ceilings = self._traverse_ceilings()
    wanted = units.optional_cm(self.default_minimum_traverse_height)
    return {
      name: top if wanted is None or name == "GZ" else min(wanted, top)
      for name, top in ceilings.items()
    }

  def _traverse_ceilings(self) -> Dict[str, float]:
    """The highest each Z-type axis travels, cm of its bottom above the deck: the top of travel less
    the park margin (`ParkLocation` `ZMargin`, `GZMargin`), as Biomek parks them (the
    Multichannel's rest Z, 39.763 cm, is -256 counts). A Span-8 probe goes no higher than its top
    less `DiscardOffset`: above that its stripper pushes a tip off (Biomek travels carrying probes
    there, as its logs show)."""
    d = self._require_declared()
    shuck = d.settings.get("DiscardOffset") if self.module_type == "span8" else None
    ceilings = {}
    for name in self.channel_z_axes + ["GZ"]:
      top = d.limits[name][1]
      ceiling = top + d.park.get("GZMargin" if name == "GZ" else "ZMargin", 0.0)
      if shuck is not None and name != "GZ":
        ceiling = min(ceiling, top - shuck)
      ceilings[name] = ceiling
    return ceilings

  async def park(self) -> None:
    """Biomek's park (`_park`): the channels and the gripper raised, the pod to its park X and Y,
    the gripper turned and closed to its park."""
    await self._park()

  async def _park(self, over: Optional[Mapping[str, int]] = None) -> None:
    """Biomek's park (`ParkLocation`): the channels and the gripper raised to their ceilings
    (`_traverse_ceilings`), the pod to the park X (`XMargin` in from its own end of the rail) and
    `Y`, the gripper turned and closed to its park (`GR`, `GY`, then `GG`). As logged after a
    prime (2026-10-09), to the count. `over`: X/Y targets (counts) for a pod whose park declares
    none. Without either, it parks where it stands.

    Raises:
      RuntimeError: without a declared configuration.
    """
    d = self.declared
    if d is None:
      raise RuntimeError(f"{self} needs a declared configuration")
    if self._held_labware is not None:
      raise RuntimeError(f"{self}: the gripper holds labware; place it before parking")
    p = d.park
    targets: Dict[str, int] = dict(over or {})
    if "XMargin" in p and "X" not in targets:
      low, high = d.limits["X"]
      x = low + p["XMargin"] if self.side == "left" else high - p["XMargin"]
      targets["X"] = d.to_counts("X", x)
    if "Y" in p and "Y" not in targets:
      targets["Y"] = d.to_counts("Y", p["Y"])
    targets.update({a: d.to_counts(a, p[a]) for a in ("GR", "GY") if a in p})
    await self._move_over(targets)
    # The channels rest at the top less the park margin, and on a Span-8 less `DiscardOffset` as
    # well (39.1079 cm on Z1, 9029 counts: lower than the probes travel, `_traverse_ceilings`).
    shuck = d.settings.get("DiscardOffset", 0.0) if self.module_type == "span8" else 0.0
    rest = {
      z: d.to_counts(z, d.limits[z][1] + p.get("ZMargin", 0.0) - shuck) for z in self.channel_z_axes
    }
    if any(self.cpositions[z][1] != c for z, c in rest.items()):
      await self.move_axes(rest)
    if "GG" in p:
      # Closed under the grip force, as Biomek's park closes it (it may stop on its own fingers).
      force = d.settings["GripperSqueezeForce"]
      await self.move_axes({"GG": d.to_counts("GG", p["GG"])}, forces={"GG": force})
      # The force leaves GG's position unknown, and the rail counts an unknown gripper as every
      # pose it could take, in the other pod's way: read it back now.
      await self._read_unknown_positions()

  async def move_aside(self) -> None:
    """Raise the pod and take it to the end of its X travel on its own side (the left pod to its
    lowest X, the right pod to its highest), leaving the other pod the most of the rail. Biomek
    moves a pod out of the way itself; the rail guard here refuses instead
    (`x_rail.py`), so a caller makes room with this.

    Raises:
      RuntimeError: without a declared configuration.
    """
    d = self.declared
    if d is None:
      raise RuntimeError(f"{self} needs a declared configuration")
    low, high = d.limits["X"]
    await self._move_over({"X": d.to_counts("X", low if self.side == "left" else high)})

  async def _move_over(
    self,
    targets: Mapping[str, int],
    speed: int = 1000,
    minimum_traverse_height: Optional[float] = None,
    z_final: Optional[float] = None,
    clearance: Optional[float] = None,
    allow_below_clearance: bool = False,
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
      minimum_traverse_height: the channels' lowest points while traveling. Default: each channel
        at `_traverse_heights` (as high as it goes). The gripper travels at its default
        always.
      z_final: where to set the channels' bottoms once there; None leaves them at the traverse
        height.
      clearance: the lowest height the deck under the path allows, from a deck model, if known.
      allow_below_clearance: travel lower than `clearance` and the obstacles allow, deliberately.
      tip_lengths: per channel Z axis, how far below its bottom the tip it carries ends, cm; none
        when None.
      gripper_height: GZ (cm) to travel at. Default: its traverse height, no higher than clears
        the pod's own pipettor for the travel's GR (`_gripper_traverse_height`).

    Raises:
      ValueError: for a channel Z axis in `targets`; for a path through a wall (a shield, a
        tower, the light curtain); for a traverse height below what the path needs (unless
        `allow_below_clearance`), a need above what the channels reach, or a height a channel
        cannot lift its tip to (under its `_traverse_ceilings`). Nothing is sent.
    """
    d = self._require_declared()
    channels = self.channel_z_axes
    stray = sorted(set(targets) & set(channels))
    if stray:
      raise ValueError(f"{self}: {stray} in a travel's targets; use z_final")
    lengths = {} if tip_lengths is None else dict(tip_lengths)
    await self._read_unknown_positions()
    area = self._travel_area(targets)
    need = g.obstacle_clearance(d.obstacles, *area)
    if need.walls:
      raise ValueError(f"{self}: the travel would pass through {', '.join(need.walls)}")
    required = max(need.height, -math.inf if clearance is None else clearance)
    heights, reach, traverse = self._traverse_targets(minimum_traverse_height, lengths)
    if required > reach:
      raise ValueError(f"{self}: the path needs {required:.3f} cm, above what the channels reach")
    if traverse < required and not allow_below_clearance:
      raise ValueError(
        f"{self}: traverse height {traverse:.3f} cm is below the {required:.3f} cm the path needs;"
        " pass allow_below_clearance=True to travel lower deliberately"
      )
    if gripper_height is None:
      # No higher than clears the pod's own pipettor for the gripper's angle, and its turn if the
      # travel turns it; only that lowers it (an axis above its traverse height stays there).
      gr = d.to_value("GR", targets.get("GR", self._cpositions["GR"][1]))
      clear = self._gripper_traverse_height(gr)
      if clear < heights["GZ"]:
        gripper_height = clear
    if gripper_height is not None:
      heights["GZ"] = gripper_height
      if d.to_counts("GZ", gripper_height) < self._cpositions["GZ"][1]:
        await self.move_axes({"GZ": d.to_counts("GZ", gripper_height)}, speed)
    await self._raise_to(heights, speed)
    result = await self.move_axes(targets, speed)
    if z_final is not None:
      await self.move_axes({n: d.to_counts(n, z_final) for n in channels}, speed)
    return result

  async def move_over(
    self,
    targets: Mapping[str, float],
    speed_fraction: float = 1.0,
    minimum_traverse_height: Optional[float] = None,
    z_final: Optional[float] = None,
    clearance: Optional[float] = None,
    allow_below_clearance: bool = False,
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
      clearance: the lowest height the deck under the path allows, mm (`traverse_clearance`).
      allow_below_clearance: travel lower than `clearance` and the obstacles allow, deliberately.
      tip_lengths: per channel Z axis, how far below its bottom the tip it carries ends, mm. The
        pod's own record when None (`carried_tip_lengths`).
      gripper_height: GZ, mm, to travel at. Default: its traverse height, no higher than clears
        the pod's own pipettor.

    Raises:
      ValueError: as `_move_over`, its Biomek-unit counterpart; for a `speed_fraction` outside
        (0, 1]. Nothing is sent.
    """
    return await self._move_over(
      {axis: units.axis_to_counts(self, axis, value) for axis, value in targets.items()},
      units.fraction_to_field(speed_fraction),
      minimum_traverse_height=units.optional_cm(minimum_traverse_height),
      z_final=units.optional_cm(z_final),
      clearance=units.optional_cm(clearance),
      allow_below_clearance=allow_below_clearance,
      tip_lengths=(
        None if tip_lengths is None else {a: units.to_cm(v) for a, v in tip_lengths.items()}
      ),
      gripper_height=units.optional_cm(gripper_height),
    )

  def traverse_heights(self) -> Dict[str, float]:
    """The height each Z-type axis (the channels' and the gripper's GZ) travels at by default, mm
    of its bottom above the deck: its ceiling (`traverse_ceilings`), or
    `default_minimum_traverse_height` where that is lower."""
    return {axis: units.to_mm(v) for axis, v in self._traverse_heights().items()}

  def traverse_ceilings(self) -> Dict[str, float]:
    """The highest each Z-type axis travels, mm of its bottom above the deck (`_traverse_ceilings`)."""
    return {axis: units.to_mm(v) for axis, v in self._traverse_ceilings().items()}

  def _traverse_targets(
    self, minimum_traverse_height: Optional[float], tip_lengths: Mapping[str, float]
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
    heights = self._traverse_heights()
    ceilings = self._traverse_ceilings()
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
    lengths = {} if tip_lengths is None else tip_lengths
    heights, _, _ = self._traverse_targets(minimum_traverse_height, lengths)
    await self._read_unknown_positions()
    await self._raise_to({n: heights[n] for n in self.channel_z_axes}, speed)

  async def _raise_to(self, heights: Mapping[str, float], speed: int) -> None:
    """Move the axes below their `heights` (cm) up to them, together."""
    d = self._require_declared()
    lift = {}
    for name, height in heights.items():
      counts = d.to_counts(name, height)
      if self._cpositions[name][1] < counts:  # counts grow with height on every Z-type axis
        lift[name] = counts
    if lift:
      await self.move_axes(lift, speed)

  def _travel_area(self, targets: Mapping[str, int]) -> Tuple[g.Range, g.Range]:
    """The X/Y area the pod's body covers from where it is to `targets`, cm."""
    d = self._require_declared()
    y_axis = "Y" if self.module_type == "multichannel" else "Y1"
    x0 = d.to_value("X", self._cpositions["X"][1])
    y0 = d.to_value("Y", self._cpositions[y_axis][1])
    x1 = d.to_value("X", targets.get("X", self._cpositions["X"][1]))
    y1 = d.to_value("Y", targets.get("Y", targets.get(y_axis, self._cpositions[y_axis][1])))
    (ax, ay), (bx, by) = g.pod_xy_box(d.geometry, x0, y0), g.pod_xy_box(d.geometry, x1, y1)
    return g.union(ax, bx), g.union(ay, by)

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

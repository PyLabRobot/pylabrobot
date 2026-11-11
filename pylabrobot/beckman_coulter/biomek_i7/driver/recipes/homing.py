"""Home All, as the Master Controller does it: a recipe of steps per arm type.

Each recipe was read off four Home Alls on one instrument (two after a power cycle, two with the
instrument already homed, one of those with the Span-8 Z channels not homed) and reproduces every
request each arm's board was sent, in order. Every target was the same in all four runs whatever
the axes' positions before homing. Where a Home All leaves X, the Multichannel's Y and the gripper
axes is each axis's home position in the instrument's motor record (`phome`, carried by its declared
configuration: `FromHome`); the gripper's two turns on the way are that position turned by 190 and
90 degrees in the instrument's GR scale, and the plunger's approach is its zero (`AtValue`). The Z
approaches (the Multichannel's -1000, the Span-8 channels' 9647) are this instrument's logged
counts, constants until another instrument's logs show where they come from.

A step decides between moving and homing the way the Master Controller does, by asking the board.
It first sets the axis's move parameters; a board refuses that with "not homed" for an axis that has
not been homed since power-up, and then the step homes it, otherwise it moves. So one recipe serves
a cold start, a homed instrument, and anything between.

The steps, as the requests they send (E = Set State ENABLED, P = Set Move Parameters at the axis's
maximum speeds, M = Move, H = Home, S = Stop Motion Service, MA = Move All):

  Home(axis, approach)        E P; homed: M [E] H; not homed: [E] H. The Span-8 board's sequence
                              has the bracketed E, the Multichannel board's does not.
  Retract(axis, target)       E P; homed: M; not homed: S E P, then [E] H.
  MoveTo(axis, target)        E P M.
  RetractGroup(axes, target)  E P on every axis together; homed: MA; not homed: S, home the axes
                              in pairs (E E H H), then E P on every axis again, MA.
  HomeGroup(...)              the Span-8's Y and Z channels: E P on Y1-Y8 at 0; homed: MA; not
                              homed: S. Then E on Y1-Y8, H Y1, home the Z channels in pairs, E P the
                              Z channels to their target, MA.

That is the Master Controller's Home All, which homes again an axis already homed: `force=True`.
Without it, an axis that was homed before the run began is only moved, and ends where a Home
would have left it: a Home step moves it to its approach and stops there (a Home leaves an axis at
its approach, to a few counts: the Multichannel's Z read -1000 six times out of six after Biomek's
homes, its plunger 43175-43178 for 43176, the gripper and Y axes 0 or -1), and a HomeGroup whose
channels were all homed before skips homing Y1 and the Z pairs. An axis the run itself homed is
homed again where the recipe says, as Biomek does (the gripper Z is homed twice at a cold start).
Every other request, and the order of every move, is the same either way; on an instrument with no
axis homed the two send the same requests.
"""

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Sequence, Set, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import NotHomedError
from pylabrobot.beckman_coulter.biomek_i7.driver.motors import AxisMove

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FromHome:
  """The axis's home position in the pod's motor record (`AxisTable.home_position`), turned by
  `degrees` in the pod's conversion for the axis: the turn's counts, rounded down, added to it. Both
  logged turns fit that (190 and 90 degrees from the gripper's home, -71487.4 and -33862.4 counts
  in this instrument's scale, were sent 71488 and 33863 counts on); another rounding would be at
  most a count, 0.003 degrees, off."""

  degrees: float = 0.0


@dataclass(frozen=True)
class AtValue:
  """The counts for `value` in the pod's conversion for the axis (`PodConfiguration.to_counts`)."""

  value: float


Target = Union[int, FromHome, AtValue]


@dataclass(frozen=True)
class Home:
  axis: str
  approach: Target


@dataclass(frozen=True)
class Retract:
  axis: str
  target: int


@dataclass(frozen=True)
class MoveTo:
  axis: str
  target: Target


@dataclass(frozen=True)
class RetractGroup:
  axes: Tuple[str, ...]
  target: int
  home_pairs: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class HomeGroup:
  probe_axes: Tuple[str, ...]
  probe_target: int
  enable_axes: Tuple[str, ...]
  home_axis: str
  then: RetractGroup


Step = Union[Home, Retract, MoveTo, RetractGroup, HomeGroup]

# The Span-8's Z channels, in the orders the Master Controller uses for them.
_SPAN8_Z = RetractGroup(
  axes=("Z7", "Z8", "Z5", "Z6", "Z3", "Z4", "Z1", "Z2"),
  target=9647,
  home_pairs=(("Z1", "Z5"), ("Z2", "Z6"), ("Z3", "Z7"), ("Z4", "Z8")),
)

# The Master Controller's `HM`: every axis but the plunger.
HOME_RECIPES: Dict[str, Tuple[Step, ...]] = {
  "multichannel": (
    Home("Z", -1000),
    Retract("GZ", 0),
    Home("GG", 0),
    MoveTo("GG", FromHome()),
    Home("GR", 0),
    MoveTo("GR", FromHome(190.0)),
    MoveTo("GR", FromHome(90.0)),
    Home("GY", 0),
    Home("GZ", 0),
    MoveTo("GY", FromHome()),
    Home("Y", 0),
    MoveTo("Y", FromHome()),
    MoveTo("GR", FromHome()),
    Home("X", 0),
    MoveTo("X", FromHome()),
  ),
  "span8": (
    _SPAN8_Z,
    Retract("GZ", 0),
    Home("GG", 0),
    MoveTo("GG", FromHome()),
    Home("GR", 0),
    MoveTo("GR", FromHome(190.0)),
    MoveTo("GR", FromHome(90.0)),
    Home("GY", 0),
    Home("GZ", 0),
    MoveTo("GY", FromHome()),
    HomeGroup(
      probe_axes=("Y7", "Y8", "Y5", "Y6", "Y3", "Y4", "Y1", "Y2"),
      probe_target=0,
      enable_axes=("Y2", "Y4", "Y3", "Y6", "Y5", "Y8", "Y7", "Y1"),
      home_axis="Y1",
      then=_SPAN8_Z,
    ),
    MoveTo("GR", FromHome()),
    Home("X", 0),
    MoveTo("X", FromHome()),
  ),
}

# Whether the board's sequence enables an axis again right before homing it.
ENABLE_BEFORE_HOME = {"multichannel": False, "span8": True}

# The order the Master Controller enables every axis in after the interlock is armed, and reads
# their positions in. The Multichannel board is sent one Z enable first.
ENABLE_ORDER: Dict[str, Tuple[str, ...]] = {
  "multichannel": ("GZ", "GR", "GY", "GG", "Z", "D", "X", "Y"),
  "span8": (
    "GR", "GG", "GZ", "D8", "GY", "D6", "D7", "D4", "D5", "D2", "D3", "Z8", "D1", "Z6", "Z7",
    "Z4", "Z5", "Z2", "Z3", "Y8", "Z1", "Y6", "Y7", "Y4", "Y5", "Y2", "Y3", "X", "Y1",
  ),
}  # fmt: skip
ENABLE_PREFIX: Dict[str, Tuple[str, ...]] = {"multichannel": ("Z",), "span8": ()}


class Homer:
  """Runs recipes on one pod, through the pod's drive commands (`Pod._unchecked_fw_*`) only.

  Args:
    pod: the pod whose board is sent the requests.
    force: home again axes that were homed before the run, as the Master Controller does.
  """

  def __init__(self, pod: "Pod", force: bool = False):
    self.pod = pod
    self.force = force
    self.enable_before_home = ENABLE_BEFORE_HOME[pod.module_type]
    self._homed_in_run: Set[str] = set()

  def _keep_home(self, axes: Sequence[str]) -> bool:
    """Whether axes that answer homed may be left as they are: not forced, and none of them homed
    by this run (the recipe's later homes of those are Biomek's own)."""
    return not self.force and not self._homed_in_run.intersection(axes)

  @staticmethod
  def _step_targets(step: Step) -> List[Tuple[str, Target]]:
    if isinstance(step, (Home,)):
      return [(step.axis, step.approach)]
    if isinstance(step, (Retract, MoveTo)):
      return [(step.axis, step.target)]
    if isinstance(step, RetractGroup):
      return [(axis, step.target) for axis in step.axes]
    return [(axis, step.probe_target) for axis in step.probe_axes] + Homer._step_targets(step.then)

  def targets(self, step: Step) -> List[Tuple[str, int]]:
    """Every (axis, position) a step may set move parameters to on this pod, in order."""
    return [(axis, self._counts(axis, target)) for axis, target in self._step_targets(step)]

  @staticmethod
  def axes(steps: Sequence[Step]) -> List[str]:
    """Every axis a recipe moves or homes, once each."""
    names = [axis for step in steps for axis, _ in Homer._step_targets(step)]
    return list(dict.fromkeys(names))

  def _counts(self, axis: str, target: Target) -> int:
    """`target` on this pod's `axis`, in counts.

    Raises:
      ValueError: for a turn or a value on a pod with no declared configuration to convert it.
    """
    if isinstance(target, int):
      return target
    if isinstance(target, FromHome) and not target.degrees:
      return self.pod.axis(axis).home_position
    declared = self.pod.declared
    if declared is None:
      raise ValueError(f"{self.pod}: {target} on {axis} needs the pod's declared configuration")
    if isinstance(target, AtValue):
      return declared.to_counts(axis, target.value)
    turn = target.degrees * declared.conversion[axis].const
    return self.pod.axis(axis).home_position + math.floor(turn)

  async def run(self, steps: Sequence[Step]) -> None:
    for step in steps:
      logger.debug("%s: %s", self.pod, step)
      if isinstance(step, Home):
        await self._home(step)
      elif isinstance(step, Retract):
        await self._retract(step)
      elif isinstance(step, MoveTo):
        await self._move_to(step)
      elif isinstance(step, RetractGroup):
        await self._retract_group(step)
      else:
        await self._home_group(step)

  # -- drive commands --------------------------------------------------------

  async def _enable(self, names: Sequence[str]) -> None:
    await self.pod._unchecked_fw_enable(names)

  async def _probe(self, names: Sequence[str], target: int) -> bool:
    """Set move parameters on every axis at its maximum speeds; True if the board took them all,
    False if any axis is not homed."""
    accepted = await self.pod._unchecked_fw_set_move_parameters(
      [AxisMove(name, target) for name in names]
    )
    if not all(accepted) and any(accepted):
      # Only all-or-none was seen; homing every axis of the group is the safe reading.
      logger.warning("%s: some of %s are homed and some are not; homing them all", self.pod, names)
    return all(accepted)

  async def _stop(self) -> None:
    await self.pod._unchecked_fw_stop_motion()

  async def _move(self, name: str) -> None:
    await self.pod._unchecked_fw_move(name)

  async def _move_all(self) -> None:
    await self.pod._unchecked_fw_move_all()

  async def _home_axes(self, names: Sequence[str]) -> None:
    await self.pod._unchecked_fw_home(names)
    self._homed_in_run.update(names)

  # -- steps -----------------------------------------------------------------

  async def _home(self, step: Home) -> None:
    await self._enable([step.axis])
    if await self._probe([step.axis], self._counts(step.axis, step.approach)):
      await self._move(step.axis)
      if self._keep_home([step.axis]):
        return
    if self.enable_before_home:
      await self._enable([step.axis])
    await self._home_axes([step.axis])

  async def _retract(self, step: Retract) -> None:
    await self._enable([step.axis])
    if await self._probe([step.axis], step.target):
      await self._move(step.axis)
      return
    await self._stop()
    await self._enable([step.axis])
    if await self._probe([step.axis], step.target):
      await self._move(step.axis)
      return
    if self.enable_before_home:
      await self._enable([step.axis])
    await self._home_axes([step.axis])

  async def _move_to(self, step: MoveTo) -> None:
    await self._enable([step.axis])
    if not await self._probe([step.axis], self._counts(step.axis, step.target)):
      raise NotHomedError(0x32, None, f"{self.pod}: {step.axis} is not homed for {step}")
    await self._move(step.axis)

  async def _home_pairs(self, pairs: Sequence[Tuple[str, str]]) -> None:
    for pair in pairs:
      await self._enable(pair)
      await self._home_axes(pair)

  async def _retract_group(self, step: RetractGroup) -> None:
    await self._enable(step.axes)
    if not await self._probe(step.axes, step.target):
      await self._stop()
      await self._home_pairs(step.home_pairs)
      await self._enable(step.axes)
      if not await self._probe(step.axes, step.target):
        raise NotHomedError(0x32, None, f"{self.pod}: {step.axes} still not homed after homing")
    await self._move_all()

  async def _home_group(self, step: HomeGroup) -> None:
    await self._enable(step.probe_axes)
    homed = await self._probe(step.probe_axes, step.probe_target)
    if homed:
      await self._move_all()
    else:
      await self._stop()
    group = (step.home_axis, *step.probe_axes, *step.then.axes)
    if not homed or not self._keep_home(group):
      await self._enable(step.enable_axes)
      await self._home_axes([step.home_axis])
      await self._home_pairs(step.then.home_pairs)
    await self._enable(step.then.axes)
    if not await self._probe(step.then.axes, step.then.target):
      raise NotHomedError(0x32, None, f"{self.pod}: {step.then.axes} still not homed after homing")
    await self._move_all()

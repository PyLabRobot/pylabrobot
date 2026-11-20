"""A Span-8 pod's eight independent channels: their syringe pumps, valves, liquid-level sensors and
tip sensor. Reached as `driver.right_x_arm.pipettes` (on whichever pod is a Span-8).

The channels' axes (Y1-Y8, Z1-Z8, D1-D8) are the pod's, moved through `Pod.move_axes`. The pumps'
valves and cutoff velocity are kept in the pod's state, because every pump move carries them;
this feature is what sets them. The host commands here are the Master Controller's Span-8 ones, as
its logs show them: `CV`, `VI`/`VO`/`VB`/`VE`, `SS`, `GT`, `VT`.

Picking tips up and discarding them are Biomek's compositions of plain moves, as its logs show
them, replayed against every logged one:

- pick up, per set of spots the probes reach in one X/Y/S move (one rack, one X, evenly spaced in
  Y): probe 1 over its spot with the span the spots need; the probes used, and every other probe
  without a tip over an emptied spot of the rack (tip tracking), down to an approach over the rack's top (`AdditionalSafeHeight` +
  `AvoidanceMargin`, Biomek's safe height there, in its world model: by pod 1's corner of the
  position); then pressed in groups (the first probe still waiting and later ones
  at least `MinLoadingInterval` from the last taken, up to `MaxLoadingProbes`: 1+5, 2+6, 3+7, 4+8).
  One move per group at `LoadSpeed` sends the group to the seat (the rack's top + `TipLoadZOffset`
  + the probe's `LoadingOffsets`) and the group before it up to the exit (the top + 1.0 cm); the
  last group then rises alone. `TiploadExitZOffset` and `TipLoadTime` are declared, but no logged
  move depends on either;
- discard: probe 1 at the trash's `XSpan`/2 + `Span8TipDisposalOffsetX`, the probes centred in Y
  ((`YSpan` - span)/2 + `Span8TipDisposalOffsetY`) at the narrowest span, the probes at
  `DiscardHeight` - 1.0 cm; then shucked in groups (the first probe still to go and
  later ones at least `MinUnloadingInterval` from the last taken, up to `MaxUnloadingProbes`): each
  probe of a group driven to the top of its travel + `DiscardOvershoot` at `DiscardSpeed`, where
  the pod's stripper pushes the tip off, and back;
- with `DetectTips` set, the tip sensor then checks the probes worked (`VT`).

Priming fills the pumps and probes with system fluid over the Span-8 wash station, as Biomek's
prime dialog does (the pumps homed, then strokes until the operator stops it), and as logged
(2026-10-09; the same position in four logs): the probes,
without tips, centred in Y at the narrowest span and lowered at speed 250 to the wash position's
height; the pumps homed (`HD`); then strokes, each a full draw with the valves to the system fluid
(`VI`) and a full push out through the probes (`VO`), at the pumps' `D1SpeedLimit` (80.17 % is the
logged 801); when stopped, the probes up and the system trailing air gap drawn at speed 10.
`start_prime` and `stop_prime` are those two halves, for an operator who watches for bubbles;
`prime` runs a set number of strokes.

The stripper works only at the top of travel, so a Span-8 cannot put tips back into a rack: they go
to waste. Carrying probes travel no higher than the top less `DiscardOffset`
(`Pod._traverse_ceilings`). Which probes carry which tips is its record of what they picked up.
"""

import asyncio
import collections
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7 import targeting, units
from pylabrobot.beckman_coulter.biomek_i7.driver import liquid, motion
from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import NotHomedError, TipCheckError
from pylabrobot.beckman_coulter.biomek_i7.resource_model.heads import shaft_of, show_tip
from pylabrobot.beckman_coulter.biomek_i7.techniques import (
  Conditioning,
  HeightFrom,
  MixHeights,
  TipTouch,
)
from pylabrobot.beckman_coulter.biomek_i7.units import (
  cutoff_to_counts,
  offset_to_biomek,
  percent_to_field,
  rate_to_field,
)
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.volume_tracker import VolumeTracker, does_volume_tracking

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck
  from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
    DeviceConfiguration,
    PodConfiguration,
  )
  from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod

logger = logging.getLogger(__name__)

# The valves' Move All timeout, as the Master Controller's logs show it.
VALVE_MOVE_ALL_TIMEOUT_MS = 6000

# Cutoff velocity range (`CV`), counts/s; quantized to 4.
CUTOFF_VELOCITY_RANGE = (200, 10800)

PROBES = 8
DEFAULT_TRASH = "TR2"  # the right trash, where Biomek discards the Span-8's tips
EXIT_ABOVE_TOP = 1.0  # cm: the probes leave a pickup this far above the rack (logged)
DISCARD_BELOW = 1.0  # cm: the probes shuck from `DiscardHeight` less this (logged)
# How far apart, cm, two spots may be in X, or off the probes' even spacing in Y, for one move.
ALIGNMENT_TOLERANCE = 0.005
# uL: how far a pump read back from counts may sit below what it was sent to (a count is 0.042 uL).
PLUNGER_TOLERANCE = 0.05
AIR_GAP_SPEED = 200  # the speed field Biomek draws the system trailing air gap at
DEFAULT_WASH = "W2"  # the Span-8 wash station's position, where Biomek primes
# cm: probe 1's X from the wash position's centre in every logged prime (X -7761 counts, 143.375
# cm, where W2's centre is 143.855). Not traced to Biomek's code; kept so the probes enter the
# trough where Biomek's do.
PRIME_X_OFFSET = -0.48
PRIME_Z_SPEED = 250  # the speed field the probes are lowered into the wash station at
PRIME_AIR_GAP_SPEED = 10  # the speed field of the air gap drawn after priming
# s: the least a prime stroke takes. A stroke takes about 4.4 s on the instrument (logged); this
# keeps an instant simulator to one a second rather than hundreds.
MINIMUM_STROKE_SECONDS = 1.0
# The Span-8's `HD`: these pumps enabled together, then D1, then one Home of D1 homes all eight.
PUMP_ENABLE_ORDER = ("D2", "D4", "D3", "D6", "D5", "D8", "D7")


def press_groups(probes: Sequence[int], most: int, interval: int) -> List[Tuple[int, ...]]:
  """Biomek's grouping of probes for pressing onto tips or shucking them, as its logs show it: each
  group takes the first probe still waiting, then later ones at least
  `interval` probes from the last one taken, up to `most`."""
  waiting = sorted(probes)
  groups = []
  while waiting:
    group = [waiting.pop(0)]
    for probe in list(waiting):
      if len(group) < most and probe - group[-1] >= interval:
        group.append(probe)
        waiting.remove(probe)
    groups.append(tuple(group))
  return groups


@dataclass(frozen=True)
class PrimePlan:
  """Where a prime goes (probe 1, Biomek cm; the probes' ends at `z`), and its strokes: uL per
  pump and the pumps' speed field."""

  x: float
  y: float
  span: float
  z: float
  stroke: float
  speed: int


class _Job(NamedTuple):
  """A probe and its tip spot, with where the probe must be (Biomek cm), the rack's top for this
  pod, the rack's top in Biomek's world model (pod 1's corner of the position) and the rack's
  spots with where each is (X, Y)."""

  probe: int
  spot: TipSpot
  x: float
  y: float
  top: float
  world_top: float
  rack_spots: Tuple[Tuple[TipSpot, float, float], ...]


@dataclass(frozen=True)
class PickUpPlan:
  """One X/Y/S move's pickup, in Biomek's frame (cm): probe 1's X/Y, the span from probe 1 to 8,
  which probe meets which spot, the heights (the probes' bottoms), the probes lowered to the
  approach (those picking up, and those without tips over the rack) and the press groups."""

  x: float
  y: float
  span: float
  pairs: Tuple[Tuple[int, TipSpot], ...]
  approach: float
  lowered: Tuple[int, ...]
  seats: Dict[int, float]
  exit: float
  groups: Tuple[Tuple[int, ...], ...]


@dataclass(frozen=True)
class DiscardPlan:
  """A discard, in Biomek's frame (cm): probe 1's X/Y, the span, the probes' Z before the shuck,
  the shuck groups and each probe's shuck target."""

  x: float
  y: float
  span: float
  z: float
  groups: Tuple[Tuple[int, ...], ...]
  shuck: Dict[int, float]


@dataclass(frozen=True)
class ProbePlan:
  """One X/Y/S move's aspirate or dispense, in Biomek's frame (cm): probe 1's X/Y, the span, the
  probes' channels, the template's moves (`driver/liquid.py`) and which probe works which
  container."""

  x: float
  y: float
  span: float
  channels: Tuple[liquid.Channel, ...]
  steps: Tuple[liquid.Step, ...]
  pairs: Tuple[Tuple[int, Container], ...]


def tip_problems(states: Sequence[Optional[str]], expected: Mapping[int, bool]) -> List[str]:
  """What the Master Controller's `VT` reports, in its words: per probe (1..8) whose tip is not as
  `expected` says, in probe order. A state of `loaded` is a tip, `unknown` cannot be told, and
  anything else is no tip."""
  problems = []
  for probe, should in sorted(expected.items()):
    state = states[probe - 1]
    if state == "unknown":
      problems.append(f"The system cannot determine whether or not probe {probe} has a tip on.")
    elif state == "loaded" and not should:
      problems.append(f"Probe {probe} should not have a tip on, but it does.")
    elif state != "loaded" and should:
      problems.append(f"Probe {probe} should have a tip on, but it does not.")
  return problems


class Pipettes:
  """The eight channels of a Span-8 pod."""

  # Where neither the caller nor a technique gives a value (STAR P6): the tips' ends 1 mm above
  # the bottom, Biomek's Water liquid type's 90 uL/s and cutoff velocity, the Z speed and the
  # conductivity of its S8 techniques.
  default_liquid_height: float = 1.0  # mm
  default_flow_rate: float = 90.0  # uL/s
  default_z_speed: float = 109.3  # mm/s: its S8 techniques' 30 % of a Span-8 Z on this unit
  default_cutoff_velocity: float = 150.0  # uL/s
  default_lls_sensitivity: int = 3500

  def __init__(self, pod: "Pod"):
    self._pod = pod
    # The tips on the probes, by probe (1..8), as this session picked them up.
    self._tips: Dict[int, Tip] = {}
    # The probes' channels in the resource tree, probe 1 first, as the STAR's
    # `pipettes.resources`: each one's shaft carries its tip. Set by the device that models them
    # (`BiomekI7Device`); empty when nothing models them.
    self.resources: List[Resource] = []
    self._priming: Optional["asyncio.Task[int]"] = None
    self._stop_priming = asyncio.Event()
    # Whether the pumps are homed: None until this session homes them (`home_pumps`) or asks
    # (`_require_pumps_homed`); a power-up leaves them not homed, and Home All does not home them.
    self._pumps_homed: Optional[bool] = None

  def __str__(self) -> str:
    return f"{self._pod}'s channels"

  @property
  def arm(self) -> "Pod":
    """The pod these channels are on (STAR's `feature.arm`)."""
    return self._pod

  # -- pumps -------------------------------------------------------------------------

  def _set_cutoff_velocity(self, velocity: int) -> None:
    """The syringe pumps' cutoff velocity for the moves that follow, counts/s: the Master
    Controller's `CV`, quantized to 4 as it does. Sends nothing.

    Private: callers pass it per call (`aspirate` / `dispense`'s `cutoff_velocity`, uL/s). It is
    kept as the Master Controller keeps it, because Biomek's moves between two `CV`s (the air gap
    before an aspirate, a prime) carry the last one set.

    Raises:
      ValueError: outside 200..10800.
    """
    low, high = CUTOFF_VELOCITY_RANGE
    if not low <= velocity <= high:
      raise ValueError(f"cutoff velocity {velocity} is outside {low}..{high}")
    self._pod._cutoff_velocity = motion.quantize(velocity, 4.0)

  @property
  def valves(self) -> Dict[str, Optional[p.Valve]]:
    """Each syringe pump's valve, as this driver last set it; None until set."""
    return dict(self._pod._valves)

  async def set_valves(self, valves: Mapping[str, p.Valve]) -> None:
    """Turn syringe pumps' valves: the Master Controller's `VI`/`VO`/`VB`/`VE`. Each pump is sent Set Move Parameters at the position it is at, with
    its minimum start and cutoff velocities, maximum velocity and acceleration, and the new valve;
    then one Move All. Positions not known are read first, as before any move.

    Raises:
      ValueError: for an axis that is not a syringe pump, or an unknown valve.
    """
    pod = self._pod
    for name, valve in valves.items():
      if not pod.axis(name).is_pump:
        raise ValueError(f"{pod}: {name} is not a syringe pump")
      if valve not in p.VALVE_POSITIONS:
        raise ValueError(f"unknown valve {valve!r}; have {sorted(p.VALVE_POSITIONS)}")
    if not valves:
      return
    logger.info("%s V %s", pod.component, " ".join(f"{n}={v}" for n, v in valves.items()))
    try:
      await pod._read_unknown_positions(pumps=True)
      await self._unchecked_fw_set_valves(valves)
      await pod._unchecked_fw_move_all(VALVE_MOVE_ALL_TIMEOUT_MS)
    except BaseException:
      for name in valves:
        pod._valves[name] = None
      pod._forget_positions(valves)
      raise
    pod._valves.update(valves)

  async def _unchecked_fw_set_valves(self, valves: Mapping[str, p.Valve]) -> None:
    """Each pump's Set Move Parameters to turn its valve where it stands, sent together."""
    pod = self._pod
    requests = []
    for name, valve in valves.items():
      axis = pod.axis(name)
      limits, pump = axis.limits, axis.pump_limits
      requests.append(
        p.set_pump_move_parameters(
          axis.index,
          pod._cpositions[name][0] or 0,  # read before this is called
          pump.min_start_velocity,
          limits.max_velocity,
          pump.min_cutoff_velocity,
          limits.max_acceleration,
          valve,
        )
      )
    await pod.board.send_many(requests)

  # -- sensors -----------------------------------------------------------------------

  async def _set_lls_sensitivity(self, value: int) -> None:
    """The liquid-level sensors' sensitivity, 0-4095: the Master Controller's `SS` (Biomek sends
    3500 on this instrument). Moves nothing. Private: callers pass it per call (`aspirate` /
    `dispense`'s `lls_sensitivity`), which sets it before each move measured from the liquid.

    Raises:
      ValueError: outside 0..4095.
    """
    if not 0 <= value <= 4095:
      raise ValueError(f"{self._pod}: LLS sensitivity is 0..4095, not {value}")
    logger.info("%s SS %d", self._pod.component, value)
    await self._pod.board.send(p.set_lls_cd_sensitivity(False, value))

  async def sense_tip_presence(self) -> List[Optional[str]]:
    """Each probe's tip, `loaded`, `unloaded` or `unknown`, as the tip sensor reports it: the
    Master Controller's `GT` (STAR's `sense_tip_presence`, which answers 1 or 0 per channel; the
    Span-8's sensor can also answer that it cannot tell). Moves nothing."""
    return p.parse_tip_status((await self._pod.board.send(p.get_tip_status())).payload)

  async def verify_tips(self, expected: Mapping[int, bool]) -> None:
    """Check that channels do or do not carry a tip, from the tip sensor: the Master Controller's
    `VT`. `expected` maps a channel (0-indexed from the back, as `use_channels`) to whether it
    should have a tip. Moves nothing.

    Raises:
      ValueError: for a channel out of range.
      TipCheckError: naming every probe that does not match, or whose tip cannot be determined.
    """
    states = await self.sense_tip_presence()
    for channel in expected:
      if not 0 <= channel < len(states):
        raise ValueError(f"{self._pod}: no channel {channel}; have 0..{len(states) - 1}")
    problems = tip_problems(states, {channel + 1: want for channel, want in expected.items()})
    if problems:
      raise TipCheckError(f"{self._pod}: " + "\n".join(problems))

  # -- priming ------------------------------------------------------------------------------------

  async def home_pumps(self) -> None:
    """Home the syringe pumps, the Master Controller's Span-8 `HD`: the pumps enabled (D1 last)
    and one Home of D1, after which all eight read 0 (logged 2026-10-09). `start_prime` does this;
    Home All does not home the Span-8's pumps."""
    pod = self._pod
    logger.info("%s HD", pod.component)
    await pod._unchecked_fw_enable(PUMP_ENABLE_ORDER)
    await pod._unchecked_fw_enable(["D1"])
    await pod._unchecked_fw_home(["D1"])
    pod._set_home_positions(f"D{n}" for n in range(1, PROBES + 1))
    self._pumps_homed = True

  def plan_prime(self, wash: Optional[Resource] = None) -> PrimePlan:
    """Where a prime goes, over `wash` (the deck's `W2` by default), and its strokes: a full
    stroke at the pumps' `D1SpeedLimit`."""
    deck, configuration, declared = self._context()
    position = wash if wash is not None else deck.get_position(DEFAULT_WASH)
    at = targeting.target(deck, configuration, self._pod.pod_number, position)
    span = declared.limits["Span"][0]
    return PrimePlan(
      x=at.x + PRIME_X_OFFSET,
      y=at.y - span / 2,
      span=span,
      z=at.z,
      stroke=declared.limits["D1"][1],
      speed=percent_to_field(declared.settings["D1SpeedLimit"], truncate=True),
    )

  @property
  def priming(self) -> bool:
    """Whether a prime started by `start_prime` has not been stopped."""
    return self._priming is not None

  async def start_prime(
    self,
    wash: Optional[Resource] = None,
    strokes: Optional[int] = None,
    minimum_traverse_height: Optional[float] = None,
    minimum_stroke_seconds: float = MINIMUM_STROKE_SECONDS,
  ) -> None:
    """Lower the probes into the wash station, home the pumps and start priming: strokes run in
    the background until `stop_prime` (or, given `strokes`, until that many are done; `stop_prime`
    still lifts the probes). Heights in mm, as `pick_up_tips`. A stroke takes at least
    `minimum_stroke_seconds`: on the instrument one takes longer (about 4.4 s), so this only paces
    a simulator that answers at once, which would otherwise stroke hundreds of times a second.

    Raises:
      RuntimeError: if a prime is running, or a probe carries a tip.
    """
    if self._priming is not None:
      raise RuntimeError(f"{self}: already priming; call stop_prime first")
    carrying = [n for n, t in enumerate(self.tips, start=1) if t is not None]
    if carrying:
      raise RuntimeError(f"{self}: probes {carrying} carry tips; discard them before priming")
    plan = self.plan_prime(wash)
    _, _, declared = self._context()
    pod = self._pod
    logger.info("%s prime over %s", pod.component, wash.name if wash else DEFAULT_WASH)
    await self._travel(plan.x, plan.y, plan.span, minimum_traverse_height)
    await pod.move_axes(
      {f"Z{n}": declared.to_counts(f"Z{n}", plan.z) for n in range(1, PROBES + 1)}, PRIME_Z_SPEED
    )
    await self.home_pumps()
    self._stop_priming.clear()
    self._priming = asyncio.ensure_future(
      self._prime_strokes(plan, strokes, minimum_stroke_seconds)
    )

  async def _prime_strokes(self, plan: PrimePlan, limit: Optional[int], least: float) -> int:
    _, _, declared = self._context()
    pumps = [f"D{n}" for n in range(1, PROBES + 1)]
    loop = asyncio.get_running_loop()
    done = 0
    while not self._stop_priming.is_set() and (limit is None or done < limit):
      started = loop.time()
      await self.set_valves({a: "input" for a in pumps})
      await self._pod.move_axes({a: declared.to_counts(a, plan.stroke) for a in pumps}, plan.speed)
      await self.set_valves({a: "output" for a in pumps})
      await self._pod.move_axes({a: 0 for a in pumps}, plan.speed)
      done += 1
      logger.debug("%s prime stroke %d", self._pod.component, done)
      rest = least - (loop.time() - started)
      if rest > 0 and (limit is None or done < limit):
        try:  # the rest of the stroke's least time, cut short by a stop
          await asyncio.wait_for(self._stop_priming.wait(), rest)
        except asyncio.TimeoutError:
          pass
    return done

  async def stop_prime(
    self, park: bool = True, minimum_traverse_height_end: Optional[float] = None
  ) -> int:
    """Stop priming once the stroke under way has pushed out, lift the probes, draw the system
    trailing air gap and, as Biomek does, park the pod (`park`; over the wash station, so only the
    probes and the gripper move). Returns the strokes made. `minimum_traverse_height_end` (mm):
    where the probes are lifted to without parking.

    Raises:
      RuntimeError: if no prime was started. A stroke's error is raised here.
    """
    task = self._priming
    if task is None:
      raise RuntimeError(f"{self}: not priming")
    self._stop_priming.set()
    try:
      strokes = await task
    finally:
      self._priming = None
    _, _, declared = self._context()
    pod = self._pod
    end = minimum_traverse_height_end
    await pod._lift_channels(None if end is None else end / 10)
    gap = declared.settings["SystemTrailingAirgap"]
    pumps = [f"D{n}" for n in range(1, PROBES + 1)]
    await pod.move_axes({a: declared.to_counts(a, gap) for a in pumps}, PRIME_AIR_GAP_SPEED)
    logger.info("%s primed: %d strokes", pod.component, strokes)
    if park:
      await self.park()
    return strokes

  async def park(self, wash: Optional[Resource] = None) -> None:
    """Park the pod as Biomek parks a Span-8: over its wash station (`wash`, the deck's `W2` by
    default), where it primes, at the narrowest span (logged after Home All and after a prime:
    X -7760, Y 203370), the probes and the gripper raised and the gripper to its park
    (`Pod.park`). Without a wash station on the deck, it parks where it stands.
    """
    deck, _, declared = self._context()
    over = None
    if wash is not None or DEFAULT_WASH in deck.position_names:
      plan = self.plan_prime(wash)
      over = {
        "X": declared.to_counts("X", plan.x),
        "Y": declared.to_counts("Y", plan.y),
        "S": declared.to_counts("S", plan.span),
      }
    await self._pod._park(over)

  async def prime(
    self,
    strokes: int,
    wash: Optional[Resource] = None,
    minimum_stroke_seconds: float = MINIMUM_STROKE_SECONDS,
  ) -> int:
    """`start_prime` for `strokes` strokes, then `stop_prime`. Returns the strokes made."""
    await self.start_prime(wash, strokes=strokes, minimum_stroke_seconds=minimum_stroke_seconds)
    if self._priming is None:
      raise RuntimeError(f"{self._pod}: the prime did not start")
    await asyncio.shield(self._priming)
    return await self.stop_prime()

  # -- tips ---------------------------------------------------------------------------------------

  @property
  def tips(self) -> List[Optional[str]]:
    """Each probe's tip (its model, else its name), probe 1..8; None for none: the ones picked up,
    or that the shafts carried when the device restored the last session's state (`_adopt_tips`),
    checked against the tip sensor after each load and discard."""
    out: List[Optional[str]] = []
    for probe in range(1, PROBES + 1):
      tip = self._tips.get(probe)
      out.append(None if tip is None else tip.model or tip.name)
    return out

  def _set_tips(self, probes: Mapping[int, Optional[Tip]]) -> None:
    """Record the tip each of `probes` (1..8) carries (None: none), show it on the probe's shaft
    (`_show_tips`), and tell the driver (`on_tips_changed`: the device saves its heads' tips)."""
    for probe, tip in probes.items():
      if tip is None:
        self._tips.pop(probe, None)
      else:
        self._tips[probe] = tip
    self._show_tips(list(probes))
    if self._pod.driver is not None:
      self._pod.driver.on_tips_changed()

  def _adopt_tips(self) -> None:
    """Take as carried the tips the probes' shafts hold: the device puts them there from the last
    session's state."""
    for probe, channel in enumerate(self.resources, start=1):
      tip = shaft_of(channel).tip
      if isinstance(tip, Tip):
        self._tips[probe] = tip

  def _show_tips(self, probes: Optional[Sequence[int]] = None) -> None:
    """Put on each of `probes`' (1..8; all, when None) shafts in the model the tip it carries, and
    take off the shaft of one that carries none, as the STAR mounts and releases its tips."""
    if not self.resources:
      return
    for probe in range(1, PROBES + 1) if probes is None else probes:
      if 1 <= probe <= len(self.resources):
        show_tip(shaft_of(self.resources[probe - 1]), self._carried_tip(probe))

  def _carried_tip(self, probe: int) -> Optional[Tip]:
    """The tip on `probe`, or None."""
    return self._tips.get(probe)

  def _context(self) -> Tuple["BiomekI7Deck", "DeviceConfiguration", "PodConfiguration"]:
    driver, declared = self._pod.driver, self._pod.declared
    if driver is None or driver.deck is None or declared is None:
      raise RuntimeError(f"{self} needs a deck and a declared configuration (use `BiomekI7`)")
    return driver.deck, driver.declared, declared

  @staticmethod
  def tip_length(tip: Tip) -> float:
    """How far below its probe `tip`'s end is, mm: its length less its `SpanSeatingDepth`
    (`fitting_depth`)."""
    return units.to_mm(Pipettes._tip_length(tip))

  @staticmethod
  def _tip_length(tip: Tip) -> float:
    """How far below its probe `tip`'s end is, cm: its length less its `SpanSeatingDepth`
    (`fitting_depth`)."""
    return float((tip.get_size_z() - tip.fitting_depth) / 10)

  def _carried_tip_lengths(self) -> Dict[str, float]:
    """Per probe carrying a tip, its Z axis and how far below the probe the tip ends, cm
    (`_carried_tip`).

    Raises:
      RuntimeError: for a tip whose length is not known.
    """
    lengths = {}
    for probe, model in enumerate(self.tips, start=1):
      if model is None:
        continue
      tip = self._carried_tip(probe)
      if tip is None:
        raise RuntimeError(f"{self}: the length of probe {probe}'s tip ({model}) is not known")
      lengths[f"Z{probe}"] = self._tip_length(tip)
    return lengths

  def _probe(self, channel: int) -> int:
    if not 0 <= channel < PROBES:
      raise ValueError(f"{self}: no channel {channel}; have 0..{PROBES - 1}")
    return channel + 1

  def plan_pick_up(
    self,
    tip_spots: Sequence[TipSpot],
    use_channels: Optional[Sequence[int]] = None,
    offsets: Optional[Sequence[Coordinate]] = None,
  ) -> List[PickUpPlan]:
    """The moves a pickup takes: one per set of spots the probes reach at once (one rack, one X,
    spaced evenly in Y for the probes taking them, a span the pod reaches), in channel order.

    Raises:
      ValueError: for a channel out of range or given twice, or not one channel per spot.
    """
    deck, configuration, declared = self._context()
    channels = list(range(len(tip_spots))) if use_channels is None else list(use_channels)
    if len(channels) != len(tip_spots) or len(set(channels)) != len(channels):
      raise ValueError(f"{self}: one distinct channel per tip spot, please")
    offsets = [Coordinate.zero()] * len(tip_spots) if offsets is None else list(offsets)
    number = self._pod.pod_number
    jobs: List[_Job] = []
    spots_of: Dict[str, Tuple[Tuple[TipSpot, float, float], ...]] = {}
    for channel, spot, offset in sorted(zip(channels, tip_spots, offsets), key=lambda j: j[0]):
      rack = spot.parent
      if rack is None:
        raise ValueError(f"{self}: tip spot {spot.name} is in no rack")
      at = targeting.target(deck, configuration, number, spot, offset=offset)
      top = targeting.target(deck, configuration, number, rack, z="t").z
      world_top = targeting.target(deck, configuration, 1, rack, z="t").z
      if rack.name not in spots_of:
        spots_of[rack.name] = tuple(
          (s, t.x, t.y)
          for s, t in (
            (s, targeting.target(deck, configuration, number, s)) for s in rack.get_all_items()
          )
        )
      jobs.append(_Job(self._probe(channel), spot, at.x, at.y, top, world_top, spots_of[rack.name]))
    low, high = declared.limits["Span"]
    plans: List[PickUpPlan] = []
    while jobs:
      first = jobs.pop(0)
      batch: List[_Job] = [first]
      span: Optional[float] = None
      for job in list(jobs):
        if abs(job.x - first.x) > ALIGNMENT_TOLERANCE or abs(job.top - first.top) > 1e-6:
          continue
        if span is None:
          wanted = (job.y - first.y) * (PROBES - 1) / (job.probe - first.probe)
          if not low - 1e-6 <= wanted <= high + 1e-6:
            continue
          span = wanted
        if abs(first.y + (job.probe - first.probe) * span / (PROBES - 1) - job.y) <= (
          ALIGNMENT_TOLERANCE
        ):
          batch.append(job)
          jobs.remove(job)
      plans.append(self._pick_up_plan(batch, low if span is None else span, declared))
    return plans

  def _pick_up_plan(
    self, batch: List["_Job"], span: float, declared: "PodConfiguration"
  ) -> PickUpPlan:
    s, geometry = declared.settings, declared.geometry
    first = batch[0]
    loading = s["LoadingOffsets"]
    probes = [job.probe for job in batch]
    y = first.y - (first.probe - 1) * span / (PROBES - 1)
    # Biomek also lowers every probe without a tip that stands over an emptied spot of the rack; one
    # over a spot still holding a tip, or over none, stays up (as logged). Spots whose contents are
    # not tracked count as holding tips.
    tips = self.tips

    def over_an_empty_spot(n: int) -> bool:
      yn = y + (n - 1) * span / (PROBES - 1)
      return any(
        abs(sx - first.x) <= ALIGNMENT_TOLERANCE
        and abs(sy - yn) <= ALIGNMENT_TOLERANCE
        and spot.tracks_tips
        and not spot.has_tip()
        for spot, sx, sy in first.rack_spots
      )

    over = [n for n in range(1, PROBES + 1) if tips[n - 1] is None and over_an_empty_spot(n)]
    return PickUpPlan(
      x=first.x,
      y=y,
      span=span,
      pairs=tuple((job.probe, job.spot) for job in batch),
      # Biomek's safe height over the rack, in its world model, which places labware by pod 1's
      # corner of the position (the press uses this pod's: they differ by up to 0.008 cm here).
      approach=first.world_top + geometry.additional_safe_height + geometry.avoidance_margin,
      lowered=tuple(sorted(set(probes) | set(over))),
      seats={n: first.top + s["TipLoadZOffset"] + loading[n - 1] for n in probes},
      exit=first.top + EXIT_ABOVE_TOP,
      groups=tuple(press_groups(probes, s["MaxLoadingProbes"], s["MinLoadingInterval"])),
    )

  def plan_discard(self, probes: Sequence[int], trash: Optional[Resource] = None) -> DiscardPlan:
    """Where a discard of `probes` (1..8) goes and how they are shucked, as Biomek does it.

    Raises:
      ValueError: for a position that is not a trash (no `tip_disposal`).
    """
    deck, configuration, declared = self._context()
    position = trash if trash is not None else deck.get_position(DEFAULT_TRASH)
    disposal = getattr(position, "tip_disposal", None)
    if disposal is None:
      raise ValueError(f"{position.name} is not a trash position (no tip disposal offsets)")
    corner = targeting.target(
      deck, configuration, self._pod.pod_number, position, x="l", y="b", z="b"
    )
    calibrated = configuration.deck_positions.get(position.name)
    x_span = calibrated.x_span if calibrated else position.get_size_x() / 10
    y_span = calibrated.y_span if calibrated else position.get_size_y() / 10
    s = declared.settings
    span = declared.limits["Span"][0]
    overshoot = s["DiscardOvershoot"]
    return DiscardPlan(
      x=corner.x + x_span / 2 + disposal.span8_x,
      y=corner.y + (y_span - span) / 2 + disposal.span8_y,
      span=span,
      z=s["DiscardHeight"] - DISCARD_BELOW,
      groups=tuple(press_groups(probes, s["MaxUnloadingProbes"], s["MinUnloadingInterval"])),
      shuck={n: declared.limits[f"Z{n}"][1] + overshoot for n in probes},
    )

  async def _travel(self, x: float, y: float, span: float, height: Optional[float]) -> None:
    """Probe 1 to X/Y with the span (Biomek cm), clear of the deck; `height` in mm (lowest points)."""
    _, _, declared = self._context()
    await self._pod._move_over(
      {
        "X": declared.to_counts("X", x),
        "Y": declared.to_counts("Y", y),
        "S": declared.to_counts("S", span),
      },
      minimum_traverse_height=None if height is None else height / 10,
    )

  async def pick_up_tips(
    self,
    tip_spots: Sequence[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Pick up a tip from each spot, one channel per spot (STAR's `Pipettes` signature), as Biomek
    does: see the module's description. With `DetectTips` set, the tip sensor checks every probe
    afterwards.

    Args:
      tip_spots: where to pick up from, one per channel.
      use_channels: which channels, 0-indexed from the back (channel 0 is probe 1). The first
        `len(tip_spots)` when None.
      offsets: added to each spot's centre, in mm. None for none.
      minimum_traverse_height_start: how high the channels' lowest points (a tip's end, else the
        probe's) travel, mm above the deck. When None, the probes travel as high as they go.
      minimum_traverse_height_end: where the channels' lowest points are left, mm. When None, the
        probes are left as high as they go.

    Raises:
      HasTipError: if a channel already carries a tip.
      ValueError: for a channel out of range, or a height out of reach.
      TipCheckError: if the tip sensor disagrees afterwards.
    """
    if not tip_spots:
      return
    channels = list(range(len(tip_spots))) if use_channels is None else list(use_channels)
    tips = self.tips
    for channel in channels:
      if tips[self._probe(channel) - 1] is not None:
        raise HasTipError(f"{self}: channel {channel} already carries a tip")
    plans = self.plan_pick_up(tip_spots, channels, offsets)
    _, _, declared = self._context()
    pod = self._pod
    speed = percent_to_field(declared.settings["LoadSpeed"])

    def z(probes: Sequence[int], height: float) -> Dict[str, int]:
      return {f"Z{n}": declared.to_counts(f"Z{n}", height) for n in probes}

    for plan in plans:
      probes = [n for n, _ in plan.pairs]
      logger.info(
        "%s pick up tips on probes %s at X %.3f Y %.3f S %.3f",
        pod.component, probes, plan.x, plan.y, plan.span,
      )  # fmt: skip
      await self._travel(plan.x, plan.y, plan.span, minimum_traverse_height_start)
      await pod.move_axes(z(plan.lowered, plan.approach))
      previous: Tuple[int, ...] = ()
      for group in plan.groups:
        targets = z(previous, plan.exit)
        targets.update({f"Z{n}": declared.to_counts(f"Z{n}", plan.seats[n]) for n in group})
        await pod.move_axes(targets, speed)
        previous = group
      await pod.move_axes(z(previous, plan.exit), speed)
      picked: Dict[int, Optional[Tip]] = {}
      for probe, spot in plan.pairs:
        tip = spot.tip_for_pickup()
        if spot.tracks_tips:
          spot.unassign_tip()
        self._tips[probe] = tip
        picked[probe] = tip
      self._set_tips(picked)
    end = minimum_traverse_height_end
    await pod._lift_channels(None if end is None else end / 10)
    await self._check_tips(sorted(n for plan in plans for n, _ in plan.pairs))

  async def drop_tips(self, tip_spots: Sequence[TipSpot], *args: Any, **kwargs: Any) -> None:
    """Not possible on a Span-8: its stripper pushes tips off only at the top of the probes'
    travel, so tips cannot be put back into a rack; they go to waste with `discard_tips`.

    Raises:
      NotImplementedError: always.
    """
    raise NotImplementedError(
      f"{self}: a Span-8 cannot put tips back into a rack (its stripper works only at the top of "
      "the probes' travel); use discard_tips"
    )

  async def discard_tips(
    self,
    use_channels: Optional[List[int]] = None,
    *,
    trash: Optional[Resource] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Shuck the channels' tips into the trash (`TR2` by default), as Biomek does: see the module's
    description. With `DetectTips` set, the tip sensor checks every probe afterwards.

    Args:
      use_channels: which channels, 0-indexed from the back. Every channel carrying a tip when None.
      trash: the trash position.
      minimum_traverse_height_start: how high the channels' lowest points (the tips' ends) travel
        to the trash, mm above the deck. When None, the probes travel as high as they go.
      minimum_traverse_height_end: where the probes are left, mm. When None, as high as they go.

    Raises:
      RuntimeError: if no channel named carries a tip.
      TipCheckError: if the tip sensor disagrees afterwards.
    """
    tips = self.tips
    named = range(PROBES) if use_channels is None else use_channels
    probes = sorted(n for n in (self._probe(c) for c in named) if tips[n - 1] is not None)
    if not probes:
      raise RuntimeError(f"{self}: no channel named carries a tip")
    plan = self.plan_discard(probes, trash)
    _, _, declared = self._context()
    pod = self._pod
    logger.info("%s discard tips on probes %s", pod.component, probes)

    def z(group: Sequence[int], height: Mapping[int, float]) -> Dict[str, int]:
      return {f"Z{n}": declared.to_counts(f"Z{n}", height[n]) for n in group}

    below = {n: plan.z for n in probes}
    await self._travel(plan.x, plan.y, plan.span, minimum_traverse_height_start)
    await pod.move_axes(z(probes, below))
    speed = percent_to_field(declared.settings["DiscardSpeed"])
    for group in plan.groups:
      await pod.move_axes(z(group, plan.shuck), speed)
      await pod.move_axes(z(group, below), speed)
    for probe in probes:
      self._tips.pop(probe, None)
    self._set_tips({n: None for n in probes})
    end = minimum_traverse_height_end
    await pod._lift_channels(None if end is None else end / 10)
    await self._check_tips(probes)

  async def _check_tips(self, probes: Sequence[int]) -> None:
    """With `DetectTips` set, the tip sensor checks the probes worked against its record, as
    Biomek's `VT <probes> <expected>`."""
    declared = self._pod.declared
    if declared is not None and declared.settings["DetectTips"]:
      tips = self.tips
      await self.verify_tips({n - 1: tips[n - 1] is not None for n in probes})

  # -- aspirate and dispense ----------------------------------------------------------------------

  def _tip_of(self, probe: int) -> Tip:
    tip = self._carried_tip(probe)
    if tip is None:
      key = self.tips[probe - 1]
      raise RuntimeError(f"{self}: probe {probe}'s tip ({key or 'none'}) is not known")
    return tip

  def plan_liquid(
    self,
    operation: str,
    containers: Sequence[Container],
    volumes: Sequence[float],
    use_channels: Optional[Sequence[int]],
    settings: liquid.Settings,
    flow_rate: float,
  ) -> List[ProbePlan]:
    """The moves an aspirate (`operation` "aspirate") or dispense takes: one batch per set of
    containers the probes reach in one X/Y/S move (one X, evenly spaced in Y; every probe working
    one container goes in together, centred at the narrowest span), in channel order. Levels come
    from the containers' trackers when volume tracking is on.

    Raises:
      ValueError: for a channel out of range or given twice, a channel without a tip, or a height
        that cannot be worked out (from the liquid, or following, in a container without a
        height <-> volume function or with untracked contents).
    """
    deck, configuration, declared = self._context()
    channels = list(range(len(containers))) if use_channels is None else list(use_channels)
    if len(channels) != len(containers) or len(volumes) != len(containers):
      raise ValueError(f"{self}: one channel and one volume per container, please")
    if len(set(channels)) != len(channels):
      raise ValueError(f"{self}: a channel given twice")
    number = self._pod.pod_number
    low, high = declared.limits["Span"]
    tracking = does_volume_tracking()
    jobs = []
    for channel, container, volume in sorted(
      zip(channels, containers, volumes), key=lambda j: j[0]
    ):
      probe = self._probe(channel)
      if self.tips[probe - 1] is None:
        raise ValueError(f"{self}: channel {channel} carries no tip")
      at = targeting.target(deck, configuration, number, container)
      jobs.append((probe, container, float(volume), at.x, at.y))
    batches: List[Tuple[float, float, List[Tuple[int, Container, float]]]] = []
    while jobs:
      first = jobs.pop(0)
      batch = [first]
      shared = [j for j in jobs if j[1] is first[1]]
      if shared:  # one container for several probes: centred, at the narrowest span
        batch += shared
        for j in shared:
          jobs.remove(j)
        probes = [j[0] for j in batch]
        mid = (min(probes) + max(probes)) / 2
        span = low
        y1 = first[4] - (mid - 1) * span / (PROBES - 1)
        batches.append((first[3], y1, span, [(j[0], j[1], j[2]) for j in batch]))  # type: ignore[arg-type]
        continue
      span_: Optional[float] = None
      for job in list(jobs):
        if abs(job[3] - first[3]) > ALIGNMENT_TOLERANCE:
          continue
        if span_ is None:
          wanted = (job[4] - first[4]) * (PROBES - 1) / (job[0] - first[0])
          if not low - 1e-6 <= wanted <= high + 1e-6:
            continue
          span_ = wanted
        if (
          abs(first[4] + (job[0] - first[0]) * span_ / (PROBES - 1) - job[4]) <= ALIGNMENT_TOLERANCE
        ):
          batch.append(job)
          jobs.remove(job)
      span = low if span_ is None else span_
      y1 = first[4] - (first[0] - 1) * span / (PROBES - 1)
      batches.append((first[3], y1, span, [(j[0], j[1], j[2]) for j in batch]))  # type: ignore[arg-type]
    plans = []
    for x, y1, span, members in batches:  # type: ignore[misc]
      share: Dict[int, int] = collections.Counter(id(c) for _, c, _ in members)  # type: ignore[assignment]
      chans = []
      for probe, container, volume in members:
        shape = liquid.ContainerShape(container)
        tip = self._tip_of(probe)
        chans.append(
          liquid.Channel(
            name=f"Z{probe}",
            well=shape,
            bottom=targeting.target(deck, configuration, number, container, z="b").z
            + shape.floor / 10,
            tip_length=self._tip_length(tip),
            volume=volume,
            held=container.tracker.get_used_volume() if tracking else None,
            tip_radius=tip.get_size_x() / 20,  # its diameter, mm -> its radius, cm
            share=share[id(container)],
          )
        )
      steps = (liquid.plan_aspirate if operation == "aspirate" else liquid.plan_dispense)(
        chans, settings, flow_rate
      )
      plans.append(
        ProbePlan(x, y1, span, tuple(chans), tuple(steps), tuple((p, c) for p, c, _ in members))
      )
    return plans

  def moves(
    self, plan: ProbePlan, offset: Coordinate, d_start: Mapping[str, float]
  ) -> List[Tuple[Dict[str, float], int, float, bool]]:
    """One batch's moves after the travel: (axis targets in Biomek's units, Z1..Z8 / X / Y / S
    cm, D1..D8 uL; speed field; delay s after; whether its sensors' sensitivity is set first). The
    approach carries `offset` (mm) with X/Y/S, as the reservoir templates' first move does. The
    plungers start at `d_start` uL and stop at `SystemTrailingAirgap`, the air that keeps the
    system liquid from the sample.

    Raises:
      ValueError: for a dispense of more than a tip holds.
    """
    _, _, declared = self._context()
    floor = declared.settings["SystemTrailingAirgap"]
    x, y = offset_to_biomek(plan.x, plan.y, offset)
    d = {
      f"D{name[1:]}": d_start.get(f"D{name[1:]}", floor) for name in (c.name for c in plan.channels)
    }
    out: List[Tuple[Dict[str, float], int, float, bool]] = []
    first = True
    for step in plan.steps:
      if isinstance(step, liquid.ZMove):
        targets: Dict[str, float] = dict(step.z)
        if first and (offset.x or offset.y):
          targets.update(X=x, Y=y, S=plan.span)
        first = False
        out.append((targets, step.speed, 0.0, step.liquid))
      elif isinstance(step, liquid.PlungerMove):
        targets = {}
        for z_name, amount in step.volume.items():
          axis = f"D{z_name[1:]}"
          new = floor if step.empty else d[axis] + amount
          if new < floor - PLUNGER_TOLERANCE:
            raise ValueError(
              f"{self}: a dispense of {-amount:.3f} uL is more than probe {z_name[1:]} holds"
            )
          new = max(new, floor)
          if abs(new - d[axis]) > PLUNGER_TOLERANCE:
            targets[axis] = round(new, 6)
          d[axis] = new
        if not targets:
          continue  # nothing to move (a 0 uL operation): Biomek sends no move
        targets.update(step.z)
        out.append((targets, self._d_speed(step.flow_rate), step.delay, False))
      elif step.dx or step.dy:
        out.append(({"X": x + step.dx, "Y": y + step.dy, "S": plan.span}, step.speed, 0.0, False))
        out.append(({"X": x, "Y": y, "S": plan.span}, step.speed, step.delay, False))
    return out

  def _d_speed(self, flow_rate: float) -> int:
    """The `AM` speed field for the pumps at `flow_rate` uL/s: per mille of a pump's maximum."""
    return rate_to_field(self._pod, "D1", flow_rate, clamp=True)

  def _settings(self, **given: Any) -> liquid.Settings:
    """The template's settings: the arguments given, else this feature's `default_*`."""
    z_speed = given.pop("z_speed", None)
    rate = self.default_z_speed if z_speed is None else z_speed
    base = liquid.Settings(
      liquid_height=self.default_liquid_height,
      z_speed_field=units.rate_to_field(self._pod, "Z1", rate),
    )
    return liquid.settings(base, **given)

  async def _run_liquid(
    self,
    plans: List[ProbePlan],
    offset: Coordinate,
    cutoff_velocity: float,
    lls_sensitivity: int,
    minimum_traverse_height_start: Optional[float],
    minimum_traverse_height_end: Optional[float],
  ) -> None:
    _, _, declared = self._context()
    pod = self._pod
    pumps = [f"D{n}" for n in range(1, PROBES + 1)]
    gap = declared.settings["SystemTrailingAirgap"]
    d_start = {
      a: declared.to_value(a, pod.cpositions[a][1])
      for a in pumps
      if pod.cpositions[a][1] is not None
    }
    # The system trailing air gap keeps the system liquid from the sample: Biomek draws it into
    # every pump (at 20 %) when a method starts; a pump below it draws it before pipetting.
    short = [a for a in pumps if d_start.get(a, 0.0) < gap - PLUNGER_TOLERANCE]
    d_start.update({a: gap for a in short})
    batches = []
    for plan in plans:  # all checked before anything moves
      moves = self.moves(plan, offset, d_start)
      for targets, _, _, _ in moves:
        d_start.update({a: v for a, v in targets.items() if a.startswith("D")})
      batches.append((plan, moves))
    if short:
      if any(self._pod._valves.get(a) != "output" for a in pumps):
        await self.set_valves({a: "output" for a in pumps})
      await pod.move_axes({a: declared.to_counts(a, gap) for a in pumps}, AIR_GAP_SPEED)
    self._set_cutoff_velocity(cutoff_to_counts(self._pod, "D1", cutoff_velocity))
    for plan, moves in batches:
      await self._travel(plan.x, plan.y, plan.span, minimum_traverse_height_start)
      for targets, speed, delay, sense in moves:
        if sense:
          await self._set_lls_sensitivity(lls_sensitivity)
        if any(a.startswith("D") for a in targets) and any(
          self._pod._valves.get(a) != "output" for a in targets if a.startswith("D")
        ):
          await self.set_valves({a: "output" for a in pumps})
        await pod.move_axes({a: declared.to_counts(a, v) for a, v in targets.items()}, speed)
        if delay > 0:
          await asyncio.sleep(delay)
      # Biomek turns every valve to output after each operation (`VO 255`).
      await self.set_valves({a: "output" for a in pumps})
    end = minimum_traverse_height_end
    await pod._lift_channels(None if end is None else end / 10)

  def _book(self, plans: List[ProbePlan], extra: float, aspirate: bool) -> List[Any]:
    """Move each probe's volume (+ `extra`) between its container's and its tip's trackers
    (pending, to commit or roll back)."""
    if not does_volume_tracking():
      return []
    booked = []
    for plan in plans:
      for c, (probe, container) in zip(plan.channels, plan.pairs):
        tip = self._tips.get(probe)
        giver: Optional[VolumeTracker] = container.tracker
        taker: Optional[VolumeTracker] = tip.tracker if tip is not None else None
        if not aspirate:
          giver, taker = taker, giver
        amount = c.volume + extra
        if giver is not None:
          booked.append(giver)
          giver.remove_liquid(min(amount, giver.get_used_volume()))
        if taker is not None:
          booked.append(taker)
          taker.add_liquid(amount)
    return booked

  async def _require_pumps_homed(self, operation: str) -> None:
    """Refuse `operation` before anything moves when the pumps are not homed. Known once this
    session homes them; otherwise each pump is asked once, as the Master Controller asks them.

    Raises:
      NotHomedError: a pump is not homed; nothing is sent.
    """
    if self._pumps_homed is None:
      states = [await self._pod.request_homed_state(f"D{n}") for n in range(1, PROBES + 1)]
      self._pumps_homed = all(state == "homed" for state in states)
    if not self._pumps_homed:
      raise NotHomedError(
        0x32,
        None,
        f"{self._pod}: the pumps, before {operation}: prime the Span-8 (`prime`) or home its pumps "
        "(`home_pumps`) first",
      )

  async def _liquid(
    self,
    operation: str,
    containers: Sequence[Container],
    volumes: Sequence[float],
    use_channels: Optional[Sequence[int]],
    offsets: Optional[Sequence[Coordinate]],
    flow_rates: Optional[Sequence[Optional[float]]],
    settings: liquid.Settings,
    cutoff_velocity: Optional[float],
    lls_sensitivity: Optional[int],
    start: Optional[float],
    end: Optional[float],
  ) -> None:
    rates = {r for r in (flow_rates or []) if r is not None}
    if len(rates) > 1:
      raise ValueError(
        f"{self}: the pumps move together: one flow rate, please (got {sorted(rates)})"
      )
    offset_set = {(o.x, o.y, o.z) for o in (offsets or [])}
    if len(offset_set) > 1:
      raise ValueError(f"{self}: the probes move together in X: one offset, please")
    offset = (offsets or [Coordinate.zero()])[0]
    flow = rates.pop() if rates else self.default_flow_rate
    plans = self.plan_liquid(operation, containers, volumes, use_channels, settings, flow)
    aspirate = operation == "aspirate"
    if settings.mix is not None:
      chans = [(probe, c) for plan in plans for c, (probe, _) in zip(plan.channels, plan.pairs)]
      liquid.check_tips_empty_for_mix(
        self, [self._tips.get(probe) for probe, _ in chans],
        [0.0 if aspirate else c.volume for _, c in chans], settings,
      )  # fmt: skip
    await self._require_pumps_homed(operation)
    logger.info(
      "%s %s %s uL on probes %s", self._pod.component, operation, list(volumes),
      [p for plan in plans for p, _ in plan.pairs],
    )  # fmt: skip
    booked = self._book(plans, settings.conditioning.excess if aspirate else 0.0, aspirate)
    try:
      await self._run_liquid(
        plans, offset, cutoff_velocity or self.default_cutoff_velocity,
        self.default_lls_sensitivity if lls_sensitivity is None else lls_sensitivity, start, end,
      )  # fmt: skip
    except BaseException:
      for tracker in booked:
        tracker.rollback()
      raise
    for tracker in booked:
      tracker.commit()

  async def aspirate(
    self,
    containers: Sequence[Container],
    volumes: Sequence[float],
    use_channels: Optional[Sequence[int]] = None,
    resource_offsets: Optional[Sequence[Coordinate]] = None,
    liquid_heights: Optional[Sequence[Optional[float]]] = None,
    lld_mode: Optional[Any] = None,
    flow_rates: Optional[Sequence[Optional[float]]] = None,
    *,
    height_from: Optional[HeightFrom] = None,
    follow_liquid: Optional[bool] = None,
    minimum_height: Optional[float] = None,
    z_speed: Optional[float] = None,
    approach_height: Optional[float] = None,
    settling_times: Optional[Sequence[float]] = None,
    blow_out_air_volumes: Optional[Sequence[float]] = None,
    pre_mixes: Optional[Sequence[Optional[Mix]]] = None,
    mix_heights: Optional[MixHeights] = None,
    transport_air_volumes: Optional[Sequence[float]] = None,
    conditioning: Optional[Conditioning] = None,
    tip_touch: Optional[TipTouch] = None,
    cutoff_velocity: Optional[float] = None,
    lls_sensitivity: Optional[int] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Draw `volumes` (uL, one per channel) from `containers`, as Biomek's "S8 Pipetting" template
    does (see `driver/liquid.py`): one batch per set of containers the probes reach in one move
    (a shared container: the probes centred in it at the narrowest span). STAR's `Pipettes`
    signature; `techniques.aspirate_arguments(..., arm=pod)` gives the keywords a Biomek
    technique stands for (`liquid_height`, `flow_rate` as one value for every channel).

    Args:
      containers: one per channel.
      volumes: uL, one per channel.
      use_channels: 0-indexed from the back (channel 0 is probe 1); the first ones when None.
      resource_offsets: added to the targets, mm; one for every channel (the probes share X).
      liquid_heights: mm from `height_from`, one per channel or one for all.
      lld_mode: liquid-level sensing: None or OFF only, until it is built.
      flow_rates: uL/s; the pumps move together, so one rate for all.
      settling_times, blow_out_air_volumes, transport_air_volumes: STAR's per-channel lists, as
        `Head96.aspirate`'s `settling_time`, `blow_out_air_volume`, `transport_air_volume`; the
        template applies one value to every probe, so the values must agree.
      pre_mixes: STAR's per-channel `Mix`es, as `Head96.aspirate`'s `pre_mix`: one mix for every
        probe, or None for all.
      height_from, follow_liquid, minimum_height, z_speed, approach_height, conditioning,
        tip_touch, mix_heights: as `Head96.aspirate`.
      cutoff_velocity: the pumps' cutoff velocity, uL/s (Biomek's technique; `CV`).
      lls_sensitivity: the liquid-level sensors' sensitivity, set before each move measured from
        the liquid (Biomek's conductivity; `SS`).
      minimum_traverse_height_start / _end: as `pick_up_tips`.

    Raises:
      NotImplementedError: for liquid-level sensing.
      ValueError: for channels, containers or heights that cannot be worked out, or per-channel
        values that differ.
    """
    _no_lld(self, lld_mode)
    settings = self._settings(
      liquid_height=_one("liquid_heights", liquid_heights), height_from=height_from,
      follow_liquid=follow_liquid, minimum_height=minimum_height, z_speed=z_speed,
      approach_height=approach_height, settling_time=_one("settling_times", settling_times),
      blow_out_air_volume=_one("blow_out_air_volumes", blow_out_air_volumes),
      transport_air_volume=_one("transport_air_volumes", transport_air_volumes),
      conditioning=conditioning, tip_touch=tip_touch,
    )  # fmt: skip
    settings = liquid.with_mix(settings, _one_mix("pre_mixes", pre_mixes), mix_heights)
    await self._liquid(
      "aspirate", containers, volumes, use_channels, resource_offsets, flow_rates, settings,
      cutoff_velocity, lls_sensitivity, minimum_traverse_height_start, minimum_traverse_height_end,
    )  # fmt: skip

  async def dispense(
    self,
    containers: Sequence[Container],
    volumes: Sequence[float],
    use_channels: Optional[Sequence[int]] = None,
    resource_offsets: Optional[Sequence[Coordinate]] = None,
    liquid_heights: Optional[Sequence[Optional[float]]] = None,
    lld_mode: Optional[Any] = None,
    flow_rates: Optional[Sequence[Optional[float]]] = None,
    *,
    height_from: Optional[HeightFrom] = None,
    follow_liquid: Optional[bool] = None,
    minimum_height: Optional[float] = None,
    z_speed: Optional[float] = None,
    approach_height: Optional[float] = None,
    settling_times: Optional[Sequence[float]] = None,
    post_mixes: Optional[Sequence[Optional[Mix]]] = None,
    mix_heights: Optional[MixHeights] = None,
    blow_out_air_volumes: Optional[Sequence[float]] = None,
    blow_out_delay: Optional[float] = None,
    tip_touch: Optional[TipTouch] = None,
    empty: bool = False,
    cutoff_velocity: Optional[float] = None,
    lls_sensitivity: Optional[int] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Push `volumes` (uL, one per channel) into `containers`, as Biomek's "S8 Pipetting" template
    does; arguments as `aspirate` and `Head96.dispense` (`post_mixes` one `Mix` for every probe,
    as `pre_mixes`). The pumps stop at `SystemTrailingAirgap`.

    Raises:
      NotImplementedError: for liquid-level sensing.
      ValueError: for channels, containers or heights that cannot be worked out, more than a tip
        holds, or per-channel values that differ.
    """
    _no_lld(self, lld_mode)
    settings = self._settings(
      liquid_height=_one("liquid_heights", liquid_heights), height_from=height_from,
      follow_liquid=follow_liquid, minimum_height=minimum_height, z_speed=z_speed,
      approach_height=approach_height, settling_time=_one("settling_times", settling_times),
      blow_out_air_volume=_one("blow_out_air_volumes", blow_out_air_volumes),
      blow_out_delay=blow_out_delay, tip_touch=tip_touch, empty=empty,
    )  # fmt: skip
    settings = liquid.with_mix(settings, _one_mix("post_mixes", post_mixes), mix_heights)
    await self._liquid(
      "dispense", containers, volumes, use_channels, resource_offsets, flow_rates, settings,
      cutoff_velocity, lls_sensitivity, minimum_traverse_height_start, minimum_traverse_height_end,
    )  # fmt: skip


def _one(name: str, values: Optional[Sequence[Optional[float]]]) -> Optional[float]:
  """The one value a per-channel argument (STAR's lists) gives every channel: the template applies
  one value to all probes (one height measured the same way, one wait, one air volume)."""
  given = {v for v in (values or []) if v is not None}
  if len(given) > 1:
    raise ValueError(f"{name}: one value for all channels, please (got {sorted(given)})")
  return given.pop() if given else None


def _one_mix(name: str, mixes: Optional[Sequence[Optional[Mix]]]) -> Optional[Mix]:
  """The one mix a per-channel list (STAR's `pre_mixes` / `post_mixes`) gives every probe: the
  template mixes with all of them alike, so a mix on some channels only, or mixes that differ, are
  refused."""
  given = set(mixes or [])
  if len(given) > 1:
    raise ValueError(f"{name}: one mix for all channels, or None for all, please (got {given})")
  return given.pop() if given else None


def _no_lld(pipettes: Pipettes, lld_mode: Any) -> None:
  if lld_mode is not None and getattr(lld_mode, "name", lld_mode) != "OFF":
    raise NotImplementedError(f"{pipettes}: liquid-level sensing is not built yet")

"""Biomek's pipetting templates ("MC Pipetting", "S8 Pipetting" and their reservoir variants), as a
plan of moves both heads carry out.

Biomek runs an aspirate or dispense as a template: a list of steps whose parameters are bound to
the technique (`=C__Height`, ...). The templates are project data, the same for both heads, and
what follows reproduces every aspirate and dispense Biomek logged:

- aspirate: to 1 mm above the top (100 %; the reservoir variants carry their X offset in this move,
  the Span-8's goes to 10 mm); the leading air gap for a blowout; the mix; down to the height (at the
  technique's Z speed); aspirate the volume + the conditioning, the tip following the liquid down
  when asked (`AspirateDelay` after); dispense each conditioning section back (`DispenseDelay` after
  each); the tip touch; up to 1 mm above the top (Z speed); the trailing air gap;
- dispense: to the approach; down to the height; dispense, following the liquid up when asked (or,
  emptying, everything at the starting height); the mix; when the well's contents are known, up to
  2 mm above the liquid (Z speed); the blowout; the tip touch; up to 1 mm above the top (Z speed).

The mix (the template's Mix step, when asked): its count of cycles, each to the draw height, draw
the mix volume, to the push height, push it back, both at the mix's flow rate, with no waits; its
moves at the operation's Z speed, a move to where the tips already are not sent; while drawing and
pushing the tips follow the liquid when asked, as the operation's own draw does.

Heights: mm from the bottom, the top (+ the well's depth) or the liquid (+ its level,
refused when the contents are not known), then at least `minimum_height` mm above the bottom. Any
PyLabRobot container works, through PyLabRobot's interface only (`ContainerShape`): the bottom is its
cavity bottom, the level comes from the volume its tracker holds through its
`compute_height_from_volume` (needed only for heights from the liquid and for following, as Biomek
needs known amounts). Following moves the tip by the level's change. A tip touch goes out the given distance in direction theta (Biomek's angle
360 - theta degrees: 90 is -Y) and back; without a distance, to the container's top
outline less the tip's widest radius; one move for all the channels, measured at the last one, as
Biomek's logged touch-offs are. (Biomek's own distance, the wall at the touch height less its
tip's cone at the rim, is `labware.biomek_touch_distance`.)

This module plans; it moves nothing. Heights are Biomek's: cm above the deck, of the channel's
bottom (the tip end + the tip's length); volumes are uL in the tip.
"""

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7.techniques import Conditioning, MixHeights, TipTouch
from pylabrobot.beckman_coulter.biomek_i7.units import fraction_to_field
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources.container import Container
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.volume_tracker import does_volume_tracking
from pylabrobot.resources.well import CrossSectionType

APPROACH_ABOVE_TOP = 1.0  # mm: every template's approach (the Span-8 reservoir variant's is 10)
MIX_EMPTY_TOLERANCE = 1e-3  # uL a tip may hold and count as empty for a mix
EXIT_ABOVE_TOP = 1.0  # mm: every template leaves to here
ABOVE_LIQUID = 2.0  # mm: where a dispense rises to when the contents are known
FULL_SPEED = 1000


class ContainerShape:
  """What the templates need of a container, in Biomek's cm, from PyLabRobot's interface: the depth
  from its cavity bottom, the liquid's height for a volume, the wall's distance from its axis."""

  def __init__(self, container: Container):
    self.container = container
    self.floor = _material_z_thickness(container)  # mm from the resource's bottom to the cavity's
    self.depth = (container.get_size_z() - self.floor) / 10

  def height(self, volume: float) -> float:
    """The liquid's height above the cavity bottom, cm, for `volume` uL.

    Raises:
      ValueError: for a container without a height <-> volume function.
    """
    if not self.container.supports_compute_height_volume_functions():
      raise ValueError(
        f"{self.container.name} has no height <-> volume function: heights from the liquid and "
        "following need one"
      )
    return self.container.compute_height_from_volume(volume) / 10

  def wall_distance(self, z: float, angle: float) -> float:
    """How far, cm, the wall is from the container's axis `z` cm above its cavity bottom, in
    direction `angle` (radians, Biomek's X/Y). The cross-section's area there is the volume
    function's slope (dV/dh, as the Prep driver builds its segments), shaped like the top outline
    (round, or a box of the top's proportions); without a volume function, the top outline."""
    hx, hy = self.container.get_size_x() / 20, self.container.get_size_y() / 20
    circle = getattr(self.container, "cross_section_type", None) == CrossSectionType.CIRCLE
    area = self._area(z * 10)
    if area is not None:
      if circle:
        return math.sqrt(area / math.pi) / 10
      scale = math.sqrt(area / (4 * hx * hy * 100))  # mm2 over the top's area
      hx, hy = hx * scale, hy * scale
    elif circle:
      return min(hx, hy)
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    return min(hx / c if c > 1e-12 else math.inf, hy / s if s > 1e-12 else math.inf)

  def _area(self, z: float) -> Optional[float]:
    """The cross-section's area, mm2, `z` mm above the cavity bottom, from the volume function; None
    without one, or where it gives no area."""
    if not self.container.supports_compute_height_volume_functions():
      return None
    depth = self.depth * 10
    low, high = max(0.0, min(z, depth) - 0.05), min(depth, max(z, 0.0) + 0.05)
    if high <= low:
      return None
    try:
      area = (
        self.container.compute_volume_from_height(high)
        - self.container.compute_volume_from_height(low)
      ) / (high - low)
    except ValueError:
      return None
    return area if area > 0 else None


def _material_z_thickness(container: Container) -> float:
  try:
    return float(container.material_z_thickness)
  except NotImplementedError:
    return 0.0


@dataclass(frozen=True)
class Channel:
  """One channel's part of an operation: its container's shape, the Biomek cm of its cavity bottom for
  this channel, the tip (length below the channel, widest radius, cm), the uL to move and what the
  container holds (None: not known)."""

  name: str  # the channel's Z axis on a Span-8, the mandrel on the 96-head
  well: ContainerShape
  bottom: float
  tip_length: float
  volume: float
  held: Optional[float]
  tip_radius: float = 0.0
  share: int = 1  # channels drawing on the same well at once (a reservoir under the 96-head)


@dataclass(frozen=True)
class Settings:
  """An operation's arguments, in the template's units (mm, the speed field, uL/s, s): the
  features convert their own (`z_speed`, mm/s) into these at their entry (`units.py`)."""

  liquid_height: float = 0.0
  height_from: str = "bottom"
  follow_liquid: bool = False
  minimum_height: float = 0.0
  z_speed_field: int = 1000
  approach_height: float = APPROACH_ABOVE_TOP
  settling_time: float = 0.0
  blow_out_air_volume: float = 0.0
  blow_out_delay: float = 0.0
  transport_air_volume: float = 0.0
  conditioning: Conditioning = field(default_factory=Conditioning)
  tip_touch: Optional[TipTouch] = None
  empty: bool = False
  mix: Optional["MixSettings"] = None


@dataclass(frozen=True)
class MixSettings:
  """The template's Mix step: `repetitions` times, to the draw height (mm from its `from`), draw
  `volume` uL, to the push height, push it back, both at `flow_rate` uL/s; the tips follow the
  liquid while drawing and pushing when `follow`. Its moves go at the operation's Z speed."""

  volume: float
  repetitions: int
  flow_rate: float
  aspirate_height: float
  aspirate_height_from: str
  dispense_height: float
  dispense_height_from: str
  follow: bool = False


def settings(base: Settings, **given: object) -> Settings:
  """`base` with every argument given (not None) in its place."""
  return dataclasses.replace(base, **{k: v for k, v in given.items() if v is not None})  # type: ignore[arg-type]


def with_mix(s: Settings, mix: Optional[Mix], heights: Optional[MixHeights]) -> Settings:
  """`s` with PyLabRobot's `mix` as the template's Mix, at `heights` (the operation's own height
  when None). Its `auto_surface_following` is Biomek's following during the mix.

  Raises:
    ValueError: for a negative volume or repetition count (Biomek's "Mix count cannot be negative").
    NotImplementedError: for a `surface_following_distance`: Biomek follows by the level only.
  """
  if mix is None:
    return dataclasses.replace(s, mix=None)
  if mix.repetitions < 0:
    raise ValueError(f"mix count cannot be negative (got {mix.repetitions})")
  if mix.volume < 0:
    raise ValueError(f"a mix volume cannot be negative (got {mix.volume})")
  if mix.surface_following_distance:
    raise NotImplementedError(
      "a mix follows the liquid by what each draw moves it (auto_surface_following), not a "
      "surface_following_distance"
    )
  if heights is None:
    heights = MixHeights(s.liquid_height, s.height_from)  # type: ignore[arg-type]
  push = heights.aspirate_height if heights.dispense_height is None else heights.dispense_height
  push_from = heights.dispense_height_from or heights.aspirate_height_from
  return dataclasses.replace(
    s,
    mix=MixSettings(
      volume=float(mix.volume),
      repetitions=int(mix.repetitions),
      flow_rate=float(mix.flow_rate),
      aspirate_height=float(heights.aspirate_height),
      aspirate_height_from=heights.aspirate_height_from,
      dispense_height=float(push),
      dispense_height_from=push_from,
      follow=mix.auto_surface_following,
    ),
  )


def check_tips_empty_for_mix(
  owner: object, tips: Sequence[Optional[Tip]], leaving: Sequence[float], s: Settings
) -> None:
  """Biomek's rule for a mix: the tips hold no liquid when it starts (air only), checked from the
  tips' trackers when volume tracking is on, `leaving` uL of each tip going out first (the
  dispense).

  Raises:
    ValueError: for a tip still holding liquid ("The mix operation cannot begin until tips are
      empty").
  """
  if not does_volume_tracking() or s.empty:
    return
  for tip, out in zip(tips, leaving):
    if tip is None:
      continue
    left = tip.tracker.get_used_volume() - out
    if left > MIX_EMPTY_TOLERANCE:
      raise ValueError(
        f"{owner}: the mix cannot begin until the tips are empty; a tip would hold {left:.3f} uL"
      )


@dataclass(frozen=True)
class ZMove:
  """Channels' bottoms to these heights (cm). `liquid` when measured from the liquid (the Span-8
  sets its sensors' sensitivity first)."""

  z: Dict[str, float]
  speed: int
  liquid: bool = False


@dataclass(frozen=True)
class PlungerMove:
  """Each channel's plunger by this many uL (+ draws, - pushes), at `flow_rate` uL/s; the bottoms
  to `z` at the same time (following the liquid); then `delay` s."""

  volume: Dict[str, float]
  flow_rate: float
  z: Dict[str, float] = field(default_factory=dict)
  delay: float = 0.0
  empty: bool = False  # push out everything the tip holds


@dataclass(frozen=True)
class Touch:
  """Out by (dx, dy) cm in Biomek's frame and back, at speed field `speed`; then `delay` s. `above_bottom`:
  where the last channel's tip end is (a Span-8 batch's highest-numbered probe), cm over its cavity
  bottom: the touch-off is measured there."""

  dx: float
  dy: float
  speed: int
  delay: float = 0.0
  above_bottom: float = 0.0


Step = Union[ZMove, PlungerMove, Touch]


def level(c: Channel, held: Optional[float]) -> Optional[float]:
  """The liquid's height above the bottom, cm, for `held` uL in the well (None: not known)."""
  return None if held is None else c.well.height(held)


def height_above_bottom(
  c: Channel, height: float, height_from: str, held: Optional[float], minimum: float
) -> float:
  """A template height (mm from the bottom, top or liquid) as cm above the well's bottom, at least
  `minimum` mm.

  Raises:
    ValueError: for a height from the liquid in a well whose contents are not known (Biomek's
      "Cannot move relative to liquid level if liquid amounts are not defined").
  """
  if height_from == "bottom":
    z = height / 10
  elif height_from == "top":
    z = height / 10 + c.well.depth
  elif height_from == "liquid":
    surface = level(c, held)
    if surface is None:
      raise ValueError(
        f"{c.name}: cannot move relative to the liquid level; the well's contents are not known"
      )
    z = height / 10 + surface
  else:
    raise ValueError(f"height_from must be bottom, top or liquid, not {height_from!r}")
  return max(z, minimum / 10)


def _z(c: Channel, above_bottom: float) -> float:
  return c.bottom + above_bottom + c.tip_length


def touch_offset(c: Channel, above_bottom: float, touch: TipTouch) -> Tuple[float, float]:
  """(dx, dy), cm, of a touch-off with the tip end `above_bottom` cm over the bottom: its
  `distance`, else the container's top outline less the tip's widest radius, in the direction
  (360 - theta) degrees. (0, 0) where that leaves no room, as Biomek moves nowhere.
  """
  angle = math.radians(360.0 - touch.theta)
  if touch.distance is not None:
    r = touch.distance / 10
  else:
    r = c.well.wall_distance(above_bottom, angle) - c.tip_radius
  if r <= 0:
    return 0.0, 0.0
  return round(math.cos(angle) * r, 6), round(math.sin(angle) * r, 6)


def _touch(
  channels: Sequence[Channel], s: Settings, held: Dict[str, Optional[float]]
) -> List[Step]:
  t = s.tip_touch
  if t is None:
    return []
  # One move for every channel, measured at the last one (a Span-8 batch's highest-numbered probe),
  # as every logged Biomek touch-off is: where the channels stand at different heights, the others
  # reach further or less far than their own wells' room, as they do under Biomek.
  last = channels[-1]
  zs = {
    c.name: _z(c, height_above_bottom(c, t.height, t.height_from, held[c.name], s.minimum_height))
    for c in channels
  }
  above = zs[last.name] - last.bottom - last.tip_length
  dx, dy = touch_offset(last, above, t)
  return [
    ZMove(zs, fraction_to_field(t.speed_fraction), liquid=t.height_from == "liquid"),
    Touch(dx, dy, fraction_to_field(t.speed_fraction), t.delay, above),
  ]


def _top(c: Channel, mm: float) -> float:
  return _z(c, c.well.depth + mm / 10)


def _mix(
  channels: Sequence[Channel], s: Settings, held: Dict[str, Optional[float]], at: Dict[str, float]
) -> List[Step]:
  """The Mix step's moves, the channels' bottoms at `at` (cm) over wells holding `held` uL: each
  cycle to the draw height, draw, to the push height, push. A move to where the channels already
  are is not sent (Biomek sends none: logged mixes at the dispense height have no Z moves)."""
  m = s.mix
  if m is None:
    return []
  steps: List[Step] = []
  where, contents = dict(at), dict(held)
  for _ in range(m.repetitions):
    for sign, height, height_from in (
      (1, m.aspirate_height, m.aspirate_height_from),
      (-1, m.dispense_height, m.dispense_height_from),
    ):
      target = {
        c.name: _z(
          c, height_above_bottom(c, height, height_from, contents[c.name], s.minimum_height)
        )
        for c in channels
      }
      if any(abs(target[n] - where[n]) > 1e-9 for n in target):
        steps.append(ZMove(target, s.z_speed_field, height_from == "liquid"))
        where = target
      after = {
        c.name: None
        if contents[c.name] is None
        else max(contents[c.name] - sign * m.volume * c.share, 0.0)  # type: ignore[operator]
        for c in channels
      }
      follow: Dict[str, float] = {}
      if m.follow:
        for c in channels:
          before, now = level(c, contents[c.name]), level(c, after[c.name])
          if before is not None and now is not None:
            start = where[c.name] - c.bottom - c.tip_length
            follow[c.name] = _z(c, max(start + now - before, s.minimum_height / 10))
      steps.append(PlungerMove({c.name: sign * m.volume for c in channels}, m.flow_rate, z=follow))
      where = {**where, **follow}
      contents = after
  return steps


def plan_aspirate(channels: Sequence[Channel], s: Settings, flow_rate: float) -> List[Step]:
  """The template's moves for an aspirate.

  Raises:
    ValueError: for a height that cannot be worked out (see `height_above_bottom`).
  """
  held = {c.name: c.held for c in channels}
  approach = {c.name: _top(c, s.approach_height) for c in channels}
  steps: List[Step] = [ZMove(approach, FULL_SPEED)]
  if s.blow_out_air_volume > 0:
    steps.append(
      PlungerMove(
        {c.name: s.blow_out_air_volume for c in channels}, flow_rate, delay=s.settling_time
      )
    )
  steps += _mix(channels, s, held, approach)
  start = {
    c.name: height_above_bottom(c, s.liquid_height, s.height_from, held[c.name], s.minimum_height)
    for c in channels
  }
  steps.append(
    ZMove(
      {c.name: _z(c, start[c.name]) for c in channels}, s.z_speed_field, s.height_from == "liquid"
    )
  )
  drawn = {c.name: c.volume + s.conditioning.extra for c in channels}
  after = {
    c.name: None if held[c.name] is None else max(held[c.name] - drawn[c.name] * c.share, 0.0)  # type: ignore[operator]
    for c in channels
  }
  follow: Dict[str, float] = {}
  if s.follow_liquid:
    for c in channels:
      before, now = level(c, held[c.name]), level(c, after[c.name])
      if before is not None and now is not None:
        end = max(start[c.name] + now - before, s.minimum_height / 10)
        follow[c.name] = _z(c, end)
  steps.append(PlungerMove(drawn, flow_rate, z=follow, delay=s.settling_time))
  for _ in range(s.conditioning.sections):
    steps.append(
      PlungerMove(
        {c.name: -s.conditioning.section_volume for c in channels},
        s.conditioning.flow_rate or flow_rate,
        delay=s.conditioning.delay,
      )
    )
    for c in channels:
      if after[c.name] is not None:
        after[c.name] += s.conditioning.section_volume * c.share  # type: ignore[operator]
  steps += _touch(channels, s, after)
  steps.append(ZMove({c.name: _top(c, EXIT_ABOVE_TOP) for c in channels}, s.z_speed_field))
  if s.transport_air_volume > 0:
    steps.append(
      PlungerMove(
        {c.name: s.transport_air_volume for c in channels}, flow_rate, delay=s.settling_time
      )
    )
  return steps


def plan_dispense(channels: Sequence[Channel], s: Settings, flow_rate: float) -> List[Step]:
  """The template's moves for a dispense.

  Raises:
    ValueError: for a height that cannot be worked out (see `height_above_bottom`).
  """
  held = {c.name: c.held for c in channels}
  steps: List[Step] = [ZMove({c.name: _top(c, s.approach_height) for c in channels}, FULL_SPEED)]
  start = {
    c.name: height_above_bottom(c, s.liquid_height, s.height_from, held[c.name], s.minimum_height)
    for c in channels
  }
  steps.append(
    ZMove(
      {c.name: _z(c, start[c.name]) for c in channels}, s.z_speed_field, s.height_from == "liquid"
    )
  )
  after = {
    c.name: None if held[c.name] is None else held[c.name] + c.volume * c.share  # type: ignore[operator]
    for c in channels
  }
  follow: Dict[str, float] = {}
  if s.follow_liquid and not s.empty:
    for c in channels:
      before, now = level(c, held[c.name]), level(c, after[c.name])
      if before is not None and now is not None:
        follow[c.name] = _z(c, max(start[c.name] + now - before, s.minimum_height / 10))
  steps.append(
    PlungerMove(
      {c.name: -c.volume for c in channels},
      flow_rate,
      z=follow,
      delay=s.settling_time,
      empty=s.empty,
    )
  )
  at = {c.name: follow.get(c.name, _z(c, start[c.name])) for c in channels}
  steps += _mix(channels, s, after, at)
  if all(after[c.name] is not None for c in channels):
    steps.append(
      ZMove(
        {
          c.name: _z(c, height_above_bottom(c, ABOVE_LIQUID, "liquid", after[c.name], 0.0))
          for c in channels
        },
        s.z_speed_field,
        liquid=True,
      )
    )
  if s.blow_out_air_volume > 0 and not s.empty:
    steps.append(
      PlungerMove(
        {c.name: -s.blow_out_air_volume for c in channels}, flow_rate, delay=s.blow_out_delay
      )
    )
  steps += _touch(channels, s, after)
  steps.append(ZMove({c.name: _top(c, EXIT_ABOVE_TOP) for c in channels}, s.z_speed_field))
  return steps

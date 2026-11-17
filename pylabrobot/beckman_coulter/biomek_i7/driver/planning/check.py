"""The one check of a pod's move, as pure functions: its gripper (and what it holds) against its
own pipettor, and all its parts (and its body, against the obstacles with no top) against what it
must clear (`obstacles.Obstacles`), each as a sweep; and the start-in-collision escape.

Biomek models each part of a pod as a box in 3D and refuses a move whose sweep overlaps: its
gripper's boxes and its pipettor's (`own_collisions`), or any part and the deck's boxes, their
own obstacles and the instrument's structure (`deck_collisions`), overlaps up to
`DECK_TOLERANCE` allowed. Every move whose path would overlap is refused before anything is sent
(`Pod.move_axes`); the planner plans around them with the same check on a larger model (the
gripper padded below, `pod_model.planning_pad`; the other pod as a wall). The parts are the pod
model's (`pod_model`).
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.obstacles import Obstacles, bounds_of
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.pod_model import (
  Bounds,
  GripperState,
  Part,
  PipettorState,
  PodModel,
  PodState,
  Point,
  body_part,
  bundle_part,
  gripper_parts,
  moving_parts,
  on_quarter,
  pipettor_parts,
  planning_pad,
)


class PodCollisionError(ValueError):
  """A move would bring a pod's gripper (or what it holds) into its own pipettor. Raised before
  anything is sent."""


def _axes(outline: Sequence[Point]) -> List[Point]:
  n = len(outline)
  return [
    (outline[i][1] - outline[(i + 1) % n][1], outline[(i + 1) % n][0] - outline[i][0])
    for i in range(n)
  ]


def overlap(a: Part, b: Part) -> bool:
  """Whether two parts share any volume (touching is clear): Z ranges, then separating axes."""
  if a.z_high <= b.z_low or b.z_high <= a.z_low:
    return False
  # Their bounds first: apart there, apart (the bounds hold each outline).
  ax0, ax1, ay0, ay1 = a.extent
  bx0, bx1, by0, by1 = b.extent
  if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
    return False
  for ax, ay in _axes(a.outline) + _axes(b.outline):
    pa = [px * ax + py * ay for px, py in a.outline]
    pb = [px * ax + py * ay for px, py in b.outline]
    if max(pa) <= min(pb) or max(pb) <= min(pa):
      return False
  return True


def _own_pairs(
  gripper: Sequence[Part], pipettor: Sequence[Part], bundle: Optional[Part]
) -> List[Tuple[str, str]]:
  """The (gripper part, pipettor part) pairs that overlap; a Span-8's tubing bundle only against
  what the gripper holds, as Biomek pairs it."""
  found = [(a.name, b.name) for a in gripper for b in pipettor if overlap(a, b)]
  if bundle is not None:
    found += [
      (a.name, bundle.name) for a in gripper if a.name == "held labware" and overlap(a, bundle)
    ]
  return found


def own_collisions_at(
  config: PodConfiguration, state: PodState, pad: float = 0.0
) -> List[Tuple[str, str]]:
  """The (gripper part, pipettor part) pairs that overlap at `state` (`_own_pairs`); none without
  a gripper. `pad`: as `gripper_parts`."""
  if state.gripper is None:
    return []
  gp = gripper_parts(config, state.x, state.gripper, pad)
  pp = pipettor_parts(config, state.x, state.pipettor)
  return _own_pairs(gp, pp, bundle_part(config, state.x, state.pipettor))


# cm: the most any point moves between two checked poses. Every part is widened by
# `AvoidanceMargin` (0.2164 cm), so a contact cannot pass between two steps unseen.
SWEEP_STEP = 0.1


def own_collisions(
  config: PodConfiguration, start: PodState, end: PodState, pad: float = 0.0
) -> List[Tuple[str, str]]:
  """The (gripper part, pipettor part) pairs a move from `start` to `end` brings together, anywhere
  along it (every axis on the same profile, scaled to its distance), checked at steps no point
  covers more than `SWEEP_STEP` in; none on a pod without a gripper. The parts are widened by
  `AvoidanceMargin`, more than a step; a Span-8's tubing bundle, not widened, meets only what the
  gripper holds (`_own_pairs`). `pad`: as `gripper_parts`."""
  g0, g1, p0, p1 = start.gripper, end.gripper, start.pipettor, end.pipettor
  if g0 is None or g1 is None:
    return []
  x = (start.x, end.x)
  # A turn between quarter turns: at least Biomek's box over the turning corners' arcs.
  boxes = quarter_turn_parts(config, start, end, pad)
  if boxes:
    pipettor = [*pipettor_parts(config, start.x, p0), *pipettor_parts(config, end.x, p1)]
    arcs = _own_pairs(boxes, pipettor, None)
    for bundle in (bundle_part(config, start.x, p0), bundle_part(config, end.x, p1)):
      arcs += _own_pairs(boxes, [], bundle)
    if arcs:
      return list(dict.fromkeys(arcs))
  # X carries every part alike, so only the other axes change what overlaps.
  reach = config.gripper_model["Finger"]["PadsCenterToRotationCenter"] + 15.0
  travel = max(
    [abs(g1.gy - g0.gy), abs(g1.gz - g0.gz), abs(g1.gg - g0.gg)]
    + [reach * math.radians(abs(g1.gr - g0.gr))]
    + [abs(b - a) for a, b in zip(p0.ys, p1.ys)]
    + [abs(b - a) for a, b in zip(p0.zs, p1.zs)]
  )
  steps = max(1, math.ceil(travel / SWEEP_STEP))
  found: List[Tuple[str, str]] = []
  # X carries both alike: checked at the start's X throughout, the pipettor's parts built once
  # when only the gripper moves.
  fixed = pipettor_parts(config, x[0], p0) if p0 == p1 else None
  fixed_bundle = bundle_part(config, x[0], p0) if p0 == p1 else None
  for i in range(steps + 1):
    t = i / steps

    def lerp(a: float, b: float) -> float:
      return float(a + t * (b - a))

    gs = GripperState(
      lerp(g0.gy, g1.gy), lerp(g0.gz, g1.gz), lerp(g0.gr, g1.gr), lerp(g0.gg, g1.gg), g0.held
    )
    ps = PipettorState(
      tuple(lerp(a, b) for a, b in zip(p0.ys, p1.ys)),
      tuple(lerp(a, b) for a, b in zip(p0.zs, p1.zs)),
      p0.tips,
      p0.tip_block,
    )
    gp = gripper_parts(config, x[0], gs, pad)
    pp = fixed if fixed is not None else pipettor_parts(config, x[0], ps)
    bundle = fixed_bundle if fixed is not None else bundle_part(config, x[0], ps)
    for pair in _own_pairs(gp, pp, bundle):
      if pair not in found:
        found.append(pair)
    if found:
      return found
  return found


# -- a pod against the deck and the instrument --------------------------------------------------
#
# Biomek checks each move of a pod against its world: one box per deck position (its site, from its
# ALP's base up to the higher of its `MinSafeHeight` and what stands on it), the positions' own
# obstacles (a trash's walls), and the instrument's obstacles (widened by their buffers). The
# position an operation works in is left out of that operation's moves, whole. The heads' and the
# Span-8 probes' lower parts reach `AdditionalSafeHeight` + `AvoidanceMargin` below the tips' ends
# for this check, and overlaps up to `DECK_TOLERANCE` are allowed.

# cm: an overlap this deep or less is clear.
DECK_TOLERANCE = 0.005


class DeckCollisionError(PodCollisionError):
  """A move would bring a pod's part (or what its gripper holds) into the deck, what stands on it,
  or the instrument. Raised before anything is sent."""


def _world_overlap(a: Part, b: Part) -> bool:
  """`overlap`, an overlap of `DECK_TOLERANCE` or less counted as clear."""
  if a.z_high - DECK_TOLERANCE <= b.z_low or b.z_high - DECK_TOLERANCE <= a.z_low:
    return False
  for ax, ay in _axes(a.outline) + _axes(b.outline):
    norm = math.hypot(ax, ay) or 1.0
    pa = [(px * ax + py * ay) / norm for px, py in a.outline]
    pb = [(px * ax + py * ay) / norm for px, py in b.outline]
    if max(pa) - DECK_TOLERANCE <= min(pb) or max(pb) - DECK_TOLERANCE <= min(pa):
      return False
  return True


def _hull(points: Sequence[Point]) -> Tuple[Point, ...]:
  """The convex hull of `points`, counterclockwise."""
  pts = sorted(set(points))
  if len(pts) <= 2:
    return tuple(pts)

  def cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

  lower: List[Point] = []
  for p in pts:
    while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
      lower.pop()
    lower.append(p)
  upper: List[Point] = []
  for p in reversed(pts):
    while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
      upper.pop()
    upper.append(p)
  return tuple(lower[:-1] + upper[:-1])


def _near(statics: Sequence[Part], box: Tuple[float, ...], margin: float) -> List[Part]:
  """The statics whose bounds meet `box` widened by `margin`."""
  x0, x1, y0, y1, z0, z1 = box
  out = []
  for s in statics:
    sx0, sx1, sy0, sy1, sz0, sz1 = bounds_of([s])
    if sx1 >= x0 - margin and x1 + margin >= sx0 and sy1 >= y0 - margin and y1 + margin >= sy0:
      if sz1 >= z0 - margin and z1 + margin >= sz0:
        out.append(s)
  return out


def deck_collisions_at(
  config: PodConfiguration, state: PodState, obstacles: Obstacles, pad: float = 0.0
) -> List[Tuple[str, str]]:
  """The (pod part, obstacle) pairs that overlap at `state`. `pad`: as `gripper_parts`."""
  body = body_part(config, state.x, state.pipettor.ys[0])
  found = _overlaps([body], obstacles.ceilings)
  moving = moving_parts(config, state.x, state.gripper, state.pipettor, pad)
  return found + _overlaps(moving, obstacles.everything)


def _overlaps(
  moving: Sequence[Part], statics: Sequence[Tuple[Part, Bounds]]
) -> List[Tuple[str, str]]:
  """The (moving, static) pairs that overlap: bounds first, then `_world_overlap`."""
  t = DECK_TOLERANCE
  found = []
  for a in moving:
    ax0, ax1, ay0, ay1, az0, az1 = bounds_of([a])
    for b, (bx0, bx1, by0, by1, bz0, bz1) in statics:
      if ax1 - t <= bx0 or bx1 - t <= ax0 or ay1 - t <= by0 or by1 - t <= ay0:
        continue
      if az1 - t <= bz0 or bz1 - t <= az0:
        continue
      if _world_overlap(a, b):
        found.append((a.name, b.name))
  return found


def deck_collisions(
  config: PodConfiguration,
  start: PodState,
  end: PodState,
  obstacles: Obstacles,
  pad: float = 0.0,
) -> List[Tuple[str, str]]:
  """The (pod part, obstacle) pairs a move from `start` to `end` brings together, anywhere along it
  (every axis on the same profile).

  Without a gripper turn every part translates in a straight line, so each one's path is covered by
  the convex hull of its start and end outlines over the union of their heights (the exact path of
  a move in X and Y, an over-estimate when Z moves too). With a turn the move is checked at steps no point
  covers more than `SWEEP_STEP` in, against the obstacles near its path; a turn between quarter
  turns also by Biomek's box over its turning parts' arcs (`quarter_turn_parts`). `pad`: as
  `gripper_parts`."""
  world = obstacles
  x = (start.x, end.x)
  g0, g1, p0, p1 = start.gripper, end.gripper, start.pipettor, end.pipettor
  first = [body_part(config, x[0], p0.ys[0])] + moving_parts(config, x[0], g0, p0, pad)
  last = [body_part(config, x[1], p1.ys[0])] + moving_parts(config, x[1], g1, p1, pad)
  turning = g0 is not None and g1 is not None and (g0.gr != g1.gr or g0.held != g1.held)
  if not turning:
    found: List[Tuple[str, str]] = []
    for a, b in zip(first, last):
      swept = Part(
        a.name, _hull(a.outline + b.outline), min(a.z_low, b.z_low), max(a.z_high, b.z_high)
      )
      statics = world.ceilings if a.name == "pod body" else world.everything
      for pair in _overlaps([swept], statics):
        if pair not in found:
          found.append(pair)
    return found
  reach = config.gripper_model["Finger"]["PadsCenterToRotationCenter"] + 15.0
  near = Obstacles(
    tuple(_near(world.structure, bounds_of(first + last), reach)),
    tuple(_near(world.deck, bounds_of(first + last), reach)),
    world.holders,
  )
  assert g0 is not None and g1 is not None
  # A turn between quarter turns: at least Biomek's box over the turning corners' arcs.
  found = []
  for pair in _overlaps(quarter_turn_parts(config, start, end, pad), near.everything):
    if pair not in found:
      found.append(pair)
  if found:
    return found
  travel = [abs(x[1] - x[0]), reach * math.radians(abs(g1.gr - g0.gr))]
  travel += [abs(g1.gy - g0.gy), abs(g1.gz - g0.gz), abs(g1.gg - g0.gg)]
  travel += [abs(b - a) for a, b in zip(p0.ys, p1.ys)] + [abs(b - a) for a, b in zip(p0.zs, p1.zs)]
  steps = max(1, math.ceil(max(travel) / SWEEP_STEP))
  for i in range(steps + 1):
    t = i / steps

    def lerp(a: float, b: float) -> float:
      return float(a + t * (b - a))

    gs = GripperState(
      lerp(g0.gy, g1.gy), lerp(g0.gz, g1.gz), lerp(g0.gr, g1.gr), lerp(g0.gg, g1.gg), g0.held
    )
    ps = PipettorState(
      tuple(lerp(a, b) for a, b in zip(p0.ys, p1.ys)),
      tuple(lerp(a, b) for a, b in zip(p0.zs, p1.zs)),
      p0.tips,
      p0.tip_block,
    )
    for pair in deck_collisions_at(config, PodState(lerp(*x), gs, ps), near, pad):
      if pair not in found:
        found.append(pair)
    if found:
      return found
  return found


# The gripper's parts that turn with GR (the upper hand does not).
TURNING = ("lower hand", "fingers", "held labware")


def _arc_box(outline: Sequence[Point], cx: float, cy: float, delta: float) -> Tuple[float, ...]:
  """The axis-aligned box over `outline`'s corners turning by `delta` degrees about (`cx`, `cy`):
  each corner at both ends, and at its radius on each axis it crosses (Biomek's box for a turn
  between quarter turns)."""
  xs: List[float] = []
  ys: List[float] = []
  for px, py in outline:
    r = math.hypot(px - cx, py - cy)
    phi = math.degrees(math.atan2(py - cy, px - cx))
    lo, hi = sorted((phi, phi + delta))
    angles = [lo, hi] + [k * 90.0 for k in range(math.ceil(lo / 90.0), math.floor(hi / 90.0) + 1)]
    for a in angles:
      xs.append(cx + r * math.cos(math.radians(a)))
      ys.append(cy + r * math.sin(math.radians(a)))
  return min(xs), max(xs), min(ys), max(ys)


def quarter_turn_parts(
  config: PodConfiguration, start: PodState, end: PodState, pad: float = 0.0
) -> List[Part]:
  """For a gripper turn from one quarter turn to another, each turning part's box over its corners'
  arcs (`_arc_box`), about the rotation centre where the move starts and where it ends, the two
  joined; none for any other move. `pad`: as `gripper_parts`."""
  g0, g1 = start.gripper, end.gripper
  if g0 is None or g1 is None or g0.gr == g1.gr:
    return []
  if not (on_quarter(config, g0.gr) and on_quarter(config, g1.gr)):
    return []
  model = PodModel.of(config).gripper_model
  assert model is not None
  geo = config.geometry
  delta = g1.gr - g0.gr
  ends = []
  for x, g, turn in ((start.x, g0, delta), (end.x, g1, -delta)):
    cx = x + geo.gripper_x_offset - model.pads_center_to_rotation_center
    cy = g.gy + geo.gripper_y_offset - model.finger_center_offset
    parts = {p.name: p for p in gripper_parts(config, x, g, pad) if p.name in TURNING}
    ends.append({n: (_arc_box(p.outline, cx, cy, turn), p) for n, p in parts.items()})
  out = []
  for name in ends[0]:
    (a, pa), (b, pb) = ends[0][name], ends[1][name]
    corners = [(x, y) for box in (a, b) for x in box[:2] for y in box[2:]]
    out.append(Part(name, _hull(corners), min(pa.z_low, pb.z_low), max(pa.z_high, pb.z_high)))
  return out


@dataclass(frozen=True)
class Escape:
  """How a pod that stands inside deck positions leaves them, as Biomek's planner disengages: the
  positions it is inside (`holders`), and how far each pipettor channel's parts (a Span-8's
  probes one by one, the 96-head's as one) and the gripper's must rise straight up to clear them
  (cm; 0 for parts inside none)."""

  holders: Tuple[str, ...]
  pipettor: Tuple[float, ...]
  gripper: float


def _channel(part: str) -> int:
  """The pipettor channel a part rises with: a Span-8 probe's (`probe N`, its upper part too), else
  the 96-head's only one."""
  return int(part.split()[1]) - 1 if part.startswith("probe ") else 0


def escape(
  config: PodConfiguration, state: PodState, obstacles: Obstacles, pad: float = 0.0
) -> Optional[Escape]:
  """Biomek's start-in-collision escape for a pod at this pose: the deck positions its parts are
  inside, and each part's straight rise until its bottom reaches their tops (Biomek's upward stop,
  the obstacle's top less the part's bottom): per channel, so a Span-8's probes inside nothing do
  not rise (the logged escapes raise only the probes still in the labware). None when it is
  inside anything that is not a position's box (a position's own obstacle, the instrument): no
  escape is planned from those. `pad`: as `gripper_parts`."""
  x, gripper, pipettor, world = state.x, state.gripper, state.pipettor, obstacles
  if _overlaps([body_part(config, x, pipettor.ys[0])], world.ceilings):
    return None
  positions: List[str] = []
  channels = [0.0] * len(pipettor.zs)
  hand = 0.0
  parts: List[Tuple[Part, Optional[int]]] = [
    (a, _channel(a.name)) for a in moving_parts(config, x, None, pipettor)
  ]
  if gripper is not None:
    parts += [(a, None) for a in gripper_parts(config, x, gripper, pad)]
  for a, channel in parts:
    for b, bounds in world.everything:
      if not _overlaps([a], [(b, bounds)]):
        continue
      if b.name not in world.holders:
        return None
      if b.name not in positions:
        positions.append(b.name)
      if channel is None:
        hand = max(hand, b.z_high - a.z_low)
      else:
        channels[channel] = max(channels[channel], b.z_high - a.z_low)
  return Escape(tuple(positions), tuple(channels), hand)


def move_clear(
  config: PodConfiguration, start: PodState, end: PodState, obstacles: Optional[Obstacles]
) -> bool:
  """Whether a move from `start` to `end` passes both checks (the deck's only with `obstacles`) as
  Biomek's planner checks it: the gripper padded below (`planning_pad`)."""
  pad = planning_pad(config)
  if own_collisions(config, start, end, pad):
    return False
  return obstacles is None or not deck_collisions(config, start, end, obstacles, pad)

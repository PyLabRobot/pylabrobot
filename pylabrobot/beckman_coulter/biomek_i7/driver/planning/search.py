"""Biomek's search for a pod's path, when the direct way is not clear: an A* over the pod's
positions, each step one axis of one part to its next contact or its destination coordinate
(`contact_stop`), every step checked as the move check checks it. `pod_search` builds a pod's
neighbours from its pose (`pose_of`), how its axes plan (`planned`) and the check (`clear`), the
pod's own (`Pod`) passed in."""

import math
from typing import (
  Callable,
  Dict,
  Hashable,
  Iterable,
  List,
  Mapping,
  Optional,
  Sequence,
  Set,
  Tuple,
  TypeVar,
  cast,
)

from pylabrobot.beckman_coulter.biomek_i7.driver import motion
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.obstacles import Obstacles
from pylabrobot.beckman_coulter.biomek_i7.driver.planning import pod_model

# A move: axis targets in counts, sent together.
Move = Dict[str, int]

State = TypeVar("State", bound=Hashable)


class NoPathError(RuntimeError):
  """No path to the destination: the search found none, or reached its limit. Nothing was sent."""


def same_value(a: float, b: float) -> bool:
  """Whether two of Biomek's values are the same: equal within 1e-12 of the smaller magnitude,
  and never less than 1e-12."""
  return abs(a - b) <= max(min(abs(a), abs(b)) * 1e-12, 1e-12)


class OpenSet:
  """Biomek's open set: a binary heap on each node's cost estimate, the same costs (`same_value`) counting as
  equal. A node rises only past a strictly larger cost; one sinking takes the smaller child (the
  left on a tie) and sinks below it when that child's cost is not larger. A node reached again
  more cheaply has its cost lowered where it stands."""

  def __init__(self) -> None:
    self.heap: List[Hashable] = []
    self.cost: Dict[Hashable, float] = {}
    self.at: Dict[Hashable, int] = {}

  def __bool__(self) -> bool:
    return bool(self.heap)

  def _before(self, a: Hashable, b: Hashable) -> bool:
    """Whether `a` may stand above `b`: its cost the same or less."""
    return same_value(self.cost[a], self.cost[b]) or self.cost[a] < self.cost[b]

  def _swap(self, i: int, j: int) -> None:
    h = self.heap
    h[i], h[j] = h[j], h[i]
    self.at[h[i]], self.at[h[j]] = i, j

  def _rise(self, i: int) -> None:
    while i > 0:
      up = (i - 1) // 2
      if self._before(self.heap[up], self.heap[i]):
        return
      self._swap(i, up)
      i = up

  def push(self, node: Hashable, cost: float) -> None:
    """Add `node`, or lower its cost if it is open with a larger one."""
    if node in self.at:
      if cost < self.cost[node]:
        self.cost[node] = cost
        self._rise(self.at[node])
      return
    self.cost[node] = cost
    self.heap.append(node)
    self.at[node] = len(self.heap) - 1
    self._rise(len(self.heap) - 1)

  def pop(self) -> Hashable:
    """Take the node at the top."""
    h = self.heap
    top = h[0]
    last = h.pop()
    del self.at[top]
    if h:
      h[0] = last
      self.at[last] = 0
      i = 0
      while True:
        left, right = 2 * i + 1, 2 * i + 2
        if left >= len(h):
          break
        child = left
        if right < len(h) and not self._before(h[left], h[right]):
          child = right
        if not self._before(h[child], h[i]):
          break
        self._swap(i, child)
        i = child
    return top


def search(
  start: State,
  is_destination: Callable[[State], bool],
  neighbours: Callable[[State], Iterable[Tuple[State, Move, float]]],
  heuristic: Callable[[State], float],
  limit: int,
) -> List[Move]:
  """Biomek's A*: from `start`, the cheapest moves (as `neighbours` gives them: the next state,
  the move to it and its cost) to a state `is_destination` accepts, guided by `heuristic`, the
  open nodes in Biomek's heap (`OpenSet`); nodes once expanded are not reopened; at most `limit`
  expansions.

  Raises:
    NoPathError: when the open set runs out ("no path possible") or the limit is reached ("all
      possible paths exhausted or search limit reached").
  """
  g: Dict[State, float] = {start: 0.0}
  parent: Dict[State, Tuple[State, Move]] = {}
  open_set = OpenSet()
  open_set.push(start, heuristic(start))
  closed: Set[State] = set()
  expansions = 0
  while open_set:
    state = cast(State, open_set.pop())
    if is_destination(state):
      moves: List[Move] = []
      while state in parent:
        state, move = parent[state]
        moves.append(move)
      return moves[::-1]
    closed.add(state)
    expansions += 1
    if expansions > limit:
      raise NoPathError("all possible paths exhausted or search limit reached")
    for following, move, cost in neighbours(state):
      if following in closed:
        continue
      total = g[state] + cost
      if total < g.get(following, math.inf):
        g[following] = total
        parent[following] = (state, move)
        open_set.push(following, total + heuristic(following))
  raise NoPathError("no path possible")


def contact_stop(
  value: float,
  positive: bool,
  near: float,
  far: float,
  faces: Iterable[Tuple[float, float]],
  destination: Optional[float],
) -> Optional[float]:
  """Where a part moving along one axis from `value` stops, as Biomek's search steps: at the next
  obstacle contact (its leading edge, `far` if `positive` else `near`, touching an obstacle's near
  face; else its trailing edge clearing the obstacle's far face) or at the destination coordinate,
  whichever comes first; None with neither ahead. `faces`: each obstacle's (low, high) along the
  axis, of those the part meets across the axis."""
  best: Optional[float] = None

  def take(c: float) -> None:
    nonlocal best
    if best is None or (c < best if positive else c > best):
      best = c

  for low, high in faces:
    if positive:
      if low - far > EPSILON:
        take(value + (low - far))
      elif high - near > EPSILON:
        take(value + (high - near))
    else:
      if near - high > EPSILON:
        take(value - (near - high))
      elif far - low > EPSILON:
        take(value - (far - low))
  if destination is not None and (
    destination > value + EPSILON if positive else destination < value - EPSILON
  ):
    take(destination)
  return best


# Biomek's tolerance in its search (cm, degrees).
EPSILON = 1e-4


def pod_search(
  config: PodConfiguration,
  positions: Mapping[str, motion.CPos],
  targets: Mapping[str, int],
  obstacles: Optional[Obstacles],
  pose_of: Callable[[Mapping[str, motion.CPos]], Optional[pod_model.PodState]],
  planned: Callable[[Mapping[str, motion.CPos], Mapping[str, int]], Dict[str, motion.CPos]],
  clear: Callable[..., bool],
  channel_z_axes: Sequence[str],
  x_bounds: Optional[Tuple[float, float]] = None,
) -> List[Move]:
  """Biomek's search (`search`) from where the pod is to `targets` (counts), for a planned
  move whose direct way is not clear. Each step moves one axis of one part to its next contact
  or its destination coordinate (`contact_stop`): X carries both parts, Y moves the
  pipettor (only with GR on a multiple of 90 degrees), GY the gripper, GR turns the gripper a
  quarter turn (to the bracketing quarters off them, straight to an off-quarter destination
  within one). Channel Z axes and GZ in `targets` join the search: up to the part's next contact
  above (its bottom at an obstacle's top) or its target, down only near the destination (within
  twice the part's footprint of its X and Y) to the next top below or its target; without them
  the heights are kept (Biomek's travel stage). Every step is checked as `_clear` checks it,
  and its stops come from the same boxes; cost its length + 1 per moving part,
  + `RotationPenalty` per turn or per move on an axis the destination leaves free; at most
  `AStarSearchLimit` expansions; ties to the first found. A Span-8's span is set first, as
  Biomek sets it before planning. `x_bounds` (cm): where X may go, within its travel (a pod
  moving aside keeps to its side).

  Raises:
    NoPathError: as `search`.
  """
  d = config
  pad = pod_model.planning_pad(d)  # the planner's gripper, its contact stops too
  eps = EPSILON
  limit = int(d.settings["AStarSearchLimit"])
  penalty = float(d.settings["RotationPenalty"])
  first: List[Move] = []
  start: Dict[str, motion.CPos] = dict(positions)
  wanted = dict(targets)
  if "S" in wanted:
    span = {"S": wanted.pop("S")}
    if not clear([span], start):
      raise NoPathError("the span cannot be set where the pod is")
    first.append(span)
    start = planned(start, span)
  channels = list(channel_z_axes)
  heights = {a: wanted.pop(a) for a in [*channels, "GZ"] if a in wanted}  # counts
  goal = pose_of(planned(start, wanted))
  world = obstacles
  if goal is None or world is None:
    raise NoPathError("a position is not known")
  gx, gg, gp = goal
  dest = {
    "X": gx if "X" in wanted else None,
    "Y": gp.ys[0] if "Y" in wanted or "Y1" in wanted else None,
    "GY": gg.gy if gg is not None and "GY" in wanted else None,
    "GR": gg.gr if gg is not None and "GR" in wanted else None,
  }
  zdest = {a: d.to_value(a, c) for a, c in heights.items()}  # cm
  statics = [(*b[:4], b[4], b[5]) for _, b in world.everything]
  y_axes = [f"Y{i}" for i in range(1, 9)] if d.module_type == "span8" else ["Y"]
  keyed = ["X", *y_axes, "GY", "GR", *channels, "GZ"]
  states: Dict[Tuple[int, ...], Dict[str, motion.CPos]] = {}

  def key(positions: Mapping[str, motion.CPos]) -> Tuple[int, ...]:
    out = tuple(int(positions[a][1] or 0) for a in keyed if a in positions)
    states[out] = dict(positions)
    return out

  def values(positions: Mapping[str, motion.CPos]) -> Dict[str, float]:
    pose = pose_of(positions)
    assert pose is not None
    x, g, p = pose
    out = {"X": x, "Y": p.ys[0], **dict(zip(channels, p.zs))}
    if g is not None:
      out.update(GY=g.gy, GR=g.gr, GZ=g.gz)
    return out

  def is_destination(state: Tuple[int, ...]) -> bool:
    positions = states[state]
    v = values(positions)
    return all(
      abs(v[a] - want) <= (0.01 if a == "GR" else 0.001)
      for a, want in dest.items()
      if want is not None
    ) and all(positions[a][1] == c for a, c in heights.items())

  def heuristic(state: Tuple[int, ...]) -> float:
    v = values(states[state])
    dx = 0.0 if dest["X"] is None else v["X"] - dest["X"]
    dy = 0.0 if dest["Y"] is None else v["Y"] - dest["Y"]
    dz = max((abs(v[a] - z) for a, z in zdest.items() if a != "GZ"), default=0.0)
    out = math.sqrt(dx * dx + dy * dy + dz * dz)
    if "GY" in v:
      dgy = 0.0 if dest["GY"] is None else v["GY"] - dest["GY"]
      dgz = v["GZ"] - zdest["GZ"] if "GZ" in zdest else 0.0
      out += math.sqrt(dx * dx + dgy * dgy + dgz * dgz)
    return out

  def extent(parts: Sequence[pod_model.Part]) -> Tuple[float, float, float, float, float]:
    return (
      min(p.extent[0] for p in parts),
      max(p.extent[1] for p in parts),
      min(p.extent[2] for p in parts),
      max(p.extent[3] for p in parts),
      min(p.z_low for p in parts),
    )

  def boxes(others: Sequence[pod_model.Part]) -> Sequence[Tuple[float, ...]]:
    return [*statics, *((*o.extent, o.z_low, o.z_high) for o in others)]

  def faces(
    moving: Sequence[pod_model.Part], others: Sequence[pod_model.Part], along_x: bool
  ) -> Tuple[float, float, List[Tuple[float, float]]]:
    x0, x1, y0, y1, bottom = extent(moving)
    met = []
    for ox0, ox1, oy0, oy1, _, top in boxes(others):
      if not bottom + eps < top:
        continue
      lo, hi = (oy0, oy1) if along_x else (ox0, ox1)
      if lo - eps > (y1 if along_x else x1) or hi + eps < (y0 if along_x else x0):
        continue
      met.append((ox0, ox1) if along_x else (oy0, oy1))
    return (x0, x1, met) if along_x else (y0, y1, met)

  def vertical(
    moving: Sequence[pod_model.Part], others: Sequence[pod_model.Part], up: bool
  ) -> Optional[float]:
    """How far the part goes up before its bottom meets the nearest top above it, or down onto
    the nearest top below it, over the obstacles under or over its footprint; None with none
    that way (an obstacle open upwards is never cleared)."""
    x0, x1, y0, y1, bottom = extent(moving)
    best: Optional[float] = None
    for ox0, ox1, oy0, oy1, _, top in boxes(others):
      if ox0 - eps > x1 or ox1 + eps < x0 or oy0 - eps > y1 or oy1 + eps < y0:
        continue
      if up and bottom + eps < top < pod_model.OPEN:
        c = top - bottom
      elif not up and top + eps < bottom:
        c = bottom - top
      else:
        continue
      best = c if best is None else min(best, c)
    return best

  def near(parts: Sequence[pod_model.Part], v: Mapping[str, float], gripper: bool) -> bool:
    """Biomek's "near the destination": within twice the part's footprint of each X and Y the
    destination gives."""
    x0, x1, y0, y1, _ = extent(parts)
    checks = [("X", x1 - x0), ("GY" if gripper else "Y", y1 - y0)]
    return all(
      dest[a] is None or abs(v[a] - cast(float, dest[a])) - 2 * size <= eps for a, size in checks
    )

  def neighbours(state: Tuple[int, ...]) -> List[Tuple[Tuple[int, ...], Move, float]]:
    positions = states[state]
    pose = pose_of(positions)
    assert pose is not None
    x, g, p = pose
    v = values(positions)
    pipettor = pod_model.moving_parts(d, x, None, p)
    gripper = pod_model.gripper_parts(d, x, g, pad) if g is not None else []
    out: List[Tuple[Tuple[int, ...], Move, float]] = []

    def take(move: Dict[str, int], cost: float) -> None:
      # Through `_planned`: a Span-8's Y and S set its probes' Y1-Y8, which `positions` holds.
      try:
        after = planned(positions, move)
      except ValueError:
        return
      if all(after[a] == positions[a] for a in after):
        return
      if not clear([move], positions):
        return
      out.append((key(after), move, cost))

    def add(axis: str, value: float, cost: float) -> None:
      low, high = d.limits["GR" if axis == "GR" else axis]
      if axis == "X" and x_bounds is not None:
        low, high = max(low, x_bounds[0]), min(high, x_bounds[1])
      counts = d.to_counts(axis, min(max(value, low), high))
      if counts != d.to_counts(axis, v[axis]):
        take({axis: counts}, cost)

    def lift(
      axes: Sequence[str],
      parts: Sequence[pod_model.Part],
      others: Sequence[pod_model.Part],
      up: bool,
    ) -> None:
      """One part's Z-type `axes` together towards their targets, to its next contact."""
      going = [
        a for a in axes if a in zdest and (zdest[a] > v[a] + eps if up else zdest[a] < v[a] - eps)
      ]
      if not going:
        return
      stop = vertical(parts, others, up)
      move = {}
      for a in going:
        z = zdest[a]
        if stop is not None:
          z = min(z, v[a] + stop) if up else max(z, v[a] - stop)
        move[a] = d.to_counts(a, z)
      delta = max(abs(d.to_value(a, c) - v[a]) for a, c in move.items())
      take(move, delta + 1)

    def free(axis: str) -> float:
      return penalty if dest[axis] is None else 0.0

    if g is not None and not pod_model.on_quarter(d, g.gr):
      # Biomek's gripper off a quarter turn: only the turns to the two quarters either side.
      assert g is not None
      for turn in (math.floor(g.gr / 90) * 90.0, math.ceil(g.gr / 90) * 90.0):
        add("GR", turn, 1 + penalty)
      return out
    for positive in (True, False):
      near_x, far_x, met = faces([*pipettor, *gripper], [], True)
      stop = contact_stop(x, positive, near_x, far_x, met, dest["X"])
      if stop is not None:
        parts = 2 if gripper else 1
        add("X", stop, parts * (abs(stop - x) + 1) + free("X"))
    for positive in (True, False):
      near_y, far_y, met = faces(pipettor, gripper, False)
      stop = contact_stop(p.ys[0], positive, near_y, far_y, met, dest["Y"])
      if stop is not None:
        add("Y", stop, abs(stop - p.ys[0]) + 1 + free("Y"))
    lift(channels, pipettor, gripper, True)
    if near(pipettor, v, False):
      lift(channels, pipettor, gripper, False)
    if g is None:
      return out
    for positive in (True, False):
      near_gy, far_gy, met = faces(gripper, pipettor, False)
      stop = contact_stop(g.gy, positive, near_gy, far_gy, met, dest["GY"])
      if stop is not None:
        add("GY", stop, abs(stop - g.gy) + 1 + free("GY"))
    lift(["GZ"], gripper, pipettor, True)
    if near(gripper, v, True):
      lift(["GZ"], gripper, pipettor, False)
    quarter = round(g.gr / 90) * 90
    turns = [quarter - 90.0, quarter + 90.0]
    if dest["GR"] is not None and abs(dest["GR"] - g.gr) <= 90 + eps:
      turns.append(dest["GR"])
    for turn in turns:
      add("GR", turn, 1 + penalty)
    return out

  found = search(key(start), is_destination, neighbours, heuristic, limit)
  return first + found

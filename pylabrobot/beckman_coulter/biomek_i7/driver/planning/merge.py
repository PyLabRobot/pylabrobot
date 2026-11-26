"""Merging a pod's planned path, as Biomek merges one: its direct candidate (each axis on its own,
in Biomek's order), and the pipeline that turns a path of single-axis moves into the moves sent
(`merge_path`). Every reordering and every joined move is kept only when the path stays clear
(`Viable`: the pod's own checks, the ones `move_axes` makes before anything is sent).

Biomek merges in steps of each axis (deltas): a move is reordered or summed as a step, so a path
is taken apart into steps from where it starts, merged, and put back together as targets."""

from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence

# A move: axis targets in counts, sent together.
Move = Dict[str, int]

# Whether the moves `moves` (in order, from where the path starts) are each clear, as `Pod._clear`.
Viable = Callable[[Sequence[Move]], bool]

# A step: each axis's change in counts.
Step = Dict[str, int]

# The order Biomek's direct candidate changes the travel axes in, after the rises: the pipettor's
# X and Y, then the gripper's GY and GR. Axes it does not plan (the Span-8's span) go with the
# first move.
TRAVEL_ORDER = ("X", "Y", "GY", "GR")

# The two parts' vertical axes, and what each part moves horizontally (X carries both).
VERTICAL = ("Z", "GZ")
HORIZONTAL = {"Z": ("X", "Y"), "GZ": ("X", "GY")}


def direct_candidate(targets: Mapping[str, int]) -> List[Move]:
  """The travel to `targets` one axis at a time, in Biomek's order (`TRAVEL_ORDER`): each move
  changes one planned axis; axes it does not plan go with the first."""
  planned = [{a: targets[a]} for a in TRAVEL_ORDER if a in targets]
  rest = {a: v for a, v in targets.items() if a not in TRAVEL_ORDER}
  if rest:
    if planned:
      planned[0] = {**rest, **planned[0]}
    else:
      planned = [rest]
  return planned


def axis_of(name: str) -> Optional[str]:
  """The planned axis an axis belongs to: a Span-8's probes' Z (Z1-Z8) are its Z and their Y
  (Y1-Y8) its Y; the span (S) is set before planning and belongs to none."""
  if name == "S":
    return None
  if name[0] in "YZ" and name[1:].isdigit():
    return name[0]
  return name


def axes(step: Mapping[str, int]) -> FrozenSet[str]:
  """The planned axes a step or a move changes."""
  return frozenset(a for a in map(axis_of, step) if a is not None)


def merge_path(moves: Sequence[Move], start: Mapping[str, int], viable: Viable) -> List[Move]:
  """`moves` (from where the pod is: `start`, every axis's counts) merged as Biomek merges a planned
  path:

  1. its steps, one per move; neighbours on the same axis summed;
  2. a later step on the same axis pulled forward and summed into an earlier one (Z and GZ only
     the same way), the steps between after it;
  3. each rise (Z, then GZ) moved earlier, one step at a time;
  4. X paired with a later Y or GY, and Y or GY with a later X, unless the next step already pairs
     with it: the later one pulled forward next to it;
  5. two passes joining each move with the next while the joined move is clear: first moves that
     change none of Z, GZ or GR; then any, never a vertical move with a horizontal move of the same
     part (Z with X or Y, GZ with X or GY).

  Steps 2-4 keep a change only when the path stays clear."""
  steps = _sum_neighbours(_steps(moves, start))

  def clear(path: Sequence[Step]) -> bool:
    return viable(_moves(path, start))

  steps = _pull_forward(steps, clear, summed=True)
  for axis in VERTICAL:
    steps = _rises_earlier(steps, clear, axis)
  steps = _pull_forward(steps, clear, summed=False)
  steps = _join(steps, clear, _neither_vertical_nor_turning)
  return _moves(_join(steps, clear, _parts_apart), start)


def _steps(moves: Sequence[Move], start: Mapping[str, int]) -> List[Step]:
  at = dict(start)
  out = []
  for move in moves:
    out.append({a: v - at[a] for a, v in move.items()})
    at.update(move)
  return out


def _moves(steps: Sequence[Step], start: Mapping[str, int]) -> List[Move]:
  at = dict(start)
  out = []
  for step in steps:
    move = {a: at[a] + d for a, d in step.items()}
    at.update(move)
    out.append(move)
  return out


def _add(a: Step, b: Step) -> Step:
  return {k: a.get(k, 0) + b.get(k, 0) for k in {**a, **b}}


def _one_axis(step: Step) -> Optional[str]:
  only = axes(step)
  return next(iter(only)) if len(only) == 1 else None


def _rising(step: Step) -> bool:
  """Whether a step moves its axis up (counts grow with height on every Z-type axis)."""
  return any(d > 0 for d in step.values())


def _sum_neighbours(steps: Sequence[Step]) -> List[Step]:
  out: List[Step] = []
  for step in steps:
    if out and _one_axis(step) is not None and _one_axis(out[-1]) == _one_axis(step):
      out[-1] = _add(out[-1], step)
    else:
      out.append(dict(step))
  return out


def _pulls(summed: bool, current: Step, following: Step, later: Step) -> bool:
  """Whether step 2 (`summed`) or step 4 pulls `later` forward to `current`, `following` being the
  step right after `current`."""
  a, b, c = _one_axis(current), _one_axis(following), _one_axis(later)
  if a is None or c is None:
    return False
  if summed:
    return a == c and (a not in VERTICAL or _rising(current) == _rising(later))
  if a == "X":
    return b not in ("Y", "GY") and c in ("Y", "GY")
  return a in ("Y", "GY") and b != "X" and c == "X"


def _pull_forward(
  steps: Sequence[Step], clear: Callable[[Sequence[Step]], bool], summed: bool
) -> List[Step]:
  """Steps 2 and 4: for each step, the later steps it pulls forward (`_pulls`), until one that
  would leave the path not clear."""
  work = [dict(s) for s in steps]
  out: List[Step] = []
  i = 0
  while i < len(work):
    current = work[i]
    j = i + 2
    while j < len(work):
      if _pulls(summed, current, work[i + 1], work[j]):
        if not clear([*out, current, work[j], *work[i + 1 : j]]):
          break
        if summed:
          pulled = _add(current, work[j])
        else:
          out.append(current)
          pulled = work[j]
        work[i + 1 : j + 1] = [pulled, *work[i + 1 : j]]
        current = pulled
        i += 1
      j += 1
    out.append(current)
    i += 1
  return out


def _rises_earlier(
  steps: Sequence[Step], clear: Callable[[Sequence[Step]], bool], axis: str
) -> List[Step]:
  """Step 3: each rise on `axis` not right after another step on it, swapped earlier one step at a
  time while the path up to it stays clear."""
  work = [dict(s) for s in steps]
  for k in range(1, len(work)):
    if _one_axis(work[k]) != axis or not _rising(work[k]) or _one_axis(work[k - 1]) == axis:
      continue
    m = k
    while m > 0:
      swapped = [*work[: m - 1], work[m], work[m - 1]]
      if not clear(swapped):
        break
      work[m - 1], work[m] = work[m], work[m - 1]
      m -= 1
  return work


def _neither_vertical_nor_turning(a: Step, b: Step) -> bool:
  return not (axes(a) | axes(b)) & {"Z", "GZ", "GR"}


def _parts_apart(a: Step, b: Step) -> bool:
  joined = axes(a) | axes(b)
  return not any(v in joined and joined & set(HORIZONTAL[v]) for v in VERTICAL)


def _join(
  steps: Sequence[Step],
  clear: Callable[[Sequence[Step]], bool],
  allowed: Callable[[Step, Step], bool],
) -> List[Step]:
  """Step 5's pass: each move takes in the next while `allowed` and the joined move is clear."""
  out: List[Step] = []
  for step in steps:
    if out and allowed(out[-1], step):
      joined = _add(out[-1], step)
      if clear([*out[:-1], joined]):
        out[-1] = joined
        continue
    out.append(dict(step))
  return out

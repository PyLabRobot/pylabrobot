"""Plan the fewest X/Y moves that position each channel at its target.

Multi-channel heads share one X carriage, enforce minimum pairwise Y spacing, strictly
descending Y by channel index, and per-container geometry (including no-go zones).
Given a list of (channel, target container) assignments, this module groups them into batches
where each batch is a set of channels whose targets can all be reached in one X/Y move.
A Z-axis operation (e.g. LLD probe, aspirate, dispense, ...) is then supplied as a callback
by the caller.

This is formally a Minimum Exact Cover problem (equivalently, Set Partitioning in OR terminology,
or minimum hypergraph coloring in graph theory): pairwise constraints alone reduce to
graph coloring; container fit with no-go zones is k-ary, making it hypergraph coloring.
Hence the enumerate-then-partition pipeline rather than 2-ary graph coloring. Intended
for n <= ~16 channels; planning is O(2^n * n^2) in the worst case, with the branch-and-
bound partition solver typically fast on the structured instances this module sees.

    batches = plan_batches(
      use_channels=[0, 1, 2, 5, 6, 7],
      containers=[w0, w1, w2, w5, w6, w7],
      channel_spacings=backend._channels_minimum_y_spacing,
      wrt_resource=backend.deck,
      x_tolerance=0.1,
    )
    await backend.execute_batched(func=my_z_callback, batches=batches)
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Collection, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

from pylabrobot.liquid_handling.channel_positioning import (
  compute_nonconsecutive_channel_offsets,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

# --- Data types ---


@dataclass
class ChannelBatch:
  """A group of channels that can operate simultaneously.

  ``y_positions`` contains entries for active channels and any phantom channels
  between non-consecutive active members.
  """

  x_position: float
  indices: List[int]
  channels: List[int]
  y_positions: Dict[int, float] = field(default_factory=dict)  # includes phantoms


def log_batches(
  batches: List[ChannelBatch],
  use_channels: Optional[List[int]] = None,
  containers: Optional[List["Container"]] = None,
) -> None:
  """Log a tree view of the batch execution plan.

  Groups batches by X position and shows Y batches nested within each X group.
  Active channels are marked with ``*``, phantoms with a space.

  Args:
    batches: Output from ``plan_batches()``.
    use_channels: Channel indices (parallel with *containers*). If omitted,
      container names are not shown next to active channels.
    containers: Container objects (parallel with *use_channels*). If omitted,
      container names are not shown next to active channels.
  """

  ch_to_container = (
    dict(zip(use_channels, containers))
    if use_channels is not None and containers is not None
    else {}
  )

  x_groups: Dict[float, list] = {}
  for b in batches:
    x_key = round(b.x_position, 1)
    x_groups.setdefault(x_key, []).append(b)

  lines = ["plan:"]
  xg_keys = list(x_groups.keys())
  for xg_i, x_key in enumerate(xg_keys):
    xg_batches = x_groups[x_key]
    is_last_xg = xg_i == len(xg_keys) - 1
    xg_branch = "└" if is_last_xg else "├"
    xg_cont = " " if is_last_xg else "│"
    lines.append(f"  {xg_branch}── x-group {xg_i + 1} (x={x_key:.1f} mm)")
    for yb_i, b in enumerate(xg_batches):
      is_last_yb = yb_i == len(xg_batches) - 1
      yb_branch = "└" if is_last_yb else "├"
      yb_cont = " " if is_last_yb else "│"
      lines.append(f"  {xg_cont}   {yb_branch}── y-batch {yb_i + 1}")
      for ch in sorted(b.y_positions.keys()):
        is_last_ch = ch == max(b.y_positions.keys())
        ch_branch = "└" if is_last_ch else "├"
        active = "*" if ch in b.channels else " "
        container_name = f" ({ch_to_container[ch].name})" if ch in ch_to_container else ""
        lines.append(
          f"  {xg_cont}   {yb_cont}   {ch_branch}── {active}ch{ch}: y={b.y_positions[ch]:.1f} mm{container_name}"
        )
  logger.info("\n".join(lines))


# --- Spacing helpers ---


def _span_required(spacings: List[float], ch_lo: int, ch_hi: int) -> float:
  """Minimum total Y distance required between channels ch_lo and ch_hi.

  Sums the rounded pairwise spacing for each adjacent pair in the range,
  matching what the firmware enforces.
  """
  return sum(math.ceil(max(spacings[ch], spacings[ch + 1]) * 10) / 10 for ch in range(ch_lo, ch_hi))


# --- Batch partitioning ---


def _interpolate_phantoms(
  channels: List[int], y_positions: Dict[int, float], spacings: List[float]
) -> Dict[int, float]:
  """Return Y positions with phantom channels filled in between non-consecutive batch members.

  Each phantom is placed using the conservative max-spacing model (via ``_span_required``),
  so non-uniform spacings are respected (e.g. a wide channel only widens its own gaps).
  """
  result = dict(y_positions)
  sorted_chs = sorted(channels)
  for k in range(len(sorted_chs) - 1):
    ch_lo, ch_hi = sorted_chs[k], sorted_chs[k + 1]
    for phantom in range(ch_lo + 1, ch_hi):
      if phantom not in result:
        result[phantom] = result[ch_lo] - _span_required(spacings, ch_lo, phantom)
  return result


# --- Input validation and position computation ---


def validate_channel_selections(
  containers: List[Container],
  num_channels: int,
  use_channels: Optional[List[int]] = None,
) -> List[int]:
  """Validate and normalize channel selection.

  If *use_channels* is ``None``, defaults to ``[0, 1, ..., len(containers)-1]``.

  Returns:
    Validated list of channel indices.

  Raises:
    ValueError: If channels are empty, out of range, or if *containers*
      and *use_channels* have different lengths.
  """
  if use_channels is None:
    use_channels = list(range(len(containers)))
  if len(use_channels) == 0:
    raise ValueError("use_channels must not be empty.")
  if not all(0 <= ch < num_channels for ch in use_channels):
    raise ValueError(
      f"All use_channels must be integers in range [0, {num_channels - 1}], got {use_channels}."
    )
  if len(containers) != len(use_channels):
    raise ValueError(
      f"Length of containers and use_channels must match, "
      f"got {len(containers)} and {len(use_channels)}."
    )
  return use_channels


# --- Validity predicate & enumeration ---


def is_valid_batch(
  job_indices: Collection[int],
  use_channels: List[int],
  containers: List[Container],
  channel_spacings: List[float],
  wrt_resource: Resource,
  x_tolerance: float,
  resource_offsets: Optional[List[Coordinate]] = None,
) -> Optional[Dict[int, Coordinate]]:
  """Validate a candidate batch against all physical constraints, cheapest first.

  Checks, in order:
    1. Unique channels — O(k), prunes most candidates instantly.
    2. Same X — O(k), uses container centers (X isn't shifted by auto-spread).
    3. Container fit — per container, calls
       :func:`compute_nonconsecutive_channel_offsets` to see if the channels
       targeting it can coexist (respecting no-go zones). Skipped when
       *resource_offsets* is provided. This is the expensive check.
    4. Y spacing between adjacent channels — O(k) on resolved positions.

  Returns:
    Dict mapping each job index to its resolved absolute Coordinate if the
    batch is valid; ``None`` otherwise.
  """
  jobs = list(job_indices)
  if not jobs:
    return {}

  # 1. Unique channels.
  if len({use_channels[j] for j in jobs}) != len(jobs):
    return None

  centers = [containers[j].get_location_wrt(wrt_resource, x="c", y="c", z="b") for j in jobs]

  # 2. Same X (container center + any user X offset; auto-spread only moves Y).
  if len(jobs) > 1:
    xs = [
      centers[k].x + (resource_offsets[jobs[k]].x if resource_offsets is not None else 0.0)
      for k in range(len(jobs))
    ]
    if max(xs) - min(xs) > x_tolerance:
      return None

  # 3. Container fit → per-channel Y offset (skipped when user supplies offsets).
  y_offsets: Dict[int, float] = {j: 0.0 for j in jobs}
  if resource_offsets is None:
    by_container: Dict[int, List[int]] = defaultdict(list)
    for j in jobs:
      by_container[id(containers[j])].append(j)
    for cjobs in by_container.values():
      c = containers[cjobs[0]]
      if len(cjobs) == 1 and not getattr(c, "no_go_zones", ()):
        continue  # single channel, no no-go zones — center is fine.
      offsets = compute_nonconsecutive_channel_offsets(
        c,
        [use_channels[j] for j in cjobs],
        channel_spacings,
      )
      if offsets is None:
        return None
      for j, off in zip(cjobs, offsets):
        y_offsets[j] = off.y

  # Resolve absolute targets.
  targets: Dict[int, Coordinate] = {}
  for k, j in enumerate(jobs):
    ox = resource_offsets[j].x if resource_offsets is not None else 0.0
    oy = resource_offsets[j].y if resource_offsets is not None else y_offsets[j]
    targets[j] = Coordinate(centers[k].x + ox, centers[k].y + oy, 0.0)

  if len(jobs) == 1:
    return targets

  # 4. Y spacing between adjacent channels.
  jobs_sorted = sorted(jobs, key=lambda j: use_channels[j])
  for lo, hi in zip(jobs_sorted, jobs_sorted[1:]):
    required = _span_required(channel_spacings, use_channels[lo], use_channels[hi])
    if targets[lo].y - targets[hi].y < required - 1e-9:
      return None

  return targets


def _build_channel_batch(
  job_indices: Collection[int],
  resolved: Dict[int, Coordinate],
  use_channels: List[int],
  channel_spacings: List[float],
) -> ChannelBatch:
  """Assemble a :class:`ChannelBatch` from validated job indices and resolved positions."""
  indices = sorted(job_indices, key=lambda i: use_channels[i])
  channels = [use_channels[i] for i in indices]
  y_positions: Dict[int, float] = {use_channels[i]: resolved[i].y for i in indices}
  y_positions = _interpolate_phantoms(channels, y_positions, channel_spacings)
  x_position = sum(resolved[i].x for i in indices) / len(indices)
  return ChannelBatch(
    x_position=x_position,
    indices=indices,
    channels=channels,
    y_positions=y_positions,
  )


def enumerate_valid_batches(
  use_channels: List[int],
  containers: List[Container],
  channel_spacings: List[float],
  wrt_resource: Resource,
  x_tolerance: float,
  resource_offsets: Optional[List[Coordinate]] = None,
) -> List[ChannelBatch]:
  """Enumerate every valid batch by backtracking, returning ChannelBatch objects.

  Jobs are visited in channel-ascending order; an invalid candidate prunes its subtree.
  """
  n = len(use_channels)
  order = sorted(range(n), key=lambda i: use_channels[i])
  result: List[ChannelBatch] = []

  def backtrack(start: int, current: List[int]):
    if current:
      resolved = is_valid_batch(
        current,
        use_channels,
        containers,
        channel_spacings,
        wrt_resource,
        x_tolerance,
        resource_offsets,
      )
      if resolved is None:
        return
      result.append(_build_channel_batch(current, resolved, use_channels, channel_spacings))
    for pos in range(start, n):
      backtrack(pos + 1, current + [order[pos]])

  backtrack(0, [])
  return result


def minimum_exact_cover(
  n_jobs: int,
  batches: List[ChannelBatch],
) -> List[ChannelBatch]:
  """Pick the fewest batches that partition ``{0..n_jobs-1}`` (branch-and-bound).

  Returned list is a subset of *batches*. Assumes every job appears in at
  least one batch (caller's responsibility) so a partition always exists.
  """
  if n_jobs == 0:
    return []

  by_min: Dict[int, List[Tuple[FrozenSet[int], ChannelBatch]]] = defaultdict(list)
  for b in batches:
    js = frozenset(b.indices)
    by_min[min(js)].append((js, b))
  # Try larger batches first at each pivot — finds a tight bound sooner.
  for bucket in by_min.values():
    bucket.sort(key=lambda js_b: len(js_b[0]), reverse=True)

  all_jobs = frozenset(range(n_jobs))
  best: List[List[ChannelBatch]] = [batches[: n_jobs + 1]]  # sentinel: unreachable length

  def recurse(remaining: FrozenSet[int], current: List[ChannelBatch]):
    if not remaining:
      if len(current) < len(best[0]):
        best[0] = list(current)
      return
    if len(current) + 1 >= len(best[0]):
      return
    pivot = min(remaining)
    for js, b in by_min[pivot]:
      if js.issubset(remaining):
        current.append(b)
        recurse(remaining - js, current)
        current.pop()

  recurse(all_jobs, [])
  return best[0]


def plan_batches(
  use_channels: List[int],
  containers: List[Container],
  channel_spacings: List[float],
  wrt_resource: Resource,
  x_tolerance: float,
  resource_offsets: Optional[List[Coordinate]] = None,
) -> List[ChannelBatch]:
  """Container-aware, optimal batch planning (respects no-go zones).

  Decides the per-container spread *per candidate batch* by asking
  :func:`compute_nonconsecutive_channel_offsets` whether the channels
  targeting each container physically coexist in it given its geometry and
  no-go zones. Pairs that behavior with minimum exact-cover partition
  (:func:`minimum_exact_cover`) so the returned plan uses the fewest batches.

  Args:
    use_channels: Channel indices being used (parallel to *containers*).
    containers: Target Container objects, one per channel.
    channel_spacings: Per-channel minimum Y spacing (mm).
    wrt_resource: Reference resource for computing absolute positions.
    x_tolerance: Positions within this tolerance share a batch (pairwise).
    resource_offsets: Optional explicit XYZ offsets from container centers.
      When provided, auto-spread and no-go-zone checks are skipped (user is
      authoritative).

  Returns:
    Flat list of ChannelBatch sorted by ascending X position.
  """
  if x_tolerance <= 0:
    raise ValueError(f"x_tolerance must be > 0, got {x_tolerance}.")
  if len(use_channels) != len(containers):
    raise ValueError(
      f"use_channels and containers must have the same length, "
      f"got {len(use_channels)} and {len(containers)}."
    )
  if len(use_channels) == 0:
    raise ValueError("use_channels must not be empty.")
  if resource_offsets is not None and len(resource_offsets) != len(use_channels):
    raise ValueError(
      f"resource_offsets length must match use_channels, "
      f"got {len(resource_offsets)} and {len(use_channels)}."
    )
  max_ch = max(use_channels)
  if len(channel_spacings) < max_ch + 1:
    raise ValueError(
      f"channel_spacings list must have at least {max_ch + 1} entries "
      f"(max channel index is {max_ch}), got {len(channel_spacings)}."
    )

  valid = enumerate_valid_batches(
    use_channels,
    containers,
    channel_spacings,
    wrt_resource,
    x_tolerance,
    resource_offsets,
  )

  # Every job must appear in at least one valid batch, else no partition exists.
  covered = set().union(*(b.indices for b in valid)) if valid else set()
  missing = set(range(len(use_channels))) - covered
  if missing:
    raise ValueError(
      f"No valid batch contains job(s) {sorted(missing)} "
      f"(channel(s) {[use_channels[j] for j in sorted(missing)]}); "
      f"container(s) cannot accommodate them."
    )

  partition = minimum_exact_cover(len(use_channels), valid)
  partition.sort(key=lambda b: b.x_position)
  return partition

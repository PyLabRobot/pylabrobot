"""Pipette orchestration: resolve container positions and partition into executable batches.

Multi-channel liquid handlers have physical constraints (single X carriage, minimum
Y spacing, descending Y order by channel index) that limit which channels can act
simultaneously.

    targets = resolve_container_targets(containers, use_channels, channel_spacings, wrt_resource)
    batches = plan_batches(use_channels, targets, channel_spacings, x_tolerance=0.1)
    await backend.execute_batched(func=my_z_callback, batches=batches)
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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


def print_batches(
  batches: List[ChannelBatch],
  use_channels: Optional[List[int]] = None,
  containers: Optional[List["Container"]] = None,
  label: str = "plan",
) -> None:
  """Print a tree view of the batch execution plan.

  Groups batches by X position and shows Y batches nested within each X group.
  Active channels are marked with ``*``, phantoms with a space.

  Args:
    batches: Output from ``plan_batches()``.
    use_channels: Channel indices (parallel with *containers*). If omitted,
      container names are not shown next to active channels.
    containers: Container objects (parallel with *use_channels*). If omitted,
      container names are not shown next to active channels.
    label: Header label for the tree.
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

  print(f"{label}:")
  xg_keys = list(x_groups.keys())
  for xg_i, x_key in enumerate(xg_keys):
    xg_batches = x_groups[x_key]
    is_last_xg = xg_i == len(xg_keys) - 1
    xg_branch = "└" if is_last_xg else "├"
    xg_cont = " " if is_last_xg else "│"
    print(f"  {xg_branch}── x-group {xg_i + 1} (x={x_key:.1f} mm)")
    for yb_i, b in enumerate(xg_batches):
      is_last_yb = yb_i == len(xg_batches) - 1
      yb_branch = "└" if is_last_yb else "├"
      yb_cont = " " if is_last_yb else "│"
      print(f"  {xg_cont}   {yb_branch}── y-batch {yb_i + 1}")
      for ch in sorted(b.y_positions.keys()):
        is_last_ch = ch == max(b.y_positions.keys())
        ch_branch = "└" if is_last_ch else "├"
        active = "*" if ch in b.channels else " "
        container_name = f" ({ch_to_container[ch].name})" if ch in ch_to_container else ""
        print(
          f"  {xg_cont}   {yb_cont}   {ch_branch}── {active}ch{ch}: y={b.y_positions[ch]:.1f} mm{container_name}"
        )


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


def resolve_container_targets(
  containers: List[Container],
  use_channels: List[int],
  channel_spacings: List[float],
  wrt_resource: Resource,
  resource_offsets: Optional[List[Coordinate]] = None,
) -> List[Coordinate]:
  """Convert containers to absolute Coordinates, auto-spreading when needed.

  When *resource_offsets* is ``None`` and multiple channels target the same
  container, computes spread offsets via ``compute_nonconsecutive_channel_offsets``
  so channels can be batched in parallel. If the container is too narrow to
  spread, channels stay at center and will be serialized by ``plan_batches``.

  When *resource_offsets* is provided, uses those offsets directly (no
  auto-spreading).

  Args:
    containers: Container objects, one per entry in *use_channels*.
    use_channels: Channel indices being used.
    channel_spacings: Minimum Y spacing per channel (mm), one entry per
      channel on the instrument.
    wrt_resource: Reference resource for computing positions. All containers
      must be descendants of this resource.
    resource_offsets: Optional XYZ offsets from container centers.

  Returns:
    List of Coordinates (parallel to *use_channels* / *containers*) with
    absolute X/Y positions ready for ``plan_batches``.
  """
  if resource_offsets is not None:
    if len(resource_offsets) != len(containers):
      raise ValueError(
        f"resource_offsets length must match containers, "
        f"got {len(resource_offsets)} and {len(containers)}."
      )
    offsets = resource_offsets
  else:
    offsets = [Coordinate.zero()] * len(containers)

  x_pos: List[float] = []
  y_pos: List[float] = []
  for container, offset in zip(containers, offsets):
    loc = container.get_location_wrt(wrt_resource, x="c", y="c", z="b")
    x_pos.append(loc.x + offset.x)
    y_pos.append(loc.y + offset.y)

  # Auto-spread: when multiple channels target the same container and no
  # explicit offsets were given, compute spread offsets so they can be batched.
  if resource_offsets is None:
    container_groups: Dict[int, List[int]] = defaultdict(list)
    for idx in range(len(containers)):
      container_groups[id(containers[idx])].append(idx)
    for c_indices in container_groups.values():
      group_channels = [use_channels[i] for i in c_indices]
      spread = compute_nonconsecutive_channel_offsets(
        container=containers[c_indices[0]],
        use_channels=group_channels,
        channel_spacings=channel_spacings,
      )
      if spread is not None:
        for i, idx_val in enumerate(c_indices):
          y_pos[idx_val] += spread[i].y

  return [Coordinate(x, y, 0) for x, y in zip(x_pos, y_pos)]


# --- Public API ---


def plan_batches(
  use_channels: List[int],
  targets: List[Coordinate],
  channel_spacings: List[float],
  x_tolerance: float,
) -> List[ChannelBatch]:
  """Partition channel–target pairs into executable batches.

  Groups by X position (within *x_tolerance*), then within each X group partitions
  into Y sub-batches respecting per-channel minimum spacing. Computes phantom channel
  positions for intermediate channels between non-consecutive batch members.

  Use ``resolve_container_targets`` to convert Container objects to Coordinates
  before calling this function.

  Args:
    use_channels: Channel indices being used (e.g. [0, 1, 2, 5, 6, 7]).
    targets: Coordinate objects with absolute X/Y positions. One per entry
      in *use_channels*.
    channel_spacings: Minimum Y spacing per channel (mm), one entry per
      channel on the instrument.
    x_tolerance: Positions within this tolerance share an X group.

  Returns:
    Flat list of ChannelBatch sorted by ascending X position.
  """

  if x_tolerance <= 0:
    raise ValueError(f"x_tolerance must be > 0, got {x_tolerance}.")
  if len(use_channels) != len(targets):
    raise ValueError(
      f"use_channels and targets must have the same length, "
      f"got {len(use_channels)} and {len(targets)}."
    )
  if len(use_channels) == 0:
    raise ValueError("use_channels must not be empty.")
  max_ch = max(use_channels)
  if len(channel_spacings) < max_ch + 1:
    raise ValueError(
      f"channel_spacings list must have at least {max_ch + 1} entries "
      f"(max channel index is {max_ch}), got {len(channel_spacings)}."
    )

  x_pos = [c.x for c in targets]
  y_pos = [c.y for c in targets]
  spacings = list(channel_spacings)

  # Group indices by X position. Sorts by X then merges adjacent positions
  # within tolerance into the same group, so positions like 99.99 and 100.01
  # (0.02mm apart) are never split across groups.
  sorted_by_x = sorted(range(len(x_pos)), key=lambda i: x_pos[i])
  x_groups: Dict[float, List[int]] = {}
  current_key: Optional[float] = None
  for i in sorted_by_x:
    if current_key is None or abs(x_pos[i] - current_key) > x_tolerance:
      current_key = x_pos[i]
    x_groups.setdefault(current_key, []).append(i)

  # Within each X group, batch by Y position (greedy first-fit)
  result: List[ChannelBatch] = []
  for _, x_group_indices in sorted(x_groups.items()):
    group_x = x_pos[x_group_indices[0]]
    channels_by_index = sorted(x_group_indices, key=lambda i: use_channels[i])
    y_batches: List[List[int]] = []

    for idx in channels_by_index:
      channel = use_channels[idx]
      y = y_pos[idx]

      for batch in y_batches:
        if channel in [use_channels[i] for i in batch]:
          continue
        hi_ch = use_channels[batch[-1]]
        hi_y = y_pos[batch[-1]]
        if hi_y - y >= _span_required(spacings, hi_ch, channel) - 1e-9:
          batch.append(idx)
          break
      else:
        y_batches.append([idx])

    for batch in y_batches:
      batch_channels = [use_channels[i] for i in batch]
      y_positions: Dict[int, float] = {use_channels[i]: y_pos[i] for i in batch}
      y_positions = _interpolate_phantoms(batch_channels, y_positions, spacings)
      result.append(
        ChannelBatch(
          x_position=group_x,
          indices=batch,
          channels=batch_channels,
          y_positions=y_positions,
        )
      )

  return result

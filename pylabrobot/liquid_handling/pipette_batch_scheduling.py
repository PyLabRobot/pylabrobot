"""Pipette orchestration: partition channel–target pairs into executable batches.

Multi-channel liquid handlers have physical constraints (single X carriage, minimum
Y spacing, descending Y order by channel index) that limit which channels can act
simultaneously.

    batches = plan_batches(
        use_channels, targets=containers,
        channel_spacings=[9.0]*8, x_tolerance=0.1,
        wrt_resource=deck,
    )
    await backend.execute_batched(func=my_z_callback, batches=batches)
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union, cast

from pylabrobot.liquid_handling.channel_positioning import (
  MIN_SPACING_EDGE,
  compute_channel_offsets,
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
  use_channels: List[int],
  containers: List["Container"],
  label: str = "plan",
) -> None:
  """Print a tree view of the batch execution plan.

  Groups batches by X position and shows Y batches nested within each X group.
  Active channels are marked with ``*``, phantoms with a space.

  Args:
    batches: Output from ``plan_batches()``.
    use_channels: Channel indices (parallel with *containers*).
    containers: Container objects (parallel with *use_channels*).
    label: Header label for the tree.
  """

  ch_to_container = dict(zip(use_channels, containers))

  x_groups: Dict[float, list] = {}
  for b in batches:
    x_key = round(b.x_position, 1)
    x_groups.setdefault(x_key, []).append(b)

  print(f"{label}:")
  xg_keys = list(x_groups.keys())
  for xg_i, x_key in enumerate(xg_keys):
    xg_batches = x_groups[x_key]
    is_last_xg = xg_i == len(xg_keys) - 1
    xg_branch = "\u2514" if is_last_xg else "\u251c"
    xg_cont = " " if is_last_xg else "\u2502"
    print(f"  {xg_branch}\u2500\u2500 x-group {xg_i + 1} (x={x_key:.1f} mm)")
    for yb_i, b in enumerate(xg_batches):
      is_last_yb = yb_i == len(xg_batches) - 1
      yb_branch = "\u2514" if is_last_yb else "\u251c"
      yb_cont = " " if is_last_yb else "\u2502"
      print(f"  {xg_cont}   {yb_branch}\u2500\u2500 y-batch {yb_i + 1}")
      for ch in sorted(b.y_positions.keys()):
        is_last_ch = ch == max(b.y_positions.keys())
        ch_branch = "\u2514" if is_last_ch else "\u251c"
        active = "*" if ch in b.channels else " "
        container_name = f" ({ch_to_container[ch].name})" if ch in ch_to_container else ""
        print(
          f"  {xg_cont}   {yb_cont}   {ch_branch}\u2500\u2500 {active}ch{ch}: y={b.y_positions[ch]:.1f} mm{container_name}"
        )


# --- Spacing helpers ---


def _effective_spacing(spacings: List[float], ch_lo: int, ch_hi: int) -> float:
  """Max of per-channel spacings across ch_lo..ch_hi (inclusive).

  Used by ``compute_single_container_offsets`` to determine a single uniform spacing
  for spreading channels across a wide container.
  """
  return max(spacings[ch_lo : ch_hi + 1])


def _span_required(spacings: List[float], ch_lo: int, ch_hi: int) -> float:
  """Minimum total Y distance required between channels ch_lo and ch_hi.

  Sums the rounded pairwise spacing for each adjacent pair in the range via
  ``_min_spacing_between``, matching what the firmware enforces.
  """
  return sum(_min_spacing_between(spacings, ch, ch + 1) for ch in range(ch_lo, ch_hi))


def _min_spacing_between(spacings: List[float], i: int, j: int) -> float:
  """Minimum Y spacing between adjacent channels *i* and *j*.

  Takes the larger of the two channels' spacings, then rounds up to 0.1 mm:
  ``math.ceil(max(spacings[i], spacings[j]) * 10) / 10``.

  Mirrors ``STARBackend._min_spacing_between`` (which operates on
  ``self._channels_minimum_y_spacing`` instead of an explicit list).
  """
  return math.ceil(max(spacings[i], spacings[j]) * 10) / 10


# --- Batch partitioning ---


@dataclass
class _BatchAccumulator:
  """Mutable working state for a batch being built up during partitioning."""

  indices: List[int]
  lo_ch: int
  hi_ch: int
  lo_y: float
  hi_y: float


def _channel_fits_batch(
  batch: _BatchAccumulator, channel: int, y: float, spacings: List[float]
) -> bool:
  """Check whether *channel* at *y* can be added to *batch* without violating spacing.

  Two checks suffice because channels are processed in ascending order, so the candidate
  is always the new high end. The (lo → candidate) check covers the full span; the
  (hi → candidate) check catches the local gap.
  """
  if batch.hi_y - y < _span_required(spacings, batch.hi_ch, channel) - 1e-9:
    return False
  if batch.lo_y - y < _span_required(spacings, batch.lo_ch, channel) - 1e-9:
    return False
  return True


def _interpolate_phantoms(
  channels: List[int], y_positions: Dict[int, float], spacings: List[float]
) -> None:
  """Fill in Y positions for phantom channels between non-consecutive batch members.

  Each phantom is placed at its actual pairwise spacing from the previous channel,
  so non-uniform spacings are respected (e.g. a wide channel only widens its own gaps).
  """
  sorted_chs = sorted(channels)
  for k in range(len(sorted_chs) - 1):
    ch_lo, ch_hi = sorted_chs[k], sorted_chs[k + 1]
    cumulative = 0.0
    for phantom in range(ch_lo + 1, ch_hi):
      cumulative += _min_spacing_between(spacings, phantom - 1, phantom)
      if phantom not in y_positions:
        y_positions[phantom] = y_positions[ch_lo] - cumulative


def _partition_into_y_batches(
  indices: List[int],
  use_channels: List[int],
  y_pos: List[float],
  spacings: List[float],
  x_position: float,
) -> List[ChannelBatch]:
  """Partition channels within an X group into minimum parallel-compatible batches.

  Uses greedy first-fit: processes channels in ascending order and assigns each to
  the first batch where it fits, or creates a new batch.
  """

  channels_by_index = sorted(indices, key=lambda i: use_channels[i])
  batches: List[_BatchAccumulator] = []

  for idx in channels_by_index:
    channel = use_channels[idx]
    y = y_pos[idx]

    assigned = False
    for batch in batches:
      if _channel_fits_batch(batch, channel, y, spacings):
        batch.indices.append(idx)
        batch.hi_ch = channel
        batch.hi_y = y
        assigned = True
        break

    if not assigned:
      batches.append(_BatchAccumulator(indices=[idx], lo_ch=channel, hi_ch=channel, lo_y=y, hi_y=y))

  result: List[ChannelBatch] = []
  for batch in batches:
    batch_channels = [use_channels[i] for i in batch.indices]
    y_positions: Dict[int, float] = {use_channels[i]: y_pos[i] for i in batch.indices}
    _interpolate_phantoms(batch_channels, y_positions, spacings)
    result.append(
      ChannelBatch(
        x_position=x_position,
        indices=batch.indices,
        channels=batch_channels,
        y_positions=y_positions,
      )
    )

  return result


# --- Input validation and position computation ---


# Shift applied to odd channel spans to avoid center divider walls in troughs.
# Will be replaced by per-container no_go_zones (see docs/proposals/container_no_go_zones.md).
_ODD_SPAN_CENTER_AVOIDANCE = 5.5  # mm


def _offsets_for_consecutive_group(
  container: Container,
  use_channels: List[int],
  spacing: float,
) -> Optional[List[Coordinate]]:
  """Compute spread offsets for a group of channels whose full physical span fits the container."""
  ch_lo, ch_hi = min(use_channels), max(use_channels)
  num_physical = ch_hi - ch_lo + 1
  min_required = MIN_SPACING_EDGE * 2 + (num_physical - 1) * spacing

  if container.get_absolute_size_y() < min_required:
    return None

  all_offsets = compute_channel_offsets(
    resource=container,
    num_channels=num_physical,
    spread="wide",
    channel_spacings=[spacing] * num_physical,
  )
  offsets = [all_offsets[ch - ch_lo] for ch in use_channels]

  # Shift odd channel spans to avoid center divider walls, but only if the
  # outermost channel (center + half-spacing) stays within the container.
  if num_physical > 1 and num_physical % 2 != 0:
    max_offset_y = max(o.y for o in offsets)
    container_half_y = container.get_absolute_size_y() / 2
    if max_offset_y + _ODD_SPAN_CENTER_AVOIDANCE + spacing / 2 <= container_half_y:
      offsets = [o + Coordinate(0, _ODD_SPAN_CENTER_AVOIDANCE, 0) for o in offsets]

  return offsets


def compute_single_container_offsets(
  container: Container,
  use_channels: List[int],
  channel_spacings: Union[float, List[float]],
) -> Optional[List[Coordinate]]:
  """Compute spread Y offsets for multiple channels targeting the same container.

  Accounts for the full physical span including phantom intermediate channels.
  When the full span doesn't fit, splits active channels into consecutive
  sub-groups at gaps in the channel sequence and computes offsets per sub-group.
  Each sub-group gets centered spread offsets, so plan_batches will naturally
  batch sub-groups that can't coexist into separate Y batches.

  Returns None if even a single pair of adjacent active channels can't fit.
  """

  if len(use_channels) == 0:
    return []
  if len(use_channels) == 1:
    return [Coordinate.zero()]

  ch_lo, ch_hi = min(use_channels), max(use_channels)
  if isinstance(channel_spacings, (int, float)):
    spacing = float(channel_spacings)
  else:
    spacing = _effective_spacing(channel_spacings, ch_lo, ch_hi)

  # Try the full span first (all channels including phantoms fit)
  full = _offsets_for_consecutive_group(container, use_channels, spacing)
  if full is not None:
    return full

  # Full span doesn't fit. Split at gaps in the sorted channel sequence
  # into consecutive sub-groups and compute offsets for each independently.
  sorted_chs = sorted(use_channels)
  groups: List[List[int]] = [[sorted_chs[0]]]
  for i in range(1, len(sorted_chs)):
    if sorted_chs[i] == sorted_chs[i - 1] + 1:
      groups[-1].append(sorted_chs[i])
    else:
      groups.append([sorted_chs[i]])

  # If there's only one consecutive group and it didn't fit above, container is too small
  if len(groups) == 1:
    return None

  # Compute offsets per sub-group
  ch_to_offset: Dict[int, Coordinate] = {}
  for group in groups:
    group_offsets = _offsets_for_consecutive_group(container, group, spacing)
    if group_offsets is None:
      return None  # even a sub-group doesn't fit
    for ch, offset in zip(group, group_offsets):
      ch_to_offset[ch] = offset

  # Return in the original use_channels order
  return [ch_to_offset[ch] for ch in use_channels]


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
    ValueError: If channels are empty or out of range.
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


def compute_positions(
  containers: List[Container],
  resource_offsets: List[Coordinate],
  wrt_resource: Resource,
) -> Tuple[List[float], List[float]]:
  """Convert containers and offsets into absolute X/Y positions relative to a resource.

  Each container must be a descendant of *wrt_resource* (checked by
  ``Resource.get_location_wrt``, which raises ``ValueError`` if not).

  Returns:
    (x_positions, y_positions) — parallel lists of coordinates in mm,
    one entry per container.
  """
  x_pos: List[float] = []
  y_pos: List[float] = []
  for resource, offset in zip(containers, resource_offsets):
    loc = resource.get_location_wrt(wrt_resource, x="c", y="c", z="b")
    x_pos.append(loc.x + offset.x)
    y_pos.append(loc.y + offset.y)
  return x_pos, y_pos


# --- Public API ---


def plan_batches(
  use_channels: List[int],
  targets: Union[List[Container], List[Coordinate]],
  channel_spacings: Union[float, List[float]],
  x_tolerance: float,
  wrt_resource: Optional[Resource] = None,
  resource_offsets: Optional[List[Coordinate]] = None,
) -> List[ChannelBatch]:
  """Partition channel–target pairs into executable batches.

  Targets can be either:

  - **Containers** (with *wrt_resource*): computes X/Y positions from containers relative
    to a reference resource. When multiple channels target the same container and *resource_offsets*
    is not provided, automatically spreads them using ``compute_single_container_offsets``.
    Channels that cannot be spread (container too narrow) stay at the container center
    and are serialized into separate batches.

  - **Coordinates**: uses absolute X/Y positions directly. No auto-spreading.

  Groups by X position (within *x_tolerance*), then within each X group partitions
  into Y sub-batches respecting per-channel minimum spacing. Computes phantom channel
  positions for intermediate channels between non-consecutive batch members.

  Args:
    use_channels: Channel indices being used (e.g. [0, 1, 2, 5, 6, 7]).
    targets: Either Container objects (requires *wrt_resource*) or Coordinate objects
      with absolute X/Y positions. One per entry in *use_channels*.
    channel_spacings: Minimum Y spacing per channel (mm). Scalar for uniform,
      or a list with one entry per channel on the instrument.
    x_tolerance: Positions within this tolerance share an X group.
    wrt_resource: Reference resource for computing positions. All containers must
      be descendants of this resource. Required when *targets* are Containers.
    resource_offsets: Optional XYZ offsets from container centers. When provided,
      auto-spreading is disabled and these offsets are used directly. Only valid
      when *targets* are Containers.

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

  if wrt_resource is not None:
    containers = cast(List[Container], targets)
    if resource_offsets is not None:
      if len(resource_offsets) != len(containers):
        raise ValueError(
          f"resource_offsets length must match containers, "
          f"got {len(resource_offsets)} and {len(containers)}."
        )
      offsets = resource_offsets
    else:
      offsets = [Coordinate.zero()] * len(containers)
    x_pos, y_pos = compute_positions(containers, offsets, wrt_resource)
  else:
    containers = None
    coordinates = cast(List[Coordinate], targets)
    x_pos = [c.x for c in coordinates]
    y_pos = [c.y for c in coordinates]

  # Normalize scalar spacing to per-channel list.
  max_ch = max(use_channels)
  if isinstance(channel_spacings, (int, float)):
    spacings: List[float] = [float(channel_spacings)] * (max_ch + 1)
  else:
    spacings = list(channel_spacings)
    if len(spacings) < max_ch + 1:
      spacings.extend([spacings[-1]] * (max_ch + 1 - len(spacings)))

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

  # When multiple channels target the same container, offset their Y positions
  # so they can be batched together
  adjusted_y = list(y_pos)
  if containers is not None and resource_offsets is None:
    for indices in x_groups.values():
      container_groups: Dict[int, List[int]] = defaultdict(list)
      for idx in indices:
        container_groups[id(containers[idx])].append(idx)
      for c_indices in container_groups.values():
        if len(c_indices) < 2:
          continue
        group_channels = [use_channels[i] for i in c_indices]
        spread = compute_single_container_offsets(
          container=containers[c_indices[0]],
          use_channels=group_channels,
          channel_spacings=channel_spacings,
        )
        if spread is not None:
          for i, idx_val in enumerate(c_indices):
            adjusted_y[idx_val] += spread[i].y

  result: List[ChannelBatch] = []
  for _, indices in sorted(x_groups.items()):
    group_x = x_pos[indices[0]]
    result.extend(_partition_into_y_batches(indices, use_channels, adjusted_y, spacings, group_x))

  return result

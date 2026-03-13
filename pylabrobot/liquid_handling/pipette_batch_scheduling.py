"""Pipette orchestration: partition channel–target pairs into executable batches.

Multi-channel liquid handlers have physical constraints (single X carriage, minimum
Y spacing, descending Y order by channel index) that limit which channels can act
simultaneously.

    batches = plan_batches(
        use_channels, x_pos, y_pos, channel_spacings=[9.0]*8,
        num_channels=8, max_y=635.0, min_y=6.0,
    )
    for batch in batches:
        backend.position_channels_in_y_direction(batch.y_positions)
        ...
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from pylabrobot.liquid_handling.utils import (
  MIN_SPACING_EDGE,
  get_wide_single_resource_liquid_op_offsets,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate


# --- Data types ---


@dataclass
class ChannelBatch:
  """A group of channels that can operate simultaneously.

  After transition optimization, ``y_positions`` contains entries for all instrument
  channels (not just active and phantom ones).
  """

  x_position: float
  indices: List[int]
  channels: List[int]
  y_positions: Dict[int, float] = field(default_factory=dict)  # includes phantoms


# --- Spacing helpers ---


def _effective_spacing(spacings: List[float], ch_lo: int, ch_hi: int) -> float:
  """Max of per-channel spacings across ch_lo..ch_hi (inclusive).

  Used by ``compute_single_container_offsets`` to determine a single uniform spacing
  for spreading channels across a wide container.
  """
  return max(spacings[ch_lo : ch_hi + 1])


def _span_required(spacings: List[float], ch_lo: int, ch_hi: int) -> float:
  """Minimum total Y distance required between channels ch_lo and ch_hi.

  Sums the actual pairwise spacing for each adjacent pair in the range, where each
  pair's spacing is ``max(spacings[k], spacings[k+1])``. This is tighter than
  ``(ch_hi - ch_lo) * max(spacings[ch_lo:ch_hi+1])`` when spacings are non-uniform.
  """
  return sum(max(spacings[ch], spacings[ch + 1]) for ch in range(ch_lo, ch_hi))


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
      cumulative += max(spacings[phantom - 1], spacings[phantom])
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


# --- Batch transition optimization ---


def _find_next_y_target(
  channel: int, start_batch: int, batches: List[ChannelBatch]
) -> Optional[float]:
  """Return the Y position where *channel* is next needed.

  Searches ``batches[start_batch:]`` for the first batch whose ``y_positions``
  contains *channel* (active or phantom). Returns ``None`` if not found.
  """
  for batch in batches[start_batch:]:
    if channel in batch.y_positions:
      return batch.y_positions[channel]
  return None


def _optimize_batch_transitions(
  batches: List[ChannelBatch],
  num_channels: int,
  spacings: List[float],
  max_y: float,
  min_y: float,
) -> None:
  """Pre-position idle channels toward their next-needed Y coordinate.

  Mutates each batch's ``y_positions`` in-place so it contains keys for ALL
  ``num_channels`` channels, ensuring every channel has a defined position
  for every batch.

  Args:
    batches: List of ChannelBatch — modified in place.
    num_channels: Total number of channels on the instrument.
    spacings: Per-channel minimum Y spacing list (length >= num_channels).
    max_y: Maximum Y position reachable by channel 0 (mm).
    min_y: Minimum Y position reachable by channel N-1 (mm).
  """

  for batch_idx, batch in enumerate(batches):
    positions = batch.y_positions
    fixed = set(batch.channels)  # only active channels are immovable, not phantoms

    # 1. Assign targets: idle channels get their next-needed Y position.
    for ch in range(num_channels):
      if ch in fixed:
        continue
      target = _find_next_y_target(ch, batch_idx + 1, batches)
      if target is not None:
        positions[ch] = target

    # 2. Fill gaps: channels with no current or future use stay where they were
    #    in the previous batch. For batch 0 (no previous), pack at min spacing
    #    from the nearest already-positioned neighbor.
    prev_positions = batches[batch_idx - 1].y_positions if batch_idx > 0 else None
    for ch in range(num_channels):
      if ch in positions:
        continue
      if prev_positions is not None and ch in prev_positions:
        positions[ch] = prev_positions[ch]
      elif ch == 0:
        # First batch, ch0 has no reference — pack above ch1
        spacing = _min_spacing_between(spacings, 0, 1)
        positions[ch] = positions.get(1, max_y) + spacing
      else:
        spacing = _min_spacing_between(spacings, ch - 1, ch)
        positions[ch] = positions[ch - 1] - spacing

    # 3. Forward sweep (ch 1 → N-1): enforce spacing, only move free channels.
    for ch in range(1, num_channels):
      if ch in fixed:
        continue
      spacing = _min_spacing_between(spacings, ch - 1, ch)
      if positions[ch - 1] - positions[ch] < spacing - 1e-9:
        positions[ch] = positions[ch - 1] - spacing

    # 4. Backward sweep (ch N-2 → 0): enforce spacing, only move free channels.
    for ch in range(num_channels - 2, -1, -1):
      if ch in fixed:
        continue
      spacing = _min_spacing_between(spacings, ch, ch + 1)
      if positions[ch] - positions[ch + 1] < spacing - 1e-9:
        positions[ch] = positions[ch + 1] + spacing

    # 5. Bounds clamp (free channels only).
    for ch in range(num_channels):
      if ch in fixed:
        continue
      if positions[ch] > max_y:
        positions[ch] = max_y
      if positions[ch] < min_y:
        positions[ch] = min_y

    # Re-run forward sweep to propagate clamped bounds.
    for ch in range(1, num_channels):
      if ch in fixed:
        continue
      spacing = _min_spacing_between(spacings, ch - 1, ch)
      if positions[ch - 1] - positions[ch] < spacing - 1e-9:
        positions[ch] = positions[ch - 1] - spacing

    # Re-run backward sweep to propagate clamped bounds upward.
    for ch in range(num_channels - 2, -1, -1):
      if ch in fixed:
        continue
      spacing = _min_spacing_between(spacings, ch, ch + 1)
      if positions[ch] - positions[ch + 1] < spacing - 1e-9:
        positions[ch] = positions[ch + 1] + spacing


# --- Input validation and position computation ---


def compute_single_container_offsets(
  container: Container,
  use_channels: List[int],
  channel_spacings: Union[float, List[float]],
) -> Optional[List[Coordinate]]:
  """Compute spread Y offsets for multiple channels targeting the same container.

  Returns None if the container is too small — caller should fall back to center
  offsets and let plan_batches serialize.
  """

  if len(use_channels) == 0:
    return []

  ch_lo, ch_hi = min(use_channels), max(use_channels)
  if isinstance(channel_spacings, (int, float)):
    spacing = float(channel_spacings)
  else:
    spacing = _effective_spacing(channel_spacings, ch_lo, ch_hi)

  num_physical = ch_hi - ch_lo + 1
  min_required = MIN_SPACING_EDGE * 2 + (num_physical - 1) * spacing

  if container.get_absolute_size_y() < min_required:
    return None

  all_offsets = get_wide_single_resource_liquid_op_offsets(
    resource=container,
    num_channels=num_physical,
    min_spacing=spacing,
  )
  offsets = [all_offsets[ch - ch_lo] for ch in use_channels]

  # Shift odd channel spans +5.5mm to avoid container center dividers
  if num_physical > 1 and num_physical % 2 != 0:
    offsets = [o + Coordinate(0, 5.5, 0) for o in offsets]

  return offsets


def validate_probing_inputs(
  containers: List[Container],
  use_channels: Optional[List[int]],
  num_channels: int,
) -> List[int]:
  """Validate and normalize channel selection for liquid height probing.

  If *use_channels* is ``None``, defaults to ``[0, 1, ..., len(containers)-1]``.

  Returns:
    Validated list of channel indices.

  Raises:
    ValueError: If channels are empty, out of range, or contain duplicates.
  """
  if use_channels is None:
    use_channels = list(range(len(containers)))
  if len(use_channels) == 0:
    raise ValueError("use_channels must not be empty.")
  if not all(0 <= ch < num_channels for ch in use_channels):
    raise ValueError(
      f"All use_channels must be integers in range [0, {num_channels - 1}], got {use_channels}."
    )
  if len(use_channels) != len(set(use_channels)):
    raise ValueError("use_channels must not contain duplicates.")
  if len(containers) != len(use_channels):
    raise ValueError(
      f"Length of containers and use_channels must match, "
      f"got {len(containers)} and {len(use_channels)}."
    )
  return use_channels


def compute_positions(
  containers: List[Container],
  resource_offsets: List[Coordinate],
  deck: "Deck",  # noqa: F821
) -> Tuple[List[float], List[float]]:
  """Convert containers and offsets into absolute X/Y machine coordinates.

  Returns:
    (x_positions, y_positions) — parallel lists of absolute coordinates in mm,
    one entry per container.
  """
  x_pos: List[float] = []
  y_pos: List[float] = []
  for resource, offset in zip(containers, resource_offsets):
    loc = resource.get_location_wrt(deck, x="c", y="c", z="b")
    x_pos.append(loc.x + offset.x)
    y_pos.append(loc.y + offset.y)
  return x_pos, y_pos


# --- Public API ---


def plan_batches(
  use_channels: List[int],
  x_pos: List[float],
  y_pos: List[float],
  channel_spacings: Union[float, List[float]],
  x_tolerance: float,
  num_channels: Optional[int] = None,
  max_y: Optional[float] = None,
  min_y: Optional[float] = None,
) -> List[ChannelBatch]:
  """Partition channel–position pairs into executable batches.

  Groups by X position (within *x_tolerance*), then within each X group partitions
  into Y sub-batches respecting per-channel minimum spacing. Computes phantom channel
  positions for intermediate channels between non-consecutive batch members.

  When *num_channels*, *max_y*, and *min_y* are all provided, idle channels are
  pre-positioned toward their next-needed Y coordinate to minimize travel between
  batch transitions.

  Args:
    use_channels: Channel indices being used (e.g. [0, 1, 2, 5, 6, 7]).
    x_pos: Absolute X position for each entry in *use_channels*.
    y_pos: Absolute Y position for each entry in *use_channels*.
    channel_spacings: Minimum Y spacing per channel (mm). Scalar for uniform,
      or a list with one entry per channel on the instrument.
    x_tolerance: Positions within this tolerance share an X group.
    num_channels: Total number of channels on the instrument. Required for
      transition optimization.
    max_y: Maximum Y position reachable by channel 0 (mm). Required for
      transition optimization.
    min_y: Minimum Y position reachable by channel N-1 (mm). Required for
      transition optimization.

  Returns:
    Flat list of ChannelBatch sorted by ascending X position.
  """

  if not (len(use_channels) == len(x_pos) == len(y_pos)):
    raise ValueError(
      f"use_channels, x_pos, and y_pos must have the same length, "
      f"got {len(use_channels)}, {len(x_pos)}, {len(y_pos)}."
    )
  if len(use_channels) == 0:
    raise ValueError("use_channels must not be empty.")

  # Normalize scalar spacing to per-channel list
  max_ch = max(use_channels)
  if isinstance(channel_spacings, (int, float)):
    spacings: List[float] = [float(channel_spacings)] * (max_ch + 1)
  else:
    spacings = channel_spacings

  # Group indices by X position (preserving first-appearance order).
  # Uses floor-based bucketing to avoid Python's banker's rounding at boundaries.
  x_groups: Dict[float, List[int]] = {}
  for i, x in enumerate(x_pos):
    x_bucket = math.floor(x / x_tolerance) * x_tolerance
    x_groups.setdefault(x_bucket, []).append(i)

  result: List[ChannelBatch] = []
  for _, indices in sorted(x_groups.items()):
    group_x = x_pos[indices[0]]
    result.extend(_partition_into_y_batches(indices, use_channels, y_pos, spacings, group_x))

  if num_channels is not None and max_y is not None and min_y is not None:
    _optimize_batch_transitions(result, num_channels, spacings, max_y, min_y)

  return result

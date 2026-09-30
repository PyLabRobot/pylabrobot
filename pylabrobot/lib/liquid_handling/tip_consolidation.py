"""Plan tip transfers without executing operations or changing tip trackers.

Callers execute each batch by picking up its origin tips and dropping them at the
corresponding targets with the same channels. Device-specific positioning and
execution remain the caller's responsibility.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from pylabrobot.resources import Tip, TipRack, TipSpot


@dataclass
class TipTransferBatch:
  """Parallel origin, target, and channel lists for one pickup/drop cycle."""

  origin_tip_spots: List[TipSpot]
  target_tip_spots: List[TipSpot]
  use_channels: List[int]


def _merge_sublists(lists: List[List[TipSpot]], max_len: int) -> List[List[TipSpot]]:
  """Merge adjacent sublists up to max_len, splitting oversized columns."""
  merged: List[List[TipSpot]] = []
  buffer: List[TipSpot] = []
  for sublist in lists:
    for start in range(0, len(sublist), max_len):
      chunk = sublist[start : start + max_len]
      if len(buffer) + len(chunk) > max_len:
        merged.append(buffer)
        buffer = []
      buffer.extend(chunk)
  if buffer:
    merged.append(buffer)
  return merged


def _column_key(tip_spot: TipSpot) -> Tuple[str, float]:
  """Group tip spots by parent rack name and local X coordinate."""
  assert tip_spot.parent is not None and tip_spot.location is not None
  return (tip_spot.parent.name, round(tip_spot.location.x, 3))


def plan_tip_consolidation(
  tip_racks: List[TipRack],
  num_channels: int,
  can_pick_up_tip: Callable[[int, Tip], bool],
  use_channels: Optional[List[int]] = None,
) -> List[TipTransferBatch]:
  """Plan consolidation of partially filled racks, grouped by tip model.

  Full and empty racks are ignored. Within each model, racks are filled in order
  of increasing empty spots, preserving input order for ties. With eight or more
  available channels, destination columns are kept together where possible and
  batches contain at most eight tips. Smaller channel selections use consecutive
  chunks of destination spots.

  Args:
    tip_racks: Racks whose tracked tip inventory should be consolidated.
    num_channels: Number of channels on the device.
    can_pick_up_tip: Predicate accepting a channel index and a tip. Used to select
      compatible channels separately for each model when use_channels is omitted.
    use_channels: Explicit channel selection, in execution order. The caller is
      responsible for ensuring these channels can handle the tips.

  Returns:
    Ordered pickup/drop batches. Planning does not mutate resources. Execute the
    plan before making other changes to the supplied racks' tip inventory.

  Raises:
    ValueError: If a partial rack has undefined or mixed tip models, an explicit
      channel selection is invalid, or no channel can handle a required transfer.
  """
  if use_channels is not None:
    if not use_channels or len(set(use_channels)) != len(use_channels):
      raise ValueError("use_channels must contain distinct channels and must not be empty.")
    if any(channel < 0 or channel >= num_channels for channel in use_channels):
      raise ValueError(f"use_channels must be in range [0, {num_channels - 1}].")

  clusters_by_model: Dict[str, List[Tuple[TipRack, int]]] = {}
  for tip_rack in tip_racks:
    spots = tip_rack.get_all_items()
    occupied = [spot for spot in spots if spot.tracker.has_tip]
    if not occupied or len(occupied) == len(spots):
      continue

    model = occupied[0].tracker.get_tip().model
    if model is None or any(spot.tracker.get_tip().model is None for spot in occupied[1:]):
      raise ValueError("Tip models must be defined for consolidation.")
    if any(spot.tracker.get_tip().model != model for spot in occupied[1:]):
      raise ValueError(
        f"Tip rack {tip_rack.name} has mixed tip models, cannot consolidate: "
        f"{[spot.tracker.get_tip() for spot in occupied]}"
      )
    clusters_by_model.setdefault(model, []).append((tip_rack, len(spots) - len(occupied)))

  batches: List[TipTransferBatch] = []
  for rack_list in clusters_by_model.values():
    rack_list.sort(key=lambda entry: entry[1])
    all_spots = [spot for rack, _ in rack_list for spot in rack.get_all_items()]
    presence = [spot.tracker.has_tip for spot in all_spots]
    num_tips = sum(presence)
    origins = [
      spot
      for index, (spot, has_tip) in enumerate(zip(all_spots, presence))
      if has_tip and index >= num_tips
    ]
    targets = [
      spot
      for index, (spot, has_tip) in enumerate(zip(all_spots, presence))
      if not has_tip and index < num_tips
    ]
    if not targets:
      continue

    tip = origins[0].tracker.get_tip()
    channels = (
      [channel for channel in range(num_channels) if can_pick_up_tip(channel, tip)]
      if use_channels is None
      else list(use_channels)
    )
    if not channels:
      raise ValueError(f"No channel capable of handling tips on deck: {tip}")

    if len(channels) >= 8:
      columns: Dict[Tuple[str, float], List[TipSpot]] = {}
      for spot in sorted(targets, key=_column_key):
        columns.setdefault(_column_key(spot), []).append(spot)
      target_batches = _merge_sublists(list(columns.values()), max_len=8)
    else:
      target_batches = [
        targets[i : i + len(channels)] for i in range(0, len(targets), len(channels))
      ]

    origin_index = 0
    for target_spots in target_batches:
      count = len(target_spots)
      batches.append(
        TipTransferBatch(
          origin_tip_spots=origins[origin_index : origin_index + count],
          target_tip_spots=target_spots,
          use_channels=channels[:count],
        )
      )
      origin_index += count
  return batches

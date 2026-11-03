"""Find which tip spots hold a tip by picking the tips up and putting them back.

Planning groups the spots so each group is one pick-up; the runner takes the device's own pick-up,
drop and tip sensor calls, so it works on any liquid handler whose channels sense their tips.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Collection, Dict, List, Optional, Sequence, Tuple

from pylabrobot.resources import TipSpot


@dataclass
class TipProbeBatch:
  """Parallel spot and channel lists for one pick-up and the drop that puts the tips back."""

  tip_spots: List[TipSpot]
  use_channels: List[int]


def _x_of(tip_spot: TipSpot) -> float:
  """Where the spot stands in X, in mm, on whatever it is placed on."""
  return round(tip_spot.get_absolute_location("c", "c", "t").x, 3)


def _y_of(tip_spot: TipSpot) -> float:
  """Where the spot stands in Y, in mm, on whatever it is placed on."""
  return round(tip_spot.get_absolute_location("c", "c", "t").y, 3)


def plan_tip_presence_probes(
  tip_spots: List[TipSpot], use_channels: List[int]
) -> List[TipProbeBatch]:
  """Group the spots, one channel each, into pick-ups at one X, in ascending X.

  The channels ride one arm, so spots at different X cannot be taken in one move. Each spot keeps
  the channel it was given. Planning does not change any tracker.

  Args:
    tip_spots: the spots to probe.
    use_channels: the channel for each spot, 0-indexed from the back.

  Returns:
    The pick-ups, in ascending X.

  Raises:
    ValueError: If the lists differ in length, or a channel is given twice.
  """
  if len(tip_spots) != len(use_channels):
    raise ValueError(
      f"one channel per tip spot: {len(use_channels)} channels for {len(tip_spots)} tip spots"
    )
  if len(set(use_channels)) != len(use_channels):
    raise ValueError(f"use_channels must each be named once, are {use_channels}")
  groups: Dict[float, List[Tuple[TipSpot, int]]] = {}
  for tip_spot, channel in zip(tip_spots, use_channels):
    groups.setdefault(_x_of(tip_spot), []).append((tip_spot, channel))
  return [
    TipProbeBatch(tip_spots=[s for s, _ in group], use_channels=[c for _, c in group])
    for _, group in sorted(groups.items())
  ]


def plan_tip_inventory(tip_spots: List[TipSpot], use_channels: List[int]) -> List[TipProbeBatch]:
  """Deal the spots to the channels one column at a time, so each pick-up stays at one X.

  Columns in ascending X; in each, the spots rear to front go to the channels in ascending order,
  channel 0 standing furthest back, as many at a time as there are channels. Spots given in any
  order take no more pick-ups than their columns need. Planning does not change any tracker.

  Args:
    tip_spots: the spots to probe, in any order.
    use_channels: the channels to probe with, 0-indexed from the back.

  Returns:
    The pick-ups, in ascending X.

  Raises:
    ValueError: If no channel is given, a channel is given twice, or a spot is given twice.
  """
  if not use_channels:
    raise ValueError("use_channels must not be empty")
  if len(set(use_channels)) != len(use_channels):
    raise ValueError(f"use_channels must each be named once, are {use_channels}")
  names = [tip_spot.name for tip_spot in tip_spots]
  if len(set(names)) != len(names):
    raise ValueError(f"tip spots must each be given once, are {names}")
  channels = sorted(use_channels)
  columns: Dict[float, List[TipSpot]] = {}
  for tip_spot in tip_spots:
    columns.setdefault(_x_of(tip_spot), []).append(tip_spot)
  batches = []
  for _, column in sorted(columns.items()):
    column.sort(key=_y_of, reverse=True)
    for start in range(0, len(column), len(channels)):
      spots = column[start : start + len(channels)]
      batches.append(TipProbeBatch(tip_spots=spots, use_channels=channels[: len(spots)]))
  return batches


async def _get_missed_channels(
  error: Exception,
  channels: List[int],
  sense_tip_presence: Optional[Callable[[], Awaitable[Sequence[bool]]]],
  missed_channels: Optional[Callable[[Exception], Optional[Collection[int]]]],
) -> Optional[List[int]]:
  """Which channels a failed pick-up left without a tip: as its error names them, else as sensed.

  The sensors are read for the pick-up's own `channels` only; the error may name any channel.

  Returns:
    The channels, or None when neither the error nor the sensors can say.
  """
  if missed_channels is not None:
    named = missed_channels(error)
    if named is not None:
      return sorted(named)
  if sense_tip_presence is None:
    return None
  try:
    sensed = await sense_tip_presence()
  except Exception:
    return None
  return [channel for channel in channels if not sensed[channel]]


async def probe_tip_presence_via_pickup(
  tip_spots: List[TipSpot],
  use_channels: List[int],
  *,
  pick_up_tips: Callable[[List[TipSpot], List[int]], Awaitable[None]],
  drop_tips: Callable[[List[TipSpot], List[int]], Awaitable[None]],
  sense_tip_presence: Optional[Callable[[], Awaitable[Sequence[bool]]]] = None,
  missed_channels: Optional[Callable[[Exception], Optional[Collection[int]]]] = None,
) -> Dict[str, bool]:
  """Pick up the tip in each spot and put it back; a channel that comes up empty found none.

  After a pick-up that raises, which channels came up empty is read from the error by
  `missed_channels` when it names them, else from the channels' own sensors. The error is raised
  again when neither says, when they name a channel outside the pick-up, or when no channel came
  up empty, so a failure other than an empty spot is never taken for one. The tips picked up go
  straight back into their own spots. Trackers are left as the device's own calls leave them.

  Args:
    tip_spots: the spots to probe.
    use_channels: the channel for each spot, 0-indexed from the back.
    pick_up_tips: the device's pick-up, taking spots and channels.
    drop_tips: the device's drop, taking spots and channels.
    sense_tip_presence: the device's tip sensors, one flag per channel, indexed by channel.
    missed_channels: the channels a pick-up's error names as having failed, or None when it
      names none.

  Returns:
    Each spot's name, and whether it held a tip.

  Raises:
    ValueError: As `plan_tip_presence_probes`.
  """
  present = {tip_spot.name: True for tip_spot in tip_spots}
  for batch in plan_tip_presence_probes(tip_spots, use_channels):
    held = list(batch.use_channels)
    try:
      await pick_up_tips(batch.tip_spots, batch.use_channels)
    except Exception as error:
      missed = await _get_missed_channels(
        error, batch.use_channels, sense_tip_presence, missed_channels
      )
      if not missed or not set(missed) <= set(batch.use_channels):
        raise
      held = [channel for channel in batch.use_channels if channel not in missed]
      for tip_spot, channel in zip(batch.tip_spots, batch.use_channels):
        if channel in missed:
          present[tip_spot.name] = False
    if held:
      back = [s for s, c in zip(batch.tip_spots, batch.use_channels) if c in held]
      await drop_tips(back, held)
  return present


async def probe_tip_inventory(
  tip_spots: List[TipSpot],
  use_channels: List[int],
  *,
  pick_up_tips: Callable[[List[TipSpot], List[int]], Awaitable[None]],
  drop_tips: Callable[[List[TipSpot], List[int]], Awaitable[None]],
  sense_tip_presence: Optional[Callable[[], Awaitable[Sequence[bool]]]] = None,
  missed_channels: Optional[Callable[[Exception], Optional[Collection[int]]]] = None,
) -> Dict[str, bool]:
  """Probe any number of spots in any order, dealt to the channels as `plan_tip_inventory` does.

  Args:
    tip_spots: the spots to probe, in any order; the result keeps it.
    use_channels: the channels to probe with, 0-indexed from the back.
    pick_up_tips: as `probe_tip_presence_via_pickup`.
    drop_tips: as `probe_tip_presence_via_pickup`.
    sense_tip_presence: as `probe_tip_presence_via_pickup`.
    missed_channels: as `probe_tip_presence_via_pickup`.

  Returns:
    Each spot's name, and whether it held a tip.

  Raises:
    ValueError: As `plan_tip_inventory`.
  """
  present: Dict[str, bool] = {}
  for batch in plan_tip_inventory(tip_spots, use_channels):
    present.update(
      await probe_tip_presence_via_pickup(
        batch.tip_spots,
        batch.use_channels,
        pick_up_tips=pick_up_tips,
        drop_tips=drop_tips,
        sense_tip_presence=sense_tip_presence,
        missed_channels=missed_channels,
      )
    )
  return {tip_spot.name: present[tip_spot.name] for tip_spot in tip_spots}

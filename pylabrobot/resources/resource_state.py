"""Shared tip / volume / deck state helpers for device peers.

Devices adapt instrument outcomes into :data:`ChannelSuccesses` / bools, then call
these helpers. No vendor or transport imports — safe for Prep, Nimbus, and a future
LiquidHandler to share.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Literal, Mapping, Optional, Sequence, Union

from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.tip_tracker import TipTracker, does_tip_tracking
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.volume_tracker import does_volume_tracking

ChannelSuccesses = Mapping[int, bool]


def all_channels_succeeded(use_channels: Sequence[int]) -> dict[int, bool]:
  return {ch: True for ch in use_channels}


def successes_from_failed_channels(
  use_channels: Sequence[int],
  failed: Collection[int],
) -> dict[int, bool]:
  failed_set = set(failed)
  return {ch: ch not in failed_set for ch in use_channels}


@dataclass(frozen=True)
class TipPickupIntent:
  channel: int
  tip_spot: TipSpot
  tip: Tip
  channel_tracker: TipTracker


@dataclass(frozen=True)
class TipDropIntent:
  channel: int
  destination: Union[TipSpot, Trash]
  tip: Tip
  channel_tracker: TipTracker


@dataclass(frozen=True)
class VolumeTransferIntent:
  channel: int
  container: Container
  tip: Tip
  volume_ul: float
  direction: Literal["aspirate", "dispense"]


def queue_tip_pickups(intents: Sequence[TipPickupIntent]) -> None:
  """Queue spot remove + channel add (commit=False). Spot ops gated by tip tracking."""
  for intent in intents:
    if intent.channel_tracker.has_tip:
      raise RuntimeError(f"Channel {intent.channel} already has a tip")
    if does_tip_tracking() and not intent.tip_spot.tracker.is_disabled:
      intent.tip_spot.tracker.remove_tip(commit=False)
    intent.channel_tracker.add_tip(intent.tip, origin=intent.tip_spot, commit=False)


def queue_tip_drops(intents: Sequence[TipDropIntent]) -> None:
  """Queue channel remove; TipSpot destinations get the tip back. Trash: channel only."""
  for intent in intents:
    if not intent.tip.tracker.is_disabled and intent.tip.tracker.get_used_volume() > 1e-6:
      raise RuntimeError(
        f"Cannot drop tip on channel {intent.channel} with volume "
        f"{intent.tip.tracker.get_used_volume()} uL"
      )
    if not intent.channel_tracker.has_tip:
      raise RuntimeError(f"Channel {intent.channel} has no tip to drop")
    intent.channel_tracker.remove_tip(commit=False)
    if isinstance(intent.destination, TipSpot):
      if does_tip_tracking() and not intent.destination.tracker.is_disabled:
        intent.destination.tracker.add_tip(intent.tip, origin=None, commit=False)


def finalize_tip_ops(
  intents: Sequence[Union[TipPickupIntent, TipDropIntent]],
  successes: ChannelSuccesses,
) -> None:
  for intent in intents:
    ok = successes.get(intent.channel, False)
    if isinstance(intent, TipPickupIntent):
      if does_tip_tracking() and not intent.tip_spot.tracker.is_disabled:
        (intent.tip_spot.tracker.commit if ok else intent.tip_spot.tracker.rollback)()
      (intent.channel_tracker.commit if ok else intent.channel_tracker.rollback)()
    else:
      (intent.channel_tracker.commit if ok else intent.channel_tracker.rollback)()
      if isinstance(intent.destination, TipSpot):
        if does_tip_tracking() and not intent.destination.tracker.is_disabled:
          (intent.destination.tracker.commit if ok else intent.destination.tracker.rollback)()


def queue_volume_transfers(intents: Sequence[VolumeTransferIntent]) -> None:
  if not does_volume_tracking():
    return
  for intent in intents:
    if intent.direction == "aspirate":
      if not intent.container.tracker.is_disabled:
        intent.container.tracker.remove_liquid(intent.volume_ul)
      if not intent.tip.tracker.is_disabled:
        intent.tip.tracker.add_liquid(intent.volume_ul)
    else:
      if not intent.tip.tracker.is_disabled:
        intent.tip.tracker.remove_liquid(intent.volume_ul)
      if not intent.container.tracker.is_disabled:
        intent.container.tracker.add_liquid(intent.volume_ul)


def finalize_volume_ops(
  intents: Sequence[VolumeTransferIntent],
  successes: ChannelSuccesses,
) -> None:
  if not does_volume_tracking():
    return
  for intent in intents:
    ok = successes.get(intent.channel, False)
    if not intent.container.tracker.is_disabled:
      (intent.container.tracker.commit if ok else intent.container.tracker.rollback)()
    if not intent.tip.tracker.is_disabled:
      (intent.tip.tracker.commit if ok else intent.tip.tracker.rollback)()


def place_resource(
  resource: Resource,
  destination: Resource,
  *,
  location: Optional[Coordinate] = None,
) -> None:
  """Reassign ``resource`` under ``destination`` after a successful place."""
  destination.check_can_drop_resource_here(resource)
  resource.unassign()
  if isinstance(destination, ResourceHolder):
    destination.assign_child_resource(resource, location=location)
  else:
    destination.assign_child_resource(
      resource, location=location if location is not None else Coordinate.zero()
    )

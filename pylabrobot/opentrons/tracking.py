"""Transactional liquid bookkeeping shared by Opentrons pipettes."""

import math
from contextlib import contextmanager
from typing import Iterator, Optional, Sequence

from pylabrobot.resources.volume_tracker import VolumeTracker, does_volume_tracking


def validate_liquid_transfer(
  sources: Sequence[Optional[VolumeTracker]],
  destinations: Sequence[Optional[VolumeTracker]],
  volume: float,
) -> None:
  """Check available liquid and capacity without changing any tracker."""
  if len(sources) != len(destinations):
    raise ValueError("Each source must have a corresponding destination")
  if not math.isfinite(volume) or volume < 0:
    raise ValueError("volume must be finite and non-negative")
  if not does_volume_tracking():
    return
  changes = {
    tracker: 0.0
    for tracker in (*sources, *destinations)
    if tracker is not None and not tracker.is_disabled
  }
  for source, destination in zip(sources, destinations):
    if source is not None and source in changes:
      changes[source] -= volume
      source.validate_remove_liquid(-changes[source])
    if destination is not None and destination in changes:
      changes[destination] += volume
      destination.validate_add_liquid(changes[destination])


@contextmanager
def track_liquid_transfer(
  sources: Sequence[Optional[VolumeTracker]],
  destinations: Sequence[Optional[VolumeTracker]],
  volume: float,
) -> Iterator[None]:
  """Stage each nozzle's transfer and settle all trackers together.

  A repeated tracker represents a shared reservoir. ``None`` represents an
  untracked source or destination. Disabled trackers are left unchanged.
  Rollback restores bookkeeping, not physical state: a timed-out or cancelled
  hardware command can still require operator reconciliation.
  """
  validate_liquid_transfer(sources, destinations, volume)
  trackers = list(
    dict.fromkeys(
      tracker
      for tracker in (*sources, *destinations)
      if tracker is not None and does_volume_tracking() and not tracker.is_disabled
    )
  )
  try:
    for source, destination in zip(sources, destinations):
      if source in trackers:
        assert source is not None
        source.remove_liquid(volume)
      if destination in trackers:
        assert destination is not None
        destination.add_liquid(volume)
    yield
  except BaseException:
    for tracker in trackers:
      tracker.rollback()
    raise
  else:
    for tracker in trackers:
      tracker.commit()

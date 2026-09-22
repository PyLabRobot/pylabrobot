"""Transactional liquid bookkeeping shared by Opentrons pipettes."""

import math
from contextlib import contextmanager
from typing import Iterator, Optional, Sequence

from pylabrobot.resources.volume_tracker import VolumeTracker, does_volume_tracking


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
  if len(sources) != len(destinations):
    raise ValueError("Each source must have a corresponding destination")
  if not math.isfinite(volume) or volume < 0:
    raise ValueError("volume must be finite and non-negative")
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

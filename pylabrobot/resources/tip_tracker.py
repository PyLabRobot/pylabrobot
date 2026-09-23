"""Deprecated: tip state is the resource tree's, and `TipTracker` is legacy's.

A tip spot holds its tip as a child, and a tip mounting shaft does too. The tip tracking switch is
in `pylabrobot.resources.tip_tracking`, and `TipTracker` in `pylabrobot.legacy.tip_tracker`.
"""

# TODO: Remove >2026-12

import warnings
from typing import Any

from pylabrobot.resources.tip_tracking import (  # noqa: F401 (re-exported for backwards compatibility)
  does_tip_tracking,
  no_tip_tracking,
  set_tip_tracking,
)


def __getattr__(name: str) -> Any:
  if name in ("TipTracker", "TrackerCallback"):
    warnings.warn(
      f"pylabrobot.resources.tip_tracker.{name} is deprecated and will be removed in the future. "
      f"A tip is its holder's child in the resource tree; use "
      f"pylabrobot.legacy.tip_tracker.{name} for the legacy liquid handler.",
      DeprecationWarning,
      stacklevel=2,
    )
    from pylabrobot.legacy import tip_tracker

    return getattr(tip_tracker, name)
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

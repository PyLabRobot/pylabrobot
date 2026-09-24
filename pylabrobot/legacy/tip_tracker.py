"""The legacy tip tracker: the pending, commit and rollback bookkeeping of the legacy liquid handler.

A resource holds no tip tracker. A tip spot's tip is its child in the resource tree, and a tracker for
a spot is a view on that tree, kept here, remembering only what the spot held before an uncommitted
operation. A tracker for a legacy channel, which has no resource, holds its tip itself.
"""

import weakref
from typing import TYPE_CHECKING, Callable, Dict, Optional, cast

from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_tracking import (  # noqa: F401 (re-exported for legacy imports)
  does_tip_tracking,
  no_tip_tracking,
  set_tip_tracking,
)
from pylabrobot.serializer import SerializableMixin

if TYPE_CHECKING:
  from pylabrobot.resources.tip_rack import TipSpot


TrackerCallback = Callable[[], None]

# Marks a tracker with no uncommitted operations.
_NOTHING_PENDING = object()


class TipTracker(SerializableMixin):
  """A tip tracker tracks tip operations and raises errors if the tip operations are invalid.

  With a `holder`, the tip spot it is a view on, the tip is the spot's child in the resource tree and
  nowhere else: the tracker reads the tree, and its operations move the tip in it. Whether it is
  disabled is the spot's to say. An uncommitted operation keeps only what the spot held before it,
  to report and to roll back to.
  """

  def __init__(self, thing: str, holder: Optional["TipSpot"] = None):
    self.thing = thing
    self._holder_ref = weakref.ref(holder) if holder is not None else None
    self._is_disabled = False
    # The tip, for a tracker without a holder. With one, the tree has it.
    self._carried: Optional["Tip"] = None
    # What was held before the uncommitted operations, while there are any.
    self._before: object = _NOTHING_PENDING
    self._tip_origin: Optional["TipSpot"] = None  # not currently in a transaction, do we need that?

    self._callback: Optional[TrackerCallback] = None

  @property
  def _holder(self) -> Optional["TipSpot"]:
    return self._holder_ref() if self._holder_ref is not None else None

  @property
  def _pending_tip(self) -> Optional["Tip"]:
    """The tip held now, uncommitted operations included."""
    holder = self._holder
    if holder is None:
      return self._carried
    return holder.tip

  @property
  def _tip(self) -> Optional["Tip"]:
    """The committed tip: what was held before the uncommitted operations, if there are any."""
    if self._before is _NOTHING_PENDING:
      return self._pending_tip
    return cast(Optional["Tip"], self._before)

  @_tip.setter
  def _tip(self, tip: Optional["Tip"]) -> None:
    self._before = tip

  def _hold(self, tip: Optional["Tip"]) -> None:
    """Hold `tip`, or nothing, without committing."""
    if self._before is _NOTHING_PENDING:
      self._before = self._pending_tip
    self._put(tip)

  def _put(self, tip: Optional["Tip"]) -> None:
    """Make the holder, or the tracker if it has none, carry exactly `tip`."""
    from pylabrobot.resources.tip_rack import resting_location

    holder = self._holder
    if holder is None:
      self._carried = tip
      return
    carried = self._pending_tip
    if carried is tip:
      return
    if carried is not None:
      holder.unassign_child_resource(carried)
    if tip is not None:
      if tip.parent is not None:
        tip.parent.unassign_child_resource(tip)
      holder.assign_child_resource(tip, location=resting_location(holder, tip))

  @property
  def is_disabled(self) -> bool:
    holder = self._holder
    if holder is None:
      return self._is_disabled
    return not holder.tip_tracking_enabled

  @property
  def has_tip(self) -> bool:
    """Whether the tip tracker has a tip. Note that this includes pending operations."""
    return self._pending_tip is not None

  def get_tip(self) -> "Tip":
    """Get the committed tip. A pending removal leaves it readable until the operation commits.

    Raises:
      NoTipError: If the tip spot does not have a tip.
    """

    if self._tip is None:
      raise NoTipError(f"{self.thing} does not have a tip.")
    return self._tip

  def disable(self) -> None:
    """Disable the tip tracker."""
    holder = self._holder
    if holder is None:
      self._is_disabled = True
    else:
      holder.disable_tip_tracking()

  def enable(self) -> None:
    """Enable the tip tracker."""
    holder = self._holder
    if holder is None:
      self._is_disabled = False
    else:
      holder.enable_tip_tracking()

  def add_tip(
    self,
    tip: Tip,
    origin: Optional["TipSpot"] = None,
    commit: bool = True,
  ) -> None:
    """Update the pending state with the operation, if the operation is valid.

    Args:
      tip: The tip to add.
      commit: Whether to commit the operation immediately. If `False`, the operation will be
        committed later with `commit()` or rolled back with `rollback()`.
    """
    if self.is_disabled:
      raise RuntimeError("Tip tracker is disabled. Call `enable()`.")
    if self._pending_tip is not None:
      raise HasTipError(f"{self.thing} already has a tip.")
    self._hold(tip)

    self._tip_origin = origin

    if commit:
      self.commit()

  def remove_tip(self, commit: bool = False) -> None:
    """Update the pending state with the operation, if the operation is valid"""
    if self.is_disabled:
      raise RuntimeError("Tip tracker is disabled. Call `enable()`.")
    if self._pending_tip is None:
      raise NoTipError(f"{self.thing} does not have a tip.")
    self._hold(None)

    if commit:
      self.commit()

  def commit(self) -> None:
    """Commit the pending operations."""
    self._before = _NOTHING_PENDING
    # Propagate state-update callback to the tip's volume tracker
    if self._tip is not None and self._callback is not None:
      self._tip.tracker.register_callback(self._callback)
    if self._callback is not None:
      self._callback()

  def rollback(self) -> None:
    """Rollback the pending operations."""
    if self.is_disabled:
      raise RuntimeError("Tip tracker is disabled. Call `enable()`.")
    self._put(self._tip)
    self._before = _NOTHING_PENDING

  def clear(self) -> None:
    """Clear the history."""
    self._put(None)
    self._before = _NOTHING_PENDING

  def serialize(self) -> dict:
    """Serialize the state of the tip tracker."""
    return {
      "tip": self._tip.serialize() if self._tip is not None else None,
      "tip_state": self._tip.tracker.serialize() if self._tip is not None else None,
      "pending_tip": self._pending_tip.serialize() if self._pending_tip is not None else None,
    }

  def load_state(self, state: dict) -> None:
    """Load a saved tip tracker state."""

    tip_data, pending_tip_data = state.get("tip"), state.get("pending_tip")
    tip = Tip.deserialize(tip_data) if tip_data is not None else None
    pending_tip = Tip.deserialize(pending_tip_data) if pending_tip_data is not None else None
    self._put(pending_tip)
    same = (tip is None) == (pending_tip is None) and (
      tip is None or tip.serialize() == cast(Tip, pending_tip).serialize()
    )
    self._before = _NOTHING_PENDING if same else tip

  def get_tip_origin(self) -> Optional["TipSpot"]:
    """Get the origin of the current tip, if known."""
    return self._tip_origin

  def __repr__(self) -> str:
    return (
      f"TipTracker({self.thing}, is_disabled={self.is_disabled}, has_tip={self.has_tip}"
      + f" tip={self._tip}, pending_tip={self._pending_tip})"
    )

  def register_callback(self, callback: TrackerCallback) -> None:
    self._callback = callback


# One tracker per spot, so what an uncommitted operation remembers outlives any one lookup. Kept
# here rather than on the spot, which holds no tracker. Keyed by identity: resources compare and hash
# by value, and two spots can look alike. A spot that is gone takes its tracker along.
_spot_trackers: Dict[int, TipTracker] = {}


def tip_spot_tracker(spot: "TipSpot") -> TipTracker:
  """The legacy tracker for a tip spot: a view on the tip it holds in the resource tree.

  Args:
    spot: the spot.

  Returns:
    Its tracker, the same one every time.
  """
  tracker = _spot_trackers.get(id(spot))
  if tracker is not None and tracker._holder is spot:
    return tracker
  tracker = TipTracker(thing=spot.name, holder=spot)
  # Held weakly, as the tracker holds its spot: the tracker must not keep the spot alive.
  spot_ref = weakref.ref(spot)

  def state_updated() -> None:
    held = spot_ref()
    if held is not None:
      held._state_updated()

  tracker.register_callback(state_updated)
  key = id(spot)
  _spot_trackers[key] = tracker
  weakref.finalize(spot, _spot_trackers.pop, key, None)
  return tracker

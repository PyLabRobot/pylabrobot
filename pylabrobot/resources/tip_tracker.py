import contextlib
import sys
from typing import Callable, Optional, TYPE_CHECKING, cast

from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.serializer import deserialize

from pylabrobot.resources.tip import Tip
if TYPE_CHECKING:
  from pylabrobot.resources.tip_rack import TipSpot


this = sys.modules[__name__]
this.tip_tracking_enabled = False # type: ignore

def set_tip_tracking(enabled: bool):
  this.tip_tracking_enabled = enabled # type: ignore

def does_tip_tracking() -> bool:
  return this.tip_tracking_enabled # type: ignore

@contextlib.contextmanager
def no_tip_tracking():
  old_value = this.tip_tracking_enabled
  this.tip_tracking_enabled = False # type: ignore
  yield
  this.tip_tracking_enabled = old_value # type: ignore


TrackerCallback = Callable[[], None]


class TipTracker:
  """ A tip tracker tracks tip operations and raises errors if the tip operations are invalid. """

  def __init__(self, thing: str):
    self.thing = thing
    self._is_disabled = False
    self._tip: Optional["Tip"] = None
    self._pending_tip: Optional["Tip"] = None
    self._tip_origin: Optional["TipSpot"] = None # not currently in a transaction, do we need that?

    self._callback: Optional[TrackerCallback] = None

  @property
  def is_disabled(self) -> bool:
    return self._is_disabled

  @property
  def has_tip(self) -> bool:
    """ Whether the tip tracker has a tip. Note that this includes pending operations. """
    return self._pending_tip is not None

  def get_tip(self) -> "Tip":
    """ Get the tip. Note that does includes pending operations.

    Raises:
      NoTipError: If the tip spot does not have a tip.
    """

    if self._tip is None:
      raise NoTipError(f"{self.thing} does not have a tip.")
    return self._tip

  def disable(self) -> None:
    """ Disable the tip tracker. """
    self._is_disabled = True

  def enable(self) -> None:
    """ Enable the tip tracker. """
    self._is_disabled = False

  def add_tip(self, tip: Tip, origin: Optional["TipSpot"] = None, commit: bool = True) -> None:
    """ Update the pending state with the operation, if the operation is valid.

    Args:
      tip: The tip to add.
      commit: Whether to commit the operation immediately. If `False`, the operation will be
        committed later with `commit()` or rolled back with `rollback()`.
    """
    if self.is_disabled:
      raise RuntimeError("Tip tracker is disabled. Call `enable()`.")
    if self._pending_tip is not None:
      raise HasTipError(f"{self.thing} already has a tip.")
    self._pending_tip = tip

    self._tip_origin = origin

    if commit:
      self.commit()

  def remove_tip(self, commit: bool = False) -> None:
    """ Update the pending state with the operation, if the operation is valid """
    if self.is_disabled:
      raise RuntimeError("Tip tracker is disabled. Call `enable()`.")
    if self._pending_tip is None:
      raise NoTipError(f"{self.thing} does not have a tip.")
    self._pending_tip = None

    if commit:
      self.commit()

  def commit(self) -> None:
    """ Commit the pending operations. """
    self._tip = self._pending_tip
    if self._callback is not None:
      self._callback()

  def rollback(self) -> None:
    """ Rollback the pending operations. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."
    self._pending_tip = self._tip

  def clear(self) -> None:
    """ Clear the history. """
    self._tip = None
    self._pending_tip = None

  def serialize(self) -> dict:
    """ Serialize the state of the tip tracker. """
    return {
      "tip": self._tip.serialize() if self._tip is not None else None,
      "tip_state": self._tip.tracker.serialize() if self._tip is not None else None,
      "pending_tip": self._pending_tip.serialize() if self._pending_tip is not None else None
    }

  def load_state(self, state: dict) -> None:
    """ Load a saved tip tracker state. """

    self._tip = cast(Optional[Tip], deserialize(state.get("tip")))
    self._pending_tip = cast(Optional[Tip], deserialize(state.get("pending_tip")))

  def get_tip_origin(self) -> Optional["TipSpot"]:
    """ Get the origin of the current tip, if known. """
    return self._tip_origin

  def __repr__(self) -> str:
    return f"TipTracker({self.thing}, is_disabled={self.is_disabled}, has_tip={self.has_tip}" + \
      f" tip={self._tip}, pending_tip={self._pending_tip})"

  def register_callback(self, callback: TrackerCallback) -> None:
    self._callback = callback

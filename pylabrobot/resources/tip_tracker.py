# interesting parallels between this, "tip rack" tracking on 96 head, and plate reader...

from abc import ABC, abstractmethod
import contextlib
import sys
from typing import List, Optional, TYPE_CHECKING

from pylabrobot.resources.errors import TipSpotHasTipError, TipSpotHasNoTipError

if TYPE_CHECKING:
  from pylabrobot.liquid_handling.standard import TipOp, Pickup, Drop
  from pylabrobot.resources.tip import Tip


this = sys.modules[__name__]
this.tip_tracking_enabled = True # type: ignore

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


class TipTracker(ABC):
  """ A tip tracker tracks tip operations and raises errors if the tip operations are invalid. """

  def __init__(self):
    self._ops: List["TipOp"] = []
    self.pending: List["TipOp"] = []
    self._is_disabled = False
    self._tip: Optional["Tip"] = None
    self._pending_tip: Optional["Tip"] = None

  @property
  def is_disabled(self) -> bool:
    return self._is_disabled

  def set_tip(self, tip: Optional["Tip"]) -> None:
    """ Set the tip. """
    self.pending.clear()
    self._tip = tip
    self._pending_tip = tip

  @abstractmethod
  def get_tip(self) -> "Tip":
    """ Get the tip. Note that does includes pending operations.

    Raises an error if there is no tip.
    """

  def disable(self) -> None:
    """ Disable the tip tracker. """
    self._is_disabled = True

  def enable(self) -> None:
    """ Enable the tip tracker. """
    self._is_disabled = False

  @property
  def history(self) -> List["TipOp"]:
    """ The past operations. """
    return self._ops

  @property
  def has_tip(self) -> bool:
    """ Whether the tip tracker has a tip. Note that this includes pending operations. """
    return self._pending_tip is not None

  def queue_pickup(self, op: "Pickup") -> None:
    """ Update the pending state with the operation, if the operation is valid """
    if self.is_disabled:
      raise RuntimeError("Tip tracker is disabled. Call `enable()`.")
    self.handle_pickup(op)
    self.pending.append(op)

  def queue_drop(self, op: "Drop") -> None:
    """ Update the pending state with the operation, if the operation is valid """
    if self.is_disabled:
      raise RuntimeError("Tip tracker is disabled. Call `enable()`.")
    self.handle_drop(op)
    self.pending.append(op)

  @abstractmethod
  def handle_pickup(self, op: "Pickup") -> None:
    """ Update the pending state with the operation, if it is valid. """

  @abstractmethod
  def handle_drop(self, op: "Drop") -> None:
    """ Update the pending state with the operation, if it is valid. """

  def commit(self) -> None:
    """ Commit the pending operations. """

    self._tip = self._pending_tip
    self._ops += self.pending
    self.pending.clear()

  def rollback(self) -> None:
    """ Rollback the pending operations. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."
    self._pending_tip = self._tip
    self.pending.clear()

  def clear(self) -> None:
    """ Clear the history. """
    self._ops.clear()
    self.pending.clear()
    self._tip = None
    self._pending_tip = None


class SpotTipTracker(TipTracker):
  """ A tip spot tip tracker tracks and validates tip operations for a single tip spot: a
  location where tips are stored.  """

  def handle_pickup(self, op: "Pickup") -> None:
    if not self.has_tip:
      raise TipSpotHasNoTipError("Tip spot has no tip.")
    self._pending_tip = None

  def handle_drop(self, op: "Drop") -> None:
    if self.has_tip:
      raise TipSpotHasTipError("Tip spot already has a tip.")
    self._pending_tip = op.tip

  def get_tip(self) -> "Tip":
    if self._tip is None:
      raise TipSpotHasNoTipError("Tip spot has no tip.")
    return self._tip

# interesting parallels between this, "tip rack" tracking on 96 head, and plate reader...

from abc import ABC, abstractmethod
import contextlib
import sys
from typing import List, Optional, TYPE_CHECKING

from pylabrobot.liquid_handling.errors import (
  ChannelHasTipError,
  ChannelHasNoTipError,
  TipSpotHasTipError,
  TipSpotHasNoTipError,
)
from pylabrobot.liquid_handling.standard import TipOp, Pickup, Drop

if TYPE_CHECKING:
  from pylabrobot.liquid_handling.resources import TipSpot
  from pylabrobot.liquid_handling.tip import Tip


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
    self._ops: List[TipOp] = []
    self.pending: List[TipOp] = []
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
  def history(self) -> List[TipOp]:
    """ The past operations. """
    return self._ops

  @property
  def has_tip(self) -> bool:
    """ Whether the tip tracker has a tip. Note that this includes pending operations. """
    return self._pending_tip is not None

  def queue_op(self, op: TipOp) -> None:
    """ Check if the operation is valid given the current state. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."
    self.handle(op)
    self.pending.append(op)

  @abstractmethod
  def handle(self, op: TipOp) -> None:
    """ Update the pending state with the operation. """

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


class ChannelTipTracker(TipTracker):
  """ A channel tip tracker tracks and validates tip operations for a single channel. """

  @property
  def _last_pickup(self) -> Optional[Pickup]:
    all_ops = self.history + self.pending
    if len(all_ops) > 0 and isinstance(all_ops[-1], Pickup):
      return all_ops[-1]
    return None

  def get_last_pickup_location(self) -> "TipSpot":
    """ The last tip pickup location. """
    if self._last_pickup is None:
      raise ChannelHasNoTipError("Channel has no tip.")
    return self._last_pickup.resource

  def handle(self, op: TipOp):
    """ Update the pending state with the operation.

    Raises:
      ChannelHasTipError: If trying to pickup a tip when the channel already has a tip.

      ChannelHasNoTipError: If trying to drop a tip when the channel has no tip.
    """

    if isinstance(op, Pickup):
      if self.has_tip:
        raise ChannelHasTipError("Channel already has tip.")
      self._pending_tip = op.tip

    if isinstance(op, Drop):
      if not self.has_tip:
        raise ChannelHasNoTipError("Channel has no tip.")
      self._pending_tip = None

  def get_tip(self) -> "Tip":
    if self._tip is None:
      raise ChannelHasNoTipError("Channel has no tip.")
    return self._tip


class SpotTipTracker(TipTracker):
  """ A tip spot tip tracker tracks and validates tip operations for a single tip spot: a
  location where tips are stored.  """

  def handle(self, op: TipOp):
    """ Update the pending state with the operation.

    Raises:
      TipSpotHasNoTipError: If trying to pickup a tip when the tip spot has no tip.

      TipSpotHasTipError: If trying to drop a tip when the tip spot already has a tip.
    """

    if isinstance(op, Pickup):
      if not self.has_tip:
        raise TipSpotHasNoTipError("Tip spot has no tip.")
      self._pending_tip = None

    if isinstance(op, Drop):
      if self.has_tip:
        raise TipSpotHasTipError("Tip spot already has a tip.")
      self._pending_tip = op.tip

  def get_tip(self) -> "Tip":
    if self._tip is None:
      raise TipSpotHasNoTipError("Tip spot has no tip.")
    return self._tip

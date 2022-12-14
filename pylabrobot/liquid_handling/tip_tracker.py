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
    self.tip: Optional["Tip"] = None

  @property
  def is_disabled(self) -> bool:
    return self._is_disabled

  def disable(self) -> None:
    """ Disable the tip tracker. """
    self._is_disabled = True

  def enable(self) -> None:
    """ Enable the tip tracker. """
    self._is_disabled = False

  @property
  def ops(self) -> List[TipOp]:
    """ The past operations. """
    return self._ops

  @property
  @abstractmethod
  def has_tip(self) -> bool:
    """ Whether the tip tracker has a tip. Note that this does include pending operations. """

  def queue_op(self, op: TipOp) -> None:
    """ Check if the operation is valid given the current state. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."
    self.validate(op)
    self.pending.append(op)

  @abstractmethod
  def validate(self, op: TipOp) -> None:
    """ Validate the current state. """

  def commit(self) -> None:
    """ Commit the pending operations. """

  def rollback(self) -> None:
    """ Rollback the pending operations. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."
    self.pending = []


class ChannelTipTracker(TipTracker):
  """ A channel tip tracker tracks and validates tip operations for a single channel. """

  @property
  def _last_pickup(self) -> Optional[Pickup]:
    if len(self.pending) > 0 and isinstance(self.pending[-1], Pickup):
      return self.pending[-1]
    if len(self.ops) > 0 and isinstance(self.ops[-1], Pickup):
      return self.ops[-1]
    return None

  def get_last_pickup_location(self) -> "TipSpot":
    """ The last tip pickup location. """
    if self._last_pickup is None:
      raise ChannelHasNoTipError("Channel has no tip.")
    return self._last_pickup.resource

  @property
  def has_tip(self) -> bool:
    num_pickups = len([op for op in self.pending if isinstance(op, Pickup)])
    num_drops = len([op for op in self.pending if isinstance(op, Drop)])
    if self.tip is None:
      return num_drops < num_pickups # if None, then we have more pickups than drops
    return num_pickups == num_drops # if not None, then we if have as many drops as pickups

  def validate(self, op: TipOp):
    """ Validate a tip operation.

    Args:
      op: The tip operation to handle.

    Raises:
      ChannelHasTipError: If trying to pickup a tip when the channel already has a tip.

      ChannelHasNoTipError: If trying to drop a tip when the channel has no tip.
    """

    if self.has_tip and isinstance(op, Pickup):
      raise ChannelHasTipError("Channel already has tip.")
    if not self.has_tip and isinstance(op, Drop):
      raise ChannelHasNoTipError("Channel has no tip.")

  def commit(self) -> None:
    """ Commit the pending operations. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."

    # Loop through the pending operations and update the tip state and op history.
    for op in self.pending:
      self._ops.append(op)
      if isinstance(op, Pickup):
        self.tip = op.tip
      elif isinstance(op, Drop):
        self.tip = None

    # Clear the pending operations.
    self.pending.clear()


class SpotTipTracker(TipTracker):
  """ A tip spot tip tracker tracks and validates tip operations for a single tip spot: a
  location where tips are stored.  """

  @property
  def has_tip(self) -> bool:
    num_pickups = len([op for op in self.pending if isinstance(op, Pickup)])
    num_drops = len([op for op in self.pending if isinstance(op, Drop)])
    if self.tip is None:
      return num_drops > num_pickups # if None, then we have more drops than pickups
    return num_pickups == num_drops # if not None, then we if have as many drops as pickups

  def validate(self, op: TipOp):
    """ Validate a tip operation.

    Args:
      op: The tip operation to handle.

    Raises:
      TipSpotHasNoTipError: If trying to pickup a tip when the tip spot has no tip.

      TipSpotHasTipError: If trying to drop a tip when the tip spot already has a tip.
    """

    if not self.has_tip and isinstance(op, Pickup):
      raise TipSpotHasNoTipError(f"Tip spot {op.resource.name} has no tip.")
    if self.has_tip and isinstance(op, Drop):
      raise TipSpotHasTipError(f"Tip spot {op.resource.name} already has a tip.")

  def commit(self) -> None:
    """ Commit the pending operations. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."

    # Loop through the pending operations and update the tip state and op history.
    for op in self.pending:
      self._ops.append(op)
      if isinstance(op, Pickup):
        self.tip = None
      elif isinstance(op, Drop):
        self.tip = op.tip

    # Clear the pending operations.
    self.pending.clear()

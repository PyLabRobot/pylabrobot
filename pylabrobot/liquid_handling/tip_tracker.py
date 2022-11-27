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
  from pylabrobot.liquid_handling.tip_type import TipType


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

  def __init__(self, start_with_tip: bool = False):
    self._ops: List[TipOp] = []
    self.pending: List[TipOp] = []
    self._start_with_tip = start_with_tip
    self._is_disabled = False

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
  def in_initial_state(self) -> bool:
    """ Whether the tracker is in the initial state. """
    return len(self._ops) == 0 and len(self.pending) == 0

  @property
  def start_with_tip(self) -> bool:
    return self._start_with_tip

  def set_initial_state(self, has_tip: bool) -> None:
    """ Set the initial state of the tip tracker. """
    if not self.in_initial_state:
      raise RuntimeError("Cannot set initial state after operations have been performed.")
    self._start_with_tip = has_tip

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
    """ Commit the pending operation. """
    assert not self.is_disabled, "Tip tracker is disabled. Call `enable()`."
    if len(self.pending) == 0:
      raise RuntimeError("No pending operations.")
    for op in self.pending:
      self._ops.append(op)
    self.pending = []

  def rollback(self) -> None:
    """ Rollback the pending operation. """
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

  @property
  def current_tip_type(self) -> Optional["TipType"]:
    """ The current tip type. """
    return self._last_pickup.tip_type if self._last_pickup is not None else None

  @property
  def current_tip_origin_spot(self) -> Optional["TipSpot"]:
    """ The current tip type. """
    return self._last_pickup.resource if self._last_pickup is not None else None

  @property
  def has_tip(self) -> bool:
    if self.in_initial_state:
      return self.start_with_tip
    return self._last_pickup is not None

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


class SpotTipTracker(TipTracker):
  """ A tip spot tip tracker tracks and validates tip operations for a single tip spot: a
  location where tips are stored.  """

  @property
  def has_tip(self) -> bool:
    if len(self.pending) > 0:
      return isinstance(self.pending[-1], Drop)
    if len(self.ops) > 0:
      return isinstance(self.ops[-1], Drop)
    return self.start_with_tip

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

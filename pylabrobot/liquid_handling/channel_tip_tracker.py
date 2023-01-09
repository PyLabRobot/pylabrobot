from typing import Optional

from pylabrobot.liquid_handling.standard import TipOp, Pickup, Drop
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.tip_tracker import TipTracker


class ChannelHasTipError(Exception):
  """ Raised when a channel has a tip, e.g. when trying to pick up a tip with a channel that already
  has a tip. """


class ChannelHasNoTipError(Exception):
  """ Raised when a channel has no tip, e.g. when trying to drop a tip with a channel that does
  not have a tip. """


class ChannelTipTracker(TipTracker):
  """ A channel tip tracker tracks and validates tip operations for a single channel. """

  @property
  def _last_pickup(self) -> Optional[Pickup]:
    all_ops = self.history + self.pending
    if len(all_ops) > 0 and isinstance(all_ops[-1], Pickup):
      return all_ops[-1]
    return None

  def get_last_pickup_location(self) -> TipSpot:
    """ The last tip pickup location. """
    if self._last_pickup is None:
      raise ChannelHasNoTipError("Channel has no tip.")
    return self._last_pickup.resource

  def handle(self, op: "TipOp"):
    """ Update the pending state with the operation.

    Raises:
      ChannelHasTipError: If trying to pickup a tip when the channel already has a tip.

      ChannelHasNoTipError: If trying to drop a tip when the channel has no tip.
    """

  def handle_pickup(self, op: Pickup):
    if self.has_tip:
      raise ChannelHasTipError("Channel already has tip.")
    self._pending_tip = op.tip

  def handle_drop(self, op: Drop): # pylint: disable=unused-argument
    if not self.has_tip:
      raise ChannelHasNoTipError("Channel has no tip.")
    self._pending_tip = None

  def get_tip(self) -> Tip:
    if self._tip is None:
      raise ChannelHasNoTipError("Channel has no tip.")
    return self._tip

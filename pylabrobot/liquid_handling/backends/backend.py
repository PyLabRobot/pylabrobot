from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Dict, List, Optional, Union

from pylabrobot.liquid_handling.standard import (
  Drop,
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.machines.backend import MachineBackend
from pylabrobot.resources import Deck, Tip
from pylabrobot.resources.tip_tracker import TipTracker


class LiquidHandlerBackend(MachineBackend, metaclass=ABCMeta):
  """
  Abstract base class for liquid handling robot backends.

  For more information on some methods and arguments, see the documentation for the
  :class:`~LiquidHandler` class.

  Attributes:
    setup_finished: Whether the backend has been set up.
  """

  _num_arms: int = 0

  @property
  def num_arms(self) -> int:
    return self._num_arms

  def __init__(self):
    super().__init__()
    self.setup_finished = False
    self._head96_installed: Optional[bool] = False
    self._deck: Optional[Deck] = None
    self._head: Optional[Dict[int, TipTracker]] = None
    self._head96: Optional[Dict[int, TipTracker]] = None

  @property
  def head96_installed(self) -> Optional[bool]:
    return self._head96_installed

  def set_deck(self, deck: Deck):
    """Set the deck for the robot. Called automatically by `LiquidHandler.setup` or can be called
    manually if interacting with the backend directly. A deck must be set before setup."""
    self._deck = deck

  def set_heads(self, head: Dict[int, TipTracker], head96: Optional[Dict[int, TipTracker]] = None):
    """Set the tip tracker for the robot. Called automatically by `LiquidHandler.setup` or can be
    called manually if interacting with the backend directly. A head must be set before setup."""
    self._head = head
    self._head96 = head96

  @property
  def deck(self) -> Deck:
    assert self._deck is not None, "Deck not set"
    return self._deck

  @property
  def head(self) -> Dict[int, TipTracker]:
    assert self._head is not None, "Head not set"
    return self._head

  @property
  def head96(self) -> Optional[Dict[int, TipTracker]]:
    return self._head96

  async def setup(self):
    """Set up the robot. This method should be called before any other method is called."""
    assert self._deck is not None, "Deck not set"

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """The number of channels that the robot has."""

  @abstractmethod
  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """Pick up tips from the specified resource."""

  @abstractmethod
  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    """Drop tips from the specified resource."""

  @abstractmethod
  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    """Aspirate liquid from the specified resource using pip."""

  @abstractmethod
  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    """Dispense liquid from the specified resource using pip."""

  @abstractmethod
  async def pick_up_tips96(self, pickup: PickupTipRack):
    """Pick up tips from the specified resource using CoRe 96."""

  @abstractmethod
  async def drop_tips96(self, drop: DropTipRack):
    """Drop tips to the specified resource using CoRe 96."""

  @abstractmethod
  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    """Aspirate from all wells in 96 well plate."""

  @abstractmethod
  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    """Dispense to all wells in 96 well plate."""

  @abstractmethod
  async def pick_up_resource(self, pickup: ResourcePickup):
    """Pick up a resource like a plate or a lid using the integrated robotic arm."""

  @abstractmethod
  async def move_picked_up_resource(self, move: ResourceMove):
    """Move a picked up resource like a plate or a lid using the integrated robotic arm."""

  @abstractmethod
  async def drop_resource(self, drop: ResourceDrop):
    """Drop a resource like a plate or a lid using the integrated robotic arm."""

  async def prepare_for_manual_channel_operation(self, channel: int):
    """Prepare the robot for manual operation."""

    raise NotImplementedError()

  async def move_channel_x(self, channel: int, x: float):
    """Move the specified channel to the specified x coordinate."""

    raise NotImplementedError()

  async def move_channel_y(self, channel: int, y: float):
    """Move the specified channel to the specified y coordinate."""

    raise NotImplementedError()

  async def move_channel_z(self, channel: int, z: float):
    """Move the specified channel to the specified z coordinate."""

    raise NotImplementedError()

  @abstractmethod
  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Check if the tip can be picked up by the specified channel. Does not consider
    if a tip is already mounted - just whether the tip can be picked up by the channel.
    """

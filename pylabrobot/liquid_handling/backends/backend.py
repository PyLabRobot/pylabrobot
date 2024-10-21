from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List, Optional, Union

from pylabrobot.machines.backends import MachineBackend
from pylabrobot.resources import Deck, Resource
from pylabrobot.liquid_handling.standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  AspirationContainer,
  Dispense,
  DispensePlate,
  DispenseContainer,
  Move,
)


class LiquidHandlerBackend(MachineBackend, metaclass=ABCMeta):
  """
  Abstract base class for liquid handling robot backends.

  For more information on some methods and arguments, see the documentation for the
  :class:`~LiquidHandler` class.

  Attributes:
    setup_finished: Whether the backend has been set up.
  """

  def __init__(self):
    self.setup_finished = False
    self._deck: Optional[Deck] = None

  def set_deck(self, deck: Deck):
    """ Set the deck for the robot. Called automatically by `LiquidHandler.setup` or can be called
    manually if interacting with the backend directly. A deck must be set before setup. """
    self._deck = deck

  @property
  def deck(self) -> Deck:
    assert self._deck is not None, "Deck not set"
    return self._deck

  async def setup(self):
    """ Set up the robot. This method should be called before any other method is called. """
    assert self._deck is not None, "Deck not set"

  async def assigned_resource_callback(self, resource: Resource):
    """ Called when a new resource was assigned to the robot.

    This callback will also be called immediately after the setup method has been called for any
    resources that were assigned to the robot before it was set up. The first resource will always
    be the deck itself.

    Args:
      resource: The resource that was assigned to the robot.
    """

  async def unassigned_resource_callback(self, name: str):
    """ Called when a resource is unassigned from the robot.

    Args:
      resource: The name of the resource that was unassigned from the robot.
    """

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """ The number of channels that the robot has. """

  @abstractmethod
  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """ Pick up tips from the specified resource. """

  @abstractmethod
  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    """ Drop tips from the specified resource. """

  @abstractmethod
  async def aspirate(self, ops: List[Aspiration], use_channels: List[int]):
    """ Aspirate liquid from the specified resource using pip. """

  @abstractmethod
  async def dispense(self, ops: List[Dispense], use_channels: List[int]):
    """ Dispense liquid from the specified resource using pip. """

  @abstractmethod
  async def pick_up_tips96(self, pickup: PickupTipRack):
    """ Pick up tips from the specified resource using CoRe 96. """

  @abstractmethod
  async def drop_tips96(self, drop: DropTipRack):
    """ Drop tips to the specified resource using CoRe 96. """

  @abstractmethod
  async def aspirate96(self, aspiration: Union[AspirationPlate, AspirationContainer]):
    """ Aspirate from all wells in 96 well plate. """

  @abstractmethod
  async def dispense96(self, dispense: Union[DispensePlate, DispenseContainer]):
    """ Dispense to all wells in 96 well plate. """

  @abstractmethod
  async def move_resource(self, move: Move):
    """ Move a resource to a new location. """

  async def prepare_for_manual_channel_operation(self, channel: int):
    """ Prepare the robot for manual operation. """

    raise NotImplementedError()

  async def move_channel_x(self, channel: int, x: float):
    """ Move the specified channel to the specified x coordinate. """

    raise NotImplementedError()

  async def move_channel_y(self, channel: int, y: float):
    """ Move the specified channel to the specified y coordinate. """

    raise NotImplementedError()

  async def move_channel_z(self, channel: int, z: float):
    """ Move the specified channel to the specified z coordinate. """

    raise NotImplementedError()

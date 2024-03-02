from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List, Type, Optional

from pylabrobot.machine import MachineBackend
from pylabrobot.resources import Resource
from pylabrobot.liquid_handling.standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  Dispense,
  DispensePlate,
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
  async def aspirate96(self, aspiration: AspirationPlate):
    """ Aspirate from all wells in 96 well plate. """

  @abstractmethod
  async def dispense96(self, dispense: DispensePlate):
    """ Dispense to all wells in 96 well plate. """

  @abstractmethod
  async def move_resource(self, move: Move):
    """ Move a resource to a new location. """

  def serialize(self):
    """ Serialize the backend so that an equivalent backend can be created by passing the dict
    as kwargs to the initializer. The dict must contain a key "type" that specifies the type of
    backend to create. This key will be removed from the dict before passing it to the initializer.
    """

    return {
      "type": self.__class__.__name__,
    }

  @classmethod
  def deserialize(cls, data: dict) -> LiquidHandlerBackend:
    """ Deserialize the backend. Unless a custom serialization method is implemented, this method
    should not be overridden. """

    # Recursively find a subclass with the correct name
    def find_subclass(cls: Type[LiquidHandlerBackend], name: str) -> \
      Optional[Type[LiquidHandlerBackend]]:
      if cls.__name__ == name:
        return cls
      for subclass in cls.__subclasses__():
        subclass_ = find_subclass(subclass, name)
        if subclass_ is not None:
          return subclass_
      return None

    subclass = find_subclass(cls, data["type"])
    if subclass is None:
      raise ValueError(f"Could not find subclass with name {data['type']}")

    del data["type"]
    return subclass(**data)

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

from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.liquid_handling.resources import Resource, TipRack
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Drop,
  Aspiration,
  Dispense,
  Move,
)


class LiquidHandlerBackend(object, metaclass=ABCMeta):
  """
  Abstract base class for liquid handling robot backends.

  For more information on some methods and arguments, see the documentation for the
  :class:`~LiquidHandler` class.

  Attributes:
    setup_finished: Whether the backend has been set up.
  """

  def __init__(self):
    self.setup_finished = False

  def setup(self):
    self.setup_finished = True

  def stop(self):
    self.setup_finished = False

  def __enter__(self):
    self.setup()
    return self

  def __exit__(self, *exc):
    self.stop()
    return False

  def assigned_resource_callback(self, resource: Resource):
    """ Called when a new resource was assigned to the robot.

    This callback will also be called immediately after the setup method has been called for any
    resources that were assigned to the robot before it was set up.

    Args:
      resource: The resource that was assigned to the robot.
    """

  def unassigned_resource_callback(self, name: str):
    """ Called when a resource is unassigned from the robot.

    Args:
      resource: The name of the resource that was unassigned from the robot.
    """

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """ The number of channels that the robot has. """

  @abstractmethod
  def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """ Pick up tips from the specified resource. """

  @abstractmethod
  def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    """ Drop tips from the specified resource. """

  @abstractmethod
  def aspirate(self, ops: List[Aspiration], use_channels: List[int]):
    """ Aspirate liquid from the specified resource using pip. """

  @abstractmethod
  def dispense(self, ops: List[Dispense], use_channels: List[int]):
    """ Dispense liquid from the specified resource using pip. """

  @abstractmethod
  def pick_up_tips96(self, tip_rack: TipRack):
    """ Pick up tips from the specified resource using CoRe 96. """

  @abstractmethod
  def drop_tips96(self, tip_rack: TipRack):
    """ Drop tips to the specified resource using CoRe 96. """

  @abstractmethod
  def aspirate96(self, aspiration: Aspiration):
    """ Aspirate from all wells in 96 well plate. """

  @abstractmethod
  def dispense96(self, dispense: Dispense):
    """ Dispense to all wells in 96 well plate. """

  @abstractmethod
  def move_resource(self, move: Move):
    """ Move a resource to a new location. """

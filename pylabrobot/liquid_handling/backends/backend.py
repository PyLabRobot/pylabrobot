from abc import ABCMeta, abstractmethod
from typing import List, Union, Optional

from pylabrobot.liquid_handling.resources import (
  Coordinate,
  Plate,
  Resource,
  Lid,
  Tip,
)
from pylabrobot.liquid_handling.standard import (
  Aspiration,
  Dispense
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
    resources that were assigned to the robot before it was set up. Note that this callback
    will also be called for resources that were assigned but not unassigned to the robot before it
    was set up, but in those cases `unassigned_resource_callback` will be also be called.

    Args:
      resource: The resource that was assigned to the robot.
    """

    pass

  def unassigned_resource_callback(self, name: str):
    """ Called when a resource is unassigned from the robot.

    Args:
      resource: The name of the resource that was unassigned from the robot.
    """

    pass

  @abstractmethod
  def pickup_tips(self, *channels: List[Optional[Tip]], **backend_kwargs):
    """ Pick up tips from the specified resource. """
    pass

  @abstractmethod
  def discard_tips(self, *channels: List[Optional[Tip]], **backend_kwars):
    """ Discard tips from the specified resource. """
    pass

  @abstractmethod
  def aspirate(self, *channels: Optional[Aspiration], **backend_kwargs):
    """ Aspirate liquid from the specified resource using pip. """
    pass

  @abstractmethod
  def dispense(self, *channels: Optional[Dispense], **backend_kwargs):
    """ Dispense liquid from the specified resource using pip. """
    pass

  @abstractmethod
  def pickup_tips96(self, resource: Resource, **backend_kwargs):
    """ Pick up tips from the specified resource using CoRe 96. """
    pass

  @abstractmethod
  def discard_tips96(self, resource: Resource, **backend_kwargs):
    """ Discard tips to the specified resource using CoRe 96. """
    pass

  @abstractmethod
  def aspirate96(
    self,
    resource: Resource,
    pattern: List[List[bool]],
    volume: float,
    **backend_kwargs
  ):
    """ Aspirate liquid from the specified resource using CoRe 96. """
    pass

  @abstractmethod
  def dispense96(
    self,
    resource: Resource,
    pattern: List[List[bool]],
    volume: float,
    **backend_kwargs
  ):
    """ Dispense liquid to the specified resource using CoRe 96. """
    pass

  @abstractmethod
  def move_plate(self, plate: Plate, to: Union[Resource, Coordinate], **backend_kwargs):
    """ Move the specified plate within the robot. """
    pass

  @abstractmethod
  def move_lid(self, lid: Lid, to: Union[Resource, Coordinate], **backend_kwargs):
    """ Move the specified lid within the robot. """
    pass

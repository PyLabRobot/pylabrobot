from abc import ABCMeta, abstractmethod
import typing

from pyhamilton.liquid_handling.resources import Resource


class AspirationInfo: # TODO: real import
  pass
class DispenseInfo:
  pass


class LiquidHandlerBackend(object, metaclass=ABCMeta):
  """
  Abstract base class for liquid handling robot backends.

  For more information on some methods and arguments, see the documentation for the
  :class:`~LiquidHandler` class.

  Attributes:
    setup_finished: Whether the backend has been set up.
  """

  @abstractmethod
  def __init__(self):
    self.setup_finished = False

  @abstractmethod
  def setup(self):
    self.setup_finished = True

  @abstractmethod
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
  def pickup_tips(
    self,
    resource,
    channel_1: typing.Optional[str] = None,
    channel_2: typing.Optional[str] = None,
    channel_3: typing.Optional[str] = None,
    channel_4: typing.Optional[str] = None,
    channel_5: typing.Optional[str] = None,
    channel_6: typing.Optional[str] = None,
    channel_7: typing.Optional[str] = None,
    channel_8: typing.Optional[str] = None,
    **backend_kwargs
  ):
    """ Pick up tips from the specified resource. """
    pass

  @abstractmethod
  def discard_tips(
    self,
    resource,
    channel_1: typing.Optional[str] = None,
    channel_2: typing.Optional[str] = None,
    channel_3: typing.Optional[str] = None,
    channel_4: typing.Optional[str] = None,
    channel_5: typing.Optional[str] = None,
    channel_6: typing.Optional[str] = None,
    channel_7: typing.Optional[str] = None,
    channel_8: typing.Optional[str] = None,
    **backend_kwars
  ):
    """ Discard tips from the specified resource. """
    pass

  @abstractmethod
  def aspirate(
    self,
    resource: typing.Union[str, Resource],
    channel_1: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_2: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_3: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_4: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_5: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_6: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_7: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    channel_8: typing.Optional[typing.Union[tuple, dict, AspirationInfo]] = None,
    **backend_kwargs
  ):
    """ Aspirate liquid from the specified resource using pip. """
    pass

  @abstractmethod
  def dispense(
    self,
    resource: typing.Union[str, Resource],
    channel_1: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_2: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_3: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_4: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_5: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_6: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_7: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    channel_8: typing.Optional[typing.Union[tuple, dict, DispenseInfo]] = None,
    **backend_kwargs
  ):
    """ Dispense liquid from the specified resource using pip. """
    pass

  @abstractmethod
  def pickup_tips96(self, resource, **backend_kwargs):
    """ Pick up tips from the specified resource using CoRe 96. """
    pass

  @abstractmethod
  def discard_tips96(self, resource, **backend_kwargs):
    """ Discard tips to the specified resource using CoRe 96. """
    pass

  @abstractmethod
  def aspirate96(self, resource, pattern, volume, **backend_kwargs):
    """ Aspirate liquid from the specified resource using CoRe 96. """
    pass

  @abstractmethod
  def dispense96(self, resource, pattern, volume, **backend_kwargs):
    """ Dispense liquid to the specified resource using CoRe 96. """
    pass

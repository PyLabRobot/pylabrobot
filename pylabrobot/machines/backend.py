import contextlib
import inspect
import weakref
from typing import Optional

from pylabrobot.concurrency import AsyncExitStackWithShielding, AsyncResource, global_manager
from pylabrobot.serializer import SerializableMixin
from pylabrobot.utils.object_parsing import find_subclass


class MachineBackend(SerializableMixin, AsyncResource):
  """Abstract class for machine backends."""

  _instances: weakref.WeakSet["MachineBackend"] = weakref.WeakSet()

  def __init__(self):
    self._instances.add(self)
    self._stack: Optional[contextlib.AsyncExitStack] = None

  def __init_subclass__(cls, **kwargs):
    super().__init_subclass__(**kwargs)
    if "setup" in cls.__dict__:
      raise TypeError(f"Subclass {cls.__name__} is not allowed to override 'setup'")
    if "stop" in cls.__dict__:
      raise TypeError(f"Subclass {cls.__name__} is not allowed to override 'stop'")

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    pass

  async def setup(self):
    await global_manager.manage_context(self)

  async def stop(self):
    await global_manager.release_context(self)

  def serialize(self) -> dict:
    return {"type": self.__class__.__name__}

  @classmethod
  def deserialize(cls, data: dict):
    class_name = data.pop("type")
    subclass = find_subclass(class_name, cls=cls)
    if subclass is None:
      raise ValueError(f'Could not find subclass with name "{class_name}"')
    if inspect.isabstract(subclass):
      raise ValueError(f'Subclass with name "{class_name}" is abstract')
    assert issubclass(subclass, cls)
    return subclass(**data)

  @classmethod
  def get_all_instances(cls):
    return cls._instances

import inspect
import weakref
from abc import ABC, abstractmethod

from pylabrobot.utils.object_parsing import find_subclass


class MachineBackend(ABC):
  """Abstract class for machine backends."""

  _instances: weakref.WeakSet["MachineBackend"] = weakref.WeakSet()

  def __init__(self):
    self._instances.add(self)

  @abstractmethod
  async def setup(self):
    pass

  @abstractmethod
  async def stop(self):
    pass

  def serialize(self) -> dict:
    return {"type": self.__class__.__name__}

  @classmethod
  def deserialize(cls, data: dict):
    data = data.copy()
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

import inspect
import weakref
from abc import ABC

from pylabrobot.concurrency import HasLifespan
from pylabrobot.serializer import SerializableMixin
from pylabrobot.utils.object_parsing import find_subclass


class MachineBackend(SerializableMixin, HasLifespan, ABC, _defer_lifespan=True):
  """Base for machine backends that define their own lifespan extension."""

  _instances: weakref.WeakSet["MachineBackend"] = weakref.WeakSet()

  def __init__(self) -> None:
    super().__init__()
    self._instances.add(self)

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

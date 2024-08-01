from abc import ABC, ABCMeta, abstractmethod
from pylabrobot.utils.object_parsing import find_subclass

class MachineBackend(ABC):
  """ Abstract class for machine backends. """

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
    class_name = data.pop("type")
    subclass = find_subclass(class_name, cls=cls)
    if subclass is None:
      raise ValueError(f'Could not find subclass with name "{data["type"]}"')
    if issubclass(subclass, ABCMeta):
      raise ValueError(f'Subclass with name "{data["type"]}" is abstract')
    assert issubclass(subclass, cls)
    return subclass(**data)

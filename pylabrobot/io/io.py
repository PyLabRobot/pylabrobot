from abc import ABC, abstractmethod


class IOBase(ABC):
  @abstractmethod
  async def write(self, *args, **kwargs):
    pass

  @abstractmethod
  async def read(self, *args, **kwargs):
    pass

  def serialize(self):
    return {}

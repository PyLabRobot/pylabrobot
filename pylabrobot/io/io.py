from abc import ABC, abstractmethod


class IOBase(ABC):
  @abstractmethod
  async def write(self, data: bytes, *args, **kwargs):
    pass

  @abstractmethod
  async def read(self, *args, **kwargs) -> bytes:
    pass

  def serialize(self):
    return {}

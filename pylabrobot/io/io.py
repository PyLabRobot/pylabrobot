from abc import ABC, abstractmethod

from pylabrobot.serializer import SerializableMixin


class IOBase(SerializableMixin, ABC):
  @abstractmethod
  async def setup(self, *args, **kwargs):
    """Open the link. Called before any read or write."""

  @abstractmethod
  async def stop(self):
    """Close the link."""

  @abstractmethod
  async def write(self, data: bytes, *args, **kwargs):
    pass

  @abstractmethod
  async def read(self, *args, **kwargs) -> bytes:
    pass

  def serialize(self):
    return {}

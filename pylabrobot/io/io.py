from abc import abstractmethod

from pylabrobot.concurrency import AsyncExitStackWithShielding, AsyncResource
from pylabrobot.serializer import SerializableMixin


class IOBase(SerializableMixin, AsyncResource):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    pass

  @abstractmethod
  async def write(self, data: bytes, *args, **kwargs):
    pass

  @abstractmethod
  async def read(self, *args, **kwargs) -> bytes:
    pass

  def serialize(self):
    return {}

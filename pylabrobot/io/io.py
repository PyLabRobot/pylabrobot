import contextlib
from abc import ABC, abstractmethod

from pylabrobot.serializer import SerializableMixin
from pylabrobot.concurrency import AsyncResource


class IOBase(SerializableMixin, AsyncResource):
  async def _enter_lifespan(self, stack: contextlib.AsyncExitStack):
    pass

  @abstractmethod
  async def write(self, data: bytes, *args, **kwargs):
    pass

  @abstractmethod
  async def read(self, *args, **kwargs) -> bytes:
    pass

  def serialize(self):
    return {}

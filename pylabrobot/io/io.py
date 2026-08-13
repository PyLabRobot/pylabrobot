import asyncio
from abc import ABC, abstractmethod
from typing import TypeVar

from pylabrobot.serializer import SerializableMixin


T = TypeVar("T")


async def _wait_for_executor_future(future: "asyncio.Future[T]") -> T:
  """Wait for executor work to finish, ignoring cancellation of the awaiting task.

  Cancelling the asyncio wrapper returned by ``run_in_executor`` does not stop a callable that is
  already running. This keeps teardown from racing work that still owns a device handle.
  """

  async def wait() -> T:
    return await future

  waiter = asyncio.create_task(wait())
  while not waiter.done():
    try:
      await asyncio.shield(waiter)
    except asyncio.CancelledError:
      continue

  return waiter.result()


class IOBase(SerializableMixin, ABC):
  @abstractmethod
  async def write(self, data: bytes, *args, **kwargs):
    pass

  @abstractmethod
  async def read(self, *args, **kwargs) -> bytes:
    pass

  def serialize(self):
    return {}

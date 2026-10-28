import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict


class _FirmwareLock:
  """Lets one command at a time run on each module.

  A module executes one command at a time, so a second command to it has to wait until the first is
  answered. Commands to different modules run together.

  Read-only request (`R*`) and query (`Q*`) commands are not coordinated here at all: a module
  answers them while a command is in flight.
  """

  def __init__(self):
    self._locks: Dict[str, asyncio.Lock] = {}

  @asynccontextmanager
  async def module(self, module: str) -> AsyncIterator[None]:
    """Run a command on a module, once the one before it to the same module is done.

    Args:
      module: the module the command is addressed to, such as `A1PM`.
    """
    async with self._locks.setdefault(module, asyncio.Lock()):
      yield

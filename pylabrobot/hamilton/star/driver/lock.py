import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Tuple


class _FirmwareLock:
  """Coordinates firmware commands by the subsystem they drive.

  The module a command is addressed to is not what has to be serialized. The C0 master is a
  router: `C0 II` drives the autoload and `C0 DI` drives the channels, and the device runs
  those two together. What cannot overlap is two commands on one physical subsystem, whether they
  are addressed to it directly or through C0.

  So a command names the subsystem it drives and each subsystem has one mutex. A command that
  reaches wider than one subsystem names how much wider instead: `EVERY_SUBSYSTEM` runs alone,
  and `EVERY_SUBSYSTEM_BUT_THE_AUTOLOAD` leaves the autoload free to come up alongside.

  Read-only request (`R*`) and query (`Q*`) commands are not coordinated here at all: the master
  answers them while a command is in flight.
  """

  # The pipetting channels are the one subsystem that is not a single module: P1 to PG share a
  # mutex, because the device drives them as a set here.
  CHANNELS = "channels"
  AUTOLOAD = "I0"

  # Scopes a single command can hold, for the ones that are not addressed at a single subsystem.
  EVERY_SUBSYSTEM = "every subsystem"
  EVERY_SUBSYSTEM_BUT_THE_AUTOLOAD = "every subsystem but the autoload"

  _SUBSYSTEMS = ("C0", "X0", "H0", "D0", "R0", AUTOLOAD, CHANNELS)
  _WITHOUT_THE_AUTOLOAD = ("C0", "X0", "H0", "D0", "R0", CHANNELS)

  def __init__(self):
    self._locks = {name: asyncio.Lock() for name in self._SUBSYSTEMS}

  @classmethod
  def subsystem_of(cls, module: str) -> str:
    """The subsystem a command addressed to `module` drives, when it names no other.

    A module this does not know drives something unaccounted for, so it is taken to reach every
    subsystem and runs alone rather than running unlocked.

    Args:
      module: the module the command is addressed to.

    Returns:
      The subsystem key.
    """
    if module.startswith("P"):
      return cls.CHANNELS
    return module if module in cls._SUBSYSTEMS else cls.EVERY_SUBSYSTEM

  @asynccontextmanager
  async def subsystem(self, key: str):
    """Run a command on one subsystem, or on the wider scope it names.

    Args:
      key: the subsystem the command drives, or one of the wider scopes: `EVERY_SUBSYSTEM`, or
        `EVERY_SUBSYSTEM_BUT_THE_AUTOLOAD`.
    """
    names: Tuple[str, ...]
    if key == self.EVERY_SUBSYSTEM_BUT_THE_AUTOLOAD:
      names = self._WITHOUT_THE_AUTOLOAD
    else:
      resolved = key if key == self.EVERY_SUBSYSTEM else self.subsystem_of(key)
      names = self._SUBSYSTEMS if resolved == self.EVERY_SUBSYSTEM else (resolved,)
    async with AsyncExitStack() as stack:
      for name in names:  # always in declaration order, so two callers cannot deadlock
        await stack.enter_async_context(self._locks[name])
      yield

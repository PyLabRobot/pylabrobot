import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Tuple

# The letters a channel's module is addressed by, in order from the back.
CHANNEL_MODULE_LETTERS = "123456789ABCDEFG"
CHANNEL_MODULES: Tuple[str, ...] = tuple("P" + letter for letter in CHANNEL_MODULE_LETTERS)


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

  # Each channel module has its own mutex, so commands to different channels run together.
  # `CHANNELS` is every one of them: what a C0 command driving the channels takes.
  CHANNELS = "channels"
  AUTOLOAD = "I0"

  # Scopes a single command can hold, for the ones that are not addressed at a single subsystem.
  EVERY_SUBSYSTEM = "every subsystem"
  EVERY_SUBSYSTEM_BUT_THE_AUTOLOAD = "every subsystem but the autoload"

  _SUBSYSTEMS: Tuple[str, ...] = ("C0", "X0", "H0", "D0", "R0", AUTOLOAD) + CHANNEL_MODULES
  _WITHOUT_THE_AUTOLOAD: Tuple[str, ...] = ("C0", "X0", "H0", "D0", "R0") + CHANNEL_MODULES

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
    elif key == self.CHANNELS:
      names = CHANNEL_MODULES
    else:
      resolved = key if key == self.EVERY_SUBSYSTEM else self.subsystem_of(key)
      names = self._SUBSYSTEMS if resolved == self.EVERY_SUBSYSTEM else (resolved,)
    async with AsyncExitStack() as stack:
      for name in names:  # always in declaration order, so two callers cannot deadlock
        await stack.enter_async_context(self._locks[name])
      yield

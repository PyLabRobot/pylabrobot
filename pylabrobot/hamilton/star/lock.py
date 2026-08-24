import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)


class _FirmwareLock:
  """Coordinates firmware commands across modules.

  Two layers, both handled by ``slave_module(module)`` / ``c0()`` so callers never touch the
  internals:

  * A readers/writer gate between the C0 master and the slave modules. A slave-module
    command blocks the C0 master while in flight; a C0 master command (``c0()``) is
    *exclusive*: it waits for all slave-module commands to drain, then runs alone. So no
    slave-module command ever overlaps a C0 master command.

  * A per-module mutex so at most one command per module is in flight. Different modules
    still overlap — an X0 arm move can run alongside an H0 head move and Px channel commands
    — but you never have two X0, two H0, etc. at once. Modules without a dedicated mutex are
    gated but not per-module serialized.

  Read-only request (``R*``) and query (``Q*``) commands are not coordinated here at all —
  they take no lock and run fully in parallel.

  The first slave-module command acquires the exclusive lock and the last one releases it,
  so a C0 command simply takes the same lock and automatically waits the slaves out.
  """

  # Slave modules that serialize against themselves: H0 (96-head), X0 (X-drives),
  # R0 (iSWAP), I0 (autoload). All Px pipetting channels (P1..PG) share one mutex.
  _SERIALIZED_MODULES = ("H0", "X0", "R0", "I0")

  def __init__(self):
    # Per-module mutexes: at most one in-flight command per module.
    self._px_lock = asyncio.Lock()  # shared by all P1..PG pipetting channels
    self._module_locks = {module: asyncio.Lock() for module in self._SERIALIZED_MODULES}

    # Readers/writer gate: slave-module commands vs the exclusive C0 master.
    self._slave_count = 0
    self._slave_count_lock = asyncio.Lock()
    self._exclusive_lock = asyncio.Lock()

  def _module_lock(self, module: str) -> Optional[asyncio.Lock]:
    """The per-module mutex for ``module``, or None if it needs no per-module serialization."""
    if module.startswith("P"):
      return self._px_lock  # P1..PG pipetting channels
    return self._module_locks.get(module)

  @asynccontextmanager
  async def slave_module(self, module: str):
    """Run a slave-module command: serialize on its module mutex and block the C0 master."""
    module_lock = self._module_lock(module)
    async with AsyncExitStack() as stack:
      if module_lock is not None:
        await stack.enter_async_context(module_lock)
      # Join the slave group; first in acquires the exclusive lock, last out releases it.
      async with self._slave_count_lock:
        self._slave_count += 1
        if self._slave_count == 1:
          await self._exclusive_lock.acquire()
      try:
        yield
      finally:
        async with self._slave_count_lock:
          self._slave_count -= 1
          if self._slave_count == 0:
            self._exclusive_lock.release()

  @asynccontextmanager
  async def c0(self):
    """Run an exclusive C0 master command. Waits for all slave commands, then runs alone."""
    async with self._exclusive_lock:
      yield

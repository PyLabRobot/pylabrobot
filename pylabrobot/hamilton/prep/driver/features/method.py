"""Prep method lifecycle service.

Owns MLPrep method commands (``PrepMethodBegin`` / ``PrepMethodEnd`` / ``PrepMethodAbort``)
through the driver, and exposes an async context manager
(:meth:`MethodLifecycle.run`) that calls ``abort`` on exception and ``end`` on
clean exit — mirrors the ``CoreGrippers.mounted()`` pattern.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver


class MethodLifecycle:
  """Method begin/end/abort + ``async with`` safety net."""

  def __init__(self, driver: "PrepDriver"):
    self._driver = driver

  async def begin(self, automatic_pause: bool = False) -> None:
    """Signal the start of a liquid-handling method."""
    await self._driver.send_command(PrepCmd.PrepMethodBegin(automatic_pause=automatic_pause))

  async def end(self) -> None:
    """Signal the end of a liquid-handling method."""
    await self._driver.send_command(PrepCmd.PrepMethodEnd())

  async def abort(self) -> None:
    """Abort the current method."""
    await self._driver.send_command(PrepCmd.PrepMethodAbort())

  @asynccontextmanager
  async def run(self, automatic_pause: bool = False) -> AsyncIterator["MethodLifecycle"]:
    """Bracket a liquid-handling block with ``begin`` / ``end``; ``abort`` on exception.

    Example:
      async with prep.method.run():
        await prep.pipettes.pick_up_tips(...)
        await prep.pipettes.aspirate(...)
    """
    await self.begin(automatic_pause=automatic_pause)
    try:
      yield self
    except BaseException:
      await self.abort()
      raise
    else:
      await self.end()

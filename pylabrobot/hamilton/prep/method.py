"""Prep method lifecycle service.

Owns MLPrep method commands (``PrepMethodBegin`` / ``PrepMethodEnd`` / ``PrepMethodAbort``)
via ``PrepClient`` transport, and exposes an async context manager
(:meth:`PrepMethodLifecycle.run`) that calls ``abort`` on exception and ``end`` on
clean exit — mirrors the ``Prep.core_grippers()`` pattern in ``prep.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from . import prep_commands as PrepCmd

if TYPE_CHECKING:
  from .client import PrepClient


class PrepMethodLifecycle:
  """Method begin/end/abort + ``async with`` safety net."""

  def __init__(self, driver: "PrepClient"):
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
  async def run(self, automatic_pause: bool = False) -> AsyncIterator["PrepMethodLifecycle"]:
    """Bracket a liquid-handling block with ``begin`` / ``end``; ``abort`` on exception.

    Usage::

      async with prep.method.run():
        await prep.channels.pick_up_tips(...)
        await prep.channels.aspirate(...)
    """
    await self.begin(automatic_pause=automatic_pause)
    try:
      yield self
    except BaseException:
      await self.abort()
      raise
    else:
      await self.end()

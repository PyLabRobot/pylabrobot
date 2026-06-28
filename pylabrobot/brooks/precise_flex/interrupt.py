"""Notebook/asyncio interrupt handling: stop the in-flight operation, keep the connection.

When a user interrupts (Jupyter stop / Ctrl+C) while a command is awaiting a reply, we want to stop
whatever the device is doing - motion or vision alike - and resync the connection rather than drop
it. ``halt_on_interrupt`` wraps a blocking command: on interrupt it runs a channel-specific
stop+resync that is itself protected against a second interrupt, then re-raises an
``asyncio.CancelledError`` (preserving cancellation) or converts a ``KeyboardInterrupt`` to
``OperationInterrupted``.

Behaviour by context:
- Notebook: the kernel stays alive; the device is stopped and the connection resynced, so work
  continues in the same session.
- Script via ``asyncio.run``: shutdown cancels the task, so the stop still fires before the process
  exits (the connection survives only until the process ends).
- Physical E-stop is a hardware path, not an interrupt: the in-flight command returns an error reply
  (``PreciseFlexError``) or times out, which is not caught here and propagates normally.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional

from pylabrobot.io.socket import Socket

from .errors import OperationInterrupted

logger = logging.getLogger(__name__)


async def _run_to_completion(coro: Awaitable[None]) -> None:
  """Await ``coro`` to completion even if the surrounding task is interrupted again.

  Shields the work from cancellation and re-awaits if a second interrupt pops the await, so an
  interrupt-triggered stop can never be left half-sent (double Ctrl+C is common in notebooks).
  """
  task = asyncio.ensure_future(coro)
  while not task.done():
    try:
      await asyncio.shield(task)
    except (KeyboardInterrupt, asyncio.CancelledError):
      continue


async def drain(io: Socket, *, timeout: float = 0.5, max_lines: int = 50) -> None:
  """Read and discard pending lines until the socket goes quiet, resyncing the stream."""
  for _ in range(max_lines):
    try:
      if not await io.readline(timeout=timeout):
        return  # peer closed
    except TimeoutError:
      return  # quiet -> resynced


async def halt_and_resync(io: Socket, stop: Optional[bytes] = None) -> None:
  """Best-effort: optionally send a ``stop`` command, then drain to resync. Never closes.

  When ``stop`` is given, a leading newline first terminates any command frame that was only
  partially written when the interrupt landed, so the stop is parsed on its own line. Pass
  ``stop=None`` to drain-only - a channel with no known abort command, where we just resync and keep
  it open.
  """
  try:
    if stop is not None:
      await io.write(b"\n" + stop + b"\n")
    await drain(io)
  except Exception:  # best-effort; the connection is deliberately left open
    logger.warning("interrupt stop/resync I/O failed", exc_info=True)


@asynccontextmanager
async def halt_on_interrupt(stop_action: Callable[[], Awaitable[None]]):
  """Wrap a blocking command so an interrupt stops the in-flight operation and keeps the connection.

  On ``KeyboardInterrupt`` or ``asyncio.CancelledError``, runs ``stop_action`` to completion
  (protected against a second interrupt), then re-raises ``CancelledError`` or converts a
  ``KeyboardInterrupt`` to ``OperationInterrupted``. Any other exception (e.g. a ``PreciseFlexError``
  from an error reply, as an E-stop produces) propagates unchanged.
  """
  try:
    yield
  except (KeyboardInterrupt, asyncio.CancelledError) as exc:
    await _run_to_completion(stop_action())
    if isinstance(exc, asyncio.CancelledError):
      raise
    raise OperationInterrupted(
      "operation halted by interrupt; device stopped, connection alive"
    ) from exc

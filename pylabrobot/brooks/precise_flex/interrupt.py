"""Notebook interrupt handling: stop the in-flight operation, keep the connection.

When a user interrupts (Jupyter stop / Ctrl+C) while a command is awaiting a reply, we want to stop
whatever the device is doing - motion or vision alike - and resync the connection rather than drop
it. ``halt_on_interrupt`` wraps a blocking command: on interrupt it runs a channel-specific
stop+resync that is itself protected against a second interrupt, then re-raises the active backend's
cancellation (preserving cancellation) or converts a ``KeyboardInterrupt`` to
``OperationInterrupted``.

Behavior by context:
- Notebook: the kernel stays alive; the device is stopped and the connection resynced, so work
  continues in the same session.
- Script shutdown cancels the task, so the stop still fires before the process
  exits (the connection survives only until the process ends).
- Physical E-stop is a hardware path, not an interrupt: the in-flight command returns an error reply
  (``PreciseFlexError``) or times out, which is not caught here and propagates normally.
"""

import logging
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional

import anyio

from pylabrobot.io.socket import Socket

from .errors import OperationInterrupted

logger = logging.getLogger(__name__)


async def _run_to_completion(coro: Awaitable[None]) -> None:
  """Await ``coro`` to completion despite surrounding task cancellation."""
  with anyio.CancelScope(shield=True):
    await coro


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

  On ``KeyboardInterrupt`` or backend cancellation, runs ``stop_action`` to completion, then
  re-raises cancellation or converts a ``KeyboardInterrupt`` to ``OperationInterrupted``. Any other
  exception (e.g. a ``PreciseFlexError`` from an error reply, as an E-stop produces) propagates
  unchanged.
  """
  try:
    yield
  except (KeyboardInterrupt, anyio.get_cancelled_exc_class()) as exc:
    await _run_to_completion(stop_action())
    if isinstance(exc, anyio.get_cancelled_exc_class()):
      raise
    raise OperationInterrupted(
      "operation halted by interrupt; device stopped, connection alive"
    ) from exc

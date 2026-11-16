"""Bounded, session-scoped asynchronous event subscriptions."""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import AsyncIterator, Callable, Literal, Optional

from pylabrobot.hamilton.transport.tcp.messages import CommandResponse

OverflowPolicy = Literal["error", "drop_oldest", "drop_newest"]


class EventOverflowError(RuntimeError):
  """A subscriber could not keep up with the instrument's events."""


class EventSubscription(AsyncIterator[CommandResponse]):
  """Consume events in arrival order on the connection's event loop.

  Use ``async with client.events() as events`` followed by ``async for event in
  events``. The default overflow policy closes the subscription with an error.
  Explicit drop policies count discarded events in ``dropped``. Stopping the
  session discards queued events and ends iteration; connection failure raises
  its error. Each subscription supports one consuming task at a time.
  """

  def __init__(
    self,
    capacity: int,
    overflow: OverflowPolicy,
    on_close: Callable[[EventSubscription], None],
  ):
    """Allocate a bounded queue and validate the selected overflow policy."""
    if capacity < 1:
      raise ValueError("Event capacity must be positive")
    if overflow not in ("error", "drop_oldest", "drop_newest"):
      raise ValueError(f"Unknown event overflow policy: {overflow}")
    self._queue: asyncio.Queue[Optional[CommandResponse]] = asyncio.Queue(capacity)
    self._overflow = overflow
    self._on_close = on_close
    self._closed = False
    self._error: Optional[Exception] = None
    self._consuming = False
    self._dropped = 0

  @property
  def dropped(self) -> int:
    """Number of events discarded due to overflow."""
    return self._dropped

  def _publish(self, event: CommandResponse) -> None:
    """Deliver without blocking the protocol reader."""
    if self._closed:
      return
    if self._queue.full():
      self._dropped += 1
      if self._overflow == "error":
        self._finish(EventOverflowError("Hamilton TCP event subscription overflowed"))
        return
      if self._overflow == "drop_newest":
        return
      self._queue.get_nowait()
    self._queue.put_nowait(event)

  def _finish(self, error: Optional[Exception] = None) -> None:
    """Close once, discard buffered events, and wake a blocked consumer."""
    if self._closed:
      return
    self._closed = True
    self._error = error
    self._on_close(self)
    while not self._queue.empty():
      self._queue.get_nowait()
    self._queue.put_nowait(None)

  async def __anext__(self) -> CommandResponse:
    """Wait for an event, session termination, or explicit subscription closure."""
    if self._consuming:
      raise RuntimeError("Event subscriptions support one consumer at a time")
    self._consuming = True
    try:
      event = None if self._closed else await self._queue.get()
      if event is None:
        if self._error is not None:
          raise self._error
        raise StopAsyncIteration
      return event
    finally:
      self._consuming = False

  async def aclose(self) -> None:
    """Unsubscribe and end pending iteration."""
    self._finish()

  async def __aenter__(self) -> EventSubscription:
    """Return the subscription for scoped consumption."""
    return self

  async def __aexit__(
    self,
    exc_type: Optional[type[BaseException]],
    exc: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> None:
    """Release the subscription when its scope ends."""
    await self.aclose()

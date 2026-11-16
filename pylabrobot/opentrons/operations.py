"""Serialize complete robot operations, including nested calls from one task."""

import asyncio
from functools import wraps
from typing import Any, Callable, Coroutine, Optional, Protocol, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


class OperationLock:
  """A task-owned reentrant lock for multi-command robot operations."""

  def __init__(self) -> None:
    self._lock = asyncio.Lock()
    self._owner: Optional[asyncio.Task] = None
    self._depth = 0

  async def __aenter__(self) -> "OperationLock":
    task = asyncio.current_task()
    if task is not self._owner:
      await self._lock.acquire()
      self._owner = task
    self._depth += 1
    return self

  async def __aexit__(self, exc_type, exc, traceback) -> None:
    self._depth -= 1
    if self._depth == 0:
      self._owner = None
      self._lock.release()


class OperationOwner(Protocol):
  """An object participating in a robot's operation lock."""

  @property
  def _operation_lock(self) -> OperationLock: ...


class RunBoundInstrument(OperationOwner, Protocol):
  """An instrument whose lifetime is bounded by its creating run."""

  def _require_active(self) -> None: ...


def serialized(method: F) -> F:
  """Hold the robot's lock through a complete public operation."""

  @wraps(method)
  async def wrapped(self: OperationOwner, *args: Any, **kwargs: Any) -> Any:
    async with self._operation_lock:
      return await method(self, *args, **kwargs)

  return cast(F, wrapped)


def instrument_operation(method: F) -> F:
  """Serialize an instrument operation and reject stale objects before work."""

  @serialized
  @wraps(method)
  async def wrapped(self: RunBoundInstrument, *args: Any, **kwargs: Any) -> Any:
    self._require_active()
    return await method(self, *args, **kwargs)

  return cast(F, wrapped)

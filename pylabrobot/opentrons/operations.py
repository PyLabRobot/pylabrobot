"""Serialize complete robot operations, including nested calls from one task."""

from functools import wraps
from typing import Any, Callable, Coroutine, Protocol, TypeVar, cast

from pylabrobot.opentrons.run import RobotOperationLock

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])

OperationLock = RobotOperationLock


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

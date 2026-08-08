"""Opt-in structured events for PLR execution and state transitions.

The bus is deliberately synchronous and in-process. Subscribers should enqueue or persist
quickly; they must not block or alter hardware control flow.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import logging
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PLREvent:
  """One structured PLR event emitted at a state or command boundary."""

  sequence: int
  name: str
  timestamp: dt.datetime
  context: Dict[str, Any]
  data: Dict[str, Any]

  def as_dict(self) -> Dict[str, Any]:
    """Return a JSONL-friendly representation of the event."""
    return {
      "sequence": self.sequence,
      "name": self.name,
      "timestamp": self.timestamp.isoformat(),
      "context": self.context.copy(),
      "data": self.data.copy(),
    }


EventListener = Callable[[PLREvent], None]
OperationContextFactory = Callable[..., Dict[str, Any]]


class EventBus:
  """In-process event fan-out that never lets observer failures affect PLR control flow."""

  def __init__(self):
    self._listeners: List[EventListener] = []
    self._lock = threading.Lock()
    self._sequence = 0

  def subscribe(self, listener: EventListener) -> Callable[[], None]:
    """Register a listener and return an unsubscribe callback."""
    with self._lock:
      self._listeners.append(listener)

    def unsubscribe() -> None:
      with self._lock:
        if listener in self._listeners:
          self._listeners.remove(listener)

    return unsubscribe

  @property
  def has_listeners(self) -> bool:
    with self._lock:
      return bool(self._listeners)

  def emit(self, name: str, *, context: Optional[Dict[str, Any]] = None, **data: Any) -> PLREvent:
    """Publish an event to a stable listener snapshot."""
    with self._lock:
      self._sequence += 1
      event = PLREvent(
        sequence=self._sequence,
        name=name,
        timestamp=dt.datetime.now(dt.timezone.utc),
        context=(context or {}).copy(),
        data=data.copy(),
      )
      listeners = self._listeners.copy()

    for listener in listeners:
      try:
        listener(event)
      except Exception:
        logger.exception("PLR event listener failed while handling %s", name)
    return event


_default_event_bus: Optional[EventBus] = None
_active_event_bus: contextvars.ContextVar[Optional[EventBus]] = contextvars.ContextVar(
  "pylabrobot_active_event_bus", default=None
)
_event_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
  "pylabrobot_event_context", default={}
)


def get_default_event_bus() -> Optional[EventBus]:
  """Return the process-wide fallback event bus, if one is installed."""
  return _default_event_bus


def set_default_event_bus(event_bus: Optional[EventBus]) -> Optional[EventBus]:
  """Install or clear the process-wide event bus and return the previous one."""
  global _default_event_bus
  previous_event_bus = _default_event_bus
  _default_event_bus = event_bus
  return previous_event_bus


def get_event_bus() -> Optional[EventBus]:
  """Return the scoped event bus, falling back to the process-wide bus."""
  return _active_event_bus.get() or _default_event_bus


def is_event_bus_active() -> bool:
  """Whether the current execution context has an interested event subscriber."""
  event_bus = get_event_bus()
  return event_bus is not None and event_bus.has_listeners


@contextmanager
def use_event_bus(event_bus: EventBus) -> Iterator[EventBus]:
  """Temporarily install an event bus for the current async/task context."""
  token = _active_event_bus.set(event_bus)
  try:
    yield event_bus
  finally:
    _active_event_bus.reset(token)


@contextmanager
def event_context(**values: Any) -> Iterator[None]:
  """Attach context to all nested events in the current execution context."""
  context = _event_context.get().copy()
  context.update({key: value for key, value in values.items() if value is not None})
  token = _event_context.set(context)
  try:
    yield
  finally:
    _event_context.reset(token)


def emit_event(name: str, **data: Any) -> Optional[PLREvent]:
  """Emit an event if an event bus with listeners is installed."""
  event_bus = get_event_bus()
  if event_bus is None or not event_bus.has_listeners:
    return None
  return event_bus.emit(name, context=_event_context.get(), **data)


def resource_reference(resource: Any) -> Optional[Dict[str, Any]]:
  """Return a compact resource identity and its assigned-resource ancestry.

  The reference always identifies the resource directly involved in an operation. Its
  ancestry is structural context only: consumers can, for example, present a well on
  the lane for its owning plate without replacing the well in the event payload.
  """
  if resource is None:
    return None
  result: Dict[str, Any] = {
    "name": getattr(resource, "name", None),
    "type": type(resource).__name__,
  }
  model = getattr(resource, "model", None)
  if model is not None:
    result["model"] = str(model)
  rotation = getattr(resource, "rotation", None)
  if rotation is not None:
    result["rotation"] = {
      "x": rotation.x,
      "y": rotation.y,
      "z": rotation.z,
    }
  ancestors = []
  current = getattr(resource, "parent", None)
  while current is not None:
    ancestor: Dict[str, Any] = {
      "name": getattr(current, "name", None),
      "type": type(current).__name__,
    }
    ancestor_model = getattr(current, "model", None)
    if ancestor_model is not None:
      ancestor["model"] = str(ancestor_model)
    ancestors.append(ancestor)
    current = getattr(current, "parent", None)
  if ancestors:
    result["ancestors"] = ancestors
  return result


def coordinate_reference(coordinate: Any) -> Optional[Dict[str, float]]:
  """Return a JSON-friendly coordinate, or ``None`` for an unlocated resource."""
  if coordinate is None:
    return None
  return {"x": coordinate.x, "y": coordinate.y, "z": coordinate.z}


def evented_operation(
  name: str, context_factory: OperationContextFactory
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
  """Decorate an async frontend call with correlated lifecycle events.

  The wrapper is a no-op when no listener is installed, preserving normal PLR performance and
  behaviour. Nested resource and transport events inherit the generated operation context.
  """

  def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
      if not is_event_bus_active():
        return await func(*args, **kwargs)

      operation_data = context_factory(*args, **kwargs)
      operation_id = uuid.uuid4().hex
      with event_context(operation=name, operation_id=operation_id, **operation_data):
        emit_event(f"{name}.started", **operation_data)
        try:
          result = await func(*args, **kwargs)
        except BaseException as error:
          emit_event(
            f"{name}.failed",
            **operation_data,
            error_type=type(error).__name__,
            error_message=str(error),
          )
          raise
        emit_event(f"{name}.completed", **operation_data)
        return result

    return wrapper

  return decorator

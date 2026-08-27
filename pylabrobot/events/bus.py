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
from typing import (
  TYPE_CHECKING,
  Any,
  Awaitable,
  Callable,
  ContextManager,
  Coroutine,
  Dict,
  Iterator,
  List,
  Optional,
  TypedDict,
  cast,
)

if TYPE_CHECKING:
  from pylabrobot.resources.coordinate import Coordinate
  from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)


class DeviceReference(TypedDict):
  """Serialized identity for a device frontend that is not necessarily a PLR resource."""

  name: str
  type: str


class RotationReference(TypedDict):
  """Serialized resource rotation."""

  x: float
  y: float
  z: float


class ResourceIdentityReference(TypedDict):
  """Fields shared by direct resources and their ancestors."""

  name: str
  type: str


class ResourceAncestorReference(ResourceIdentityReference, total=False):
  """Compact identity for one resource ancestor."""

  model: str


class ResourceReference(ResourceIdentityReference, total=False):
  """Compact identity and structural context for a PLR resource."""

  model: str
  rotation: RotationReference
  ancestors: List[ResourceAncestorReference]


class CoordinateReference(TypedDict):
  """Serialized PLR coordinate."""

  x: float
  y: float
  z: float
  type: str


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


@dataclass(frozen=True)
class LifespanEventInfo:
  """Names and payloads for a resource's separate entry and exit transitions."""

  entry: str
  exit: str
  entry_data: Dict[str, Any]
  exit_data: Optional[Dict[str, Any]] = None


EventListener = Callable[[PLREvent], None]
OperationContextFactory = Callable[..., Dict[str, Any]]
CompletionDataFactory = Callable[[], Dict[str, Any]]


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


def device_reference(device: object, *, name: str) -> DeviceReference:
  """Return the explicit identity of a device frontend that is not a PLR resource."""

  return {"name": name, "type": type(device).__name__}


def resource_reference(resource: Optional["Resource"]) -> Optional[ResourceReference]:
  """Return a compact resource identity and its assigned-resource ancestry.

  The reference always identifies the resource directly involved in an operation. Its
  ancestry is structural context only: consumers can, for example, present a well on
  the lane for its owning plate without replacing the well in the event payload.
  """
  if resource is None:
    return None
  result: ResourceReference = {
    "name": resource.name,
    "type": type(resource).__name__,
  }
  if resource.model is not None:
    result["model"] = resource.model
  result["rotation"] = {
    "x": resource.rotation.x,
    "y": resource.rotation.y,
    "z": resource.rotation.z,
  }
  ancestors: List[ResourceAncestorReference] = []
  current = resource.parent
  while current is not None:
    ancestor: ResourceAncestorReference = {
      "name": current.name,
      "type": type(current).__name__,
    }
    if current.model is not None:
      ancestor["model"] = current.model
    ancestors.append(ancestor)
    current = current.parent
  if ancestors:
    result["ancestors"] = ancestors
  return result


def coordinate_reference(coordinate: Optional["Coordinate"]) -> Optional[CoordinateReference]:
  """Return PLR's serialized coordinate representation, or ``None`` when absent.

  ``Coordinate`` already has a stable serialization contract. Reusing it preserves the
  explicit ``type`` discriminator that consumers need to distinguish geometric targets
  from generic ``x``/``y``/``z`` mappings.
  """
  if coordinate is None:
    return None
  return cast(CoordinateReference, coordinate.serialize())


@contextmanager
def event_operation(
  name: str,
  *,
  completed_data_factory: CompletionDataFactory | None = None,
  **operation_data: Any,
) -> Iterator[None]:
  """Emit correlated lifecycle events around a semantic operation block.

  This is the block-oriented counterpart to :func:`evented_operation`, for
  integrations whose operation spans several frontend calls rather than one
  decorated method. ``completed_data_factory`` can capture the final resource
  state after a successful operation; the started event always describes the
  invocation state.
  """

  if not is_event_bus_active():
    yield
    return

  operation_id = uuid.uuid4().hex
  with event_context(operation=name, operation_id=operation_id, **operation_data):
    emit_event(f"{name}.started", **operation_data)
    try:
      yield
    except BaseException as error:
      emit_event(
        f"{name}.failed",
        **operation_data,
        error_type=type(error).__name__,
        error_message=str(error),
      )
      raise
    completed_data = (
      completed_data_factory() if completed_data_factory is not None else operation_data
    )
    emit_event(f"{name}.completed", **completed_data)


class _LifespanEventScope:
  """Mark when a resource is active without owning its asynchronous lifespan."""

  def __init__(self, info: Optional[LifespanEventInfo], enabled: bool) -> None:
    self.info = info
    self.enabled = enabled
    self.active = False
    self.body_error: Optional[BaseException] = None
    self.entry_token: Optional[contextvars.Token[Dict[str, Any]]] = None
    self.exit_token: Optional[contextvars.Token[Dict[str, Any]]] = None

  @property
  def lifespan_active(self) -> ContextManager[None]:
    """Return the scope indicating successful entry through the active body."""
    return self._active_scope()

  @contextmanager
  def _active_scope(self) -> Iterator[None]:
    """Indicate successful entry and start exit when the active block ends."""
    self.active = True
    if self.enabled:
      assert self.info is not None and self.entry_token is not None
      emit_event(f"{self.info.entry}.completed", **self.info.entry_data)
      _event_context.reset(self.entry_token)
      self.entry_token = None
    try:
      yield
    except BaseException as error:
      self.body_error = error
      raise
    finally:
      if self.enabled:
        assert self.info is not None
        data = self.info.exit_data if self.info.exit_data is not None else self.info.entry_data
        self.exit_token = _event_context.set(
          {
            **_event_context.get(),
            "operation": self.info.exit,
            "operation_id": uuid.uuid4().hex,
            **data,
          }
        )
        emit_event(f"{self.info.exit}.started", **data)


@contextmanager
def lifespan_events(info: Optional[LifespanEventInfo]) -> Iterator[_LifespanEventScope]:
  """Observe entry and exit around a nested lifespan and its active-body scope.

  Place the owned lifespan inside this scope and its active body inside
  ``lifespan_active``. Entry and exit failures are then attributed to their
  respective transitions without manually entering or exiting the lifespan.
  """
  scope = _LifespanEventScope(info, info is not None and is_event_bus_active())
  if scope.enabled:
    assert info is not None
    scope.entry_token = _event_context.set(
      {
        **_event_context.get(),
        "operation": info.entry,
        "operation_id": uuid.uuid4().hex,
        **info.entry_data,
      }
    )
    emit_event(f"{info.entry}.started", **info.entry_data)
  try:
    yield scope
  except BaseException as error:
    if scope.enabled:
      assert info is not None
      if scope.active:
        data = info.exit_data if info.exit_data is not None else info.entry_data
        if error is scope.body_error:
          emit_event(f"{info.exit}.completed", **data)
        else:
          emit_event(
            f"{info.exit}.failed", **data, error_type=type(error).__name__, error_message=str(error)
          )
      else:
        emit_event(
          f"{info.entry}.failed",
          **info.entry_data,
          error_type=type(error).__name__,
          error_message=str(error),
        )
    raise
  else:
    if scope.enabled:
      assert info is not None
      if scope.active:
        data = info.exit_data if info.exit_data is not None else info.entry_data
        emit_event(f"{info.exit}.completed", **data)
  finally:
    if scope.exit_token is not None:
      _event_context.reset(scope.exit_token)
    if scope.entry_token is not None:
      _event_context.reset(scope.entry_token)


def evented_operation(
  name: str, context_factory: OperationContextFactory
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Coroutine[Any, Any, Any]]]:
  """Decorate an async frontend call with correlated lifecycle events.

  The wrapper is a no-op when no listener is installed, preserving normal PLR performance and
  behaviour. Nested resource and transport events inherit the generated operation context.

  This helper deliberately forwards the decorated method's original ``*args`` and ``**kwargs``
  directly to ``context_factory``. Use it only when the event context is a simple projection of
  invocation arguments and pre-operation resource state, and keep the factory's calling signature
  aligned with the decorated method. For context that depends on validation, normalization,
  derived values, hardware responses, or final state, construct an :func:`event_operation`
  explicitly inside the method instead.
  """

  def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
      if not is_event_bus_active():
        return await func(*args, **kwargs)

      operation_data = context_factory(*args, **kwargs)
      with event_operation(name, **operation_data):
        return await func(*args, **kwargs)

    return wrapper

  return decorator

"""Structured concurrency primitives for asynchronous lifespans."""

import asyncio
import inspect
import sys
import warnings
from abc import ABCMeta
from contextlib import AsyncExitStack, asynccontextmanager
from enum import Enum, auto
from types import MethodType, TracebackType
from typing import (
  Any,
  AsyncContextManager,
  AsyncIterator,
  Awaitable,
  Callable,
  ContextManager,
  Dict,
  Generic,
  Optional,
  Set,
  Tuple,
  Type,
  TypeVar,
  cast,
)

import anyio
import sniffio
from typing_extensions import ParamSpec

from pylabrobot.events import LifespanEventInfo, lifespan_events

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup, ExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup, ExceptionGroup

_P = ParamSpec("_P")
_T = TypeVar("_T")
_ExceptionType = Optional[Type[BaseException]]
_SyncExit = Callable[..., Any]
_AsyncExit = Callable[..., Awaitable[Any]]


def _failure_is_reachable(start: BaseException, target: BaseException) -> bool:
  """Report whether one failure already appears in another failure's context chain.

  Args:
    start: Failure whose exception context chain is walked.
    target: Failure to look for in that chain.

  Returns:
    True when target is reachable from start.
  """
  seen: Set[int] = set()
  current: Optional[BaseException] = start
  while current is not None and id(current) not in seen:
    if current is target:
      return True
    seen.add(id(current))
    current = current.__context__
  return False


def _link_cleanup_failure(error: BaseException, exception: Optional[BaseException]) -> None:
  """Keep the failure a cleanup unwinds reachable from the cleanup's own failure.

  The exit stack repairs this chain itself whenever the lifespan body failed. It cannot
  repair it after a successful body, because there is no in-flight failure to chain onto.

  Args:
    error: Failure raised by the cleanup operation.
    exception: Failure the cleanup was unwinding, if any.
  """
  if exception is None or exception is error or error.__context__ is not None:
    return
  if _failure_is_reachable(exception, error):
    return
  error.__context__ = exception


def _linked_sync_exit(exit_operation: _SyncExit) -> _SyncExit:
  """Wrap a synchronous exit so its failure keeps the failure it unwinds reachable.

  Args:
    exit_operation: Synchronous cleanup with `__exit__` calling conventions.

  Returns:
    An equivalent exit that links its own failure to the failure it unwinds.
  """

  def linked_exit(
    exception_type: _ExceptionType,
    exception: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> Any:
    """Run the wrapped exit, linking any failure it raises.

    Args:
      exception_type: Type of the failure this cleanup unwinds, if any.
      exception: Failure this cleanup unwinds, if any.
      traceback: Traceback of the failure this cleanup unwinds, if any.

    Returns:
      Whatever the wrapped exit returns, preserving its suppression decision.

    Raises:
      BaseException: If the wrapped exit fails.
    """
    try:
      return exit_operation(exception_type, exception, traceback)
    except BaseException as error:
      _link_cleanup_failure(error, exception)
      raise

  return linked_exit


def _linked_async_exit(exit_operation: _AsyncExit) -> _AsyncExit:
  """Wrap an asynchronous exit so its failure keeps the failure it unwinds reachable.

  Args:
    exit_operation: Asynchronous cleanup with `__aexit__` calling conventions.

  Returns:
    An equivalent exit that links its own failure to the failure it unwinds.
  """

  async def linked_exit(
    exception_type: _ExceptionType,
    exception: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> Any:
    """Run the wrapped exit, linking any failure it raises.

    Args:
      exception_type: Type of the failure this cleanup unwinds, if any.
      exception: Failure this cleanup unwinds, if any.
      traceback: Traceback of the failure this cleanup unwinds, if any.

    Returns:
      Whatever the wrapped exit returns, preserving its suppression decision.

    Raises:
      BaseException: If the wrapped exit fails.
    """
    try:
      return await exit_operation(exception_type, exception, traceback)
    except BaseException as error:
      _link_cleanup_failure(error, exception)
      raise

  return linked_exit


class _TaskGroupBodyFailure(BaseException):
  """Unique scalar substituted for a task-group body's failure during exit."""


def _restore_task_group_body_failure(
  error: BaseException,
  marker: _TaskGroupBodyFailure,
  body_failure: BaseException,
) -> Tuple[BaseException, bool]:
  """Replace a boundary marker in an AnyIO task-group failure tree.

  Args:
    error: Failure raised by the AnyIO task-group boundary.
    marker: Unique marker passed to that boundary.
    body_failure: Original failure raised by the managed body.

  Returns:
    The restored failure tree and whether the marker was found.
  """
  if error is marker:
    return body_failure, True
  if not isinstance(error, BaseExceptionGroup):
    return error, False

  marker_found = False
  restored: list[BaseException] = []
  for member in error.exceptions:
    restored_member, member_contains_marker = _restore_task_group_body_failure(
      member, marker, body_failure
    )
    restored.append(restored_member)
    marker_found = marker_found or member_contains_marker
  if not marker_found:
    return error, False
  derived = error.derive(tuple(restored)).with_traceback(error.__traceback__)
  derived.__cause__ = error.__cause__
  derived.__suppress_context__ = error.__suppress_context__
  derived.__context__ = error.__context__
  try:
    notes = error.__notes__
  except AttributeError:
    pass
  else:
    derived.__notes__ = list(notes)
  return derived, True


class _TaskGroupBoundary(Generic[_T]):
  """Enter a task-group-owning context without changing managed body failure identity.

  An AnyIO task group reports its body's failure as a member of an exception group. So
  does every context manager that exits through one, such as a blocking portal. This
  boundary substitutes a unique marker for the body's failure, then restores the failure
  in place of that marker, so callers see the failure they raised rather than a group.
  """

  def __init__(self, context: AsyncContextManager[_T]) -> None:
    """Own the task-group-backed context this boundary delimits."""
    self._context = context

  async def __aenter__(self) -> _T:
    """Enter and return whatever the owned context provides."""
    return await self._context.__aenter__()

  async def __aexit__(
    self,
    exception_type: _ExceptionType,
    exception: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> Any:
    """Exit while distinguishing this boundary's synthetic group from real groups."""
    if exception is None or isinstance(exception, anyio.get_cancelled_exc_class()):
      return await self._context.__aexit__(exception_type, exception, traceback)

    marker = _TaskGroupBodyFailure()
    restored_error: Optional[BaseException] = None
    try:
      result = await self._context.__aexit__(type(marker), marker, traceback)
    except BaseException as error:
      if isinstance(error, BaseExceptionGroup) and error.exceptions == (marker,):
        return False
      restored_error, marker_found = _restore_task_group_body_failure(error, marker, exception)
      if not marker_found:
        raise
    else:
      return result

    raise restored_error


class AsyncExitStackWithShielding(AsyncExitStack):
  """An async exit stack with cancellation-shielded callback registration.

  Cleanup runs in reverse registration order, and every registered cleanup is attempted
  even after an earlier one fails. When several cleanups fail, the caller receives the
  last failure and reaches the earlier ones through its exception context chain.

  The low-level `push()` and `push_async_exit()` registrations keep plain
  `contextlib.AsyncExitStack` semantics, where an earlier cleanup failure is reachable
  only when the stack body itself failed.
  """

  def enter_context(self, cm: ContextManager[_T]) -> _T:
    """Enter a synchronous context manager for the remainder of this stack.

    Args:
      cm: Synchronous context manager whose exit this stack owns.

    Returns:
      The value produced by entering the context manager.

    Raises:
      TypeError: If the object does not support the context manager protocol.
    """
    cls = type(cm)
    try:
      enter = cls.__enter__
      exit_operation = cls.__exit__
    except AttributeError:
      raise TypeError(
        f"'{cls.__module__}.{cls.__qualname__}' object does not support the "
        "context manager protocol"
      ) from None
    result = enter(cm)
    self.push(_linked_sync_exit(MethodType(exit_operation, cm)))
    return result

  async def enter_async_context(self, cm: AsyncContextManager[_T]) -> _T:
    """Enter an asynchronous context manager for the remainder of this stack.

    Args:
      cm: Asynchronous context manager whose exit this stack owns.

    Returns:
      The value produced by entering the context manager.

    Raises:
      TypeError: If the object does not support the asynchronous context manager protocol.
    """
    cls = type(cm)
    try:
      enter = cls.__aenter__
      exit_operation = cls.__aexit__
    except AttributeError:
      raise TypeError(
        f"'{cls.__module__}.{cls.__qualname__}' object does not support the "
        "asynchronous context manager protocol"
      ) from None
    result = await enter(cm)
    self.push_async_exit(_linked_async_exit(MethodType(exit_operation, cm)))
    return result

  async def enter_task_group(self) -> anyio.abc.TaskGroup:
    """Enter an AnyIO task group that preserves the managed body's failures.

    Returns:
      The task group owned by this exit stack.
    """
    return await self.enter_task_group_context(anyio.create_task_group())

  async def enter_task_group_context(self, cm: AsyncContextManager[_T]) -> _T:
    """Enter a context manager that exits through a task group of its own.

    Such a context reports the managed body's failure as a member of an exception group.
    Entering it here keeps that failure's identity instead, exactly as `enter_task_group`
    does for a bare task group.

    Args:
      cm: Asynchronous context manager that owns an AnyIO task group.

    Returns:
      Whatever the context manager provides on entry.
    """
    return await self.enter_async_context(_TaskGroupBoundary(cm))

  def callback(
    self, callback: Callable[_P, _T], /, *args: _P.args, **kwds: _P.kwargs
  ) -> Callable[_P, _T]:
    """Register a synchronous cleanup call.

    Args:
      callback: Synchronous cleanup operation to call.
      *args: Positional arguments forwarded to the callback.
      **kwds: Keyword arguments forwarded to the callback.

    Returns:
      The callback, so this method can be used as a decorator.
    """

    def callback_exit(
      exception_type: _ExceptionType,
      exception: Optional[BaseException],
      traceback: Optional[TracebackType],
    ) -> None:
      """Call the registered cleanup, ignoring the failure it unwinds."""
      callback(*args, **kwds)

    self.push(_linked_sync_exit(callback_exit))
    return callback

  def push_async_callback(
    self, callback: Callable[_P, Awaitable[_T]], /, *args: _P.args, **kwds: _P.kwargs
  ) -> Callable[_P, Awaitable[_T]]:
    """Register an asynchronous cleanup call.

    Args:
      callback: Async cleanup operation to call.
      *args: Positional arguments forwarded to the callback.
      **kwds: Keyword arguments forwarded to the callback.

    Returns:
      The callback, so this method can be used as a decorator.
    """

    async def callback_exit(
      exception_type: _ExceptionType,
      exception: Optional[BaseException],
      traceback: Optional[TracebackType],
    ) -> None:
      """Await the registered cleanup, ignoring the failure it unwinds."""
      await callback(*args, **kwds)

    self.push_async_exit(_linked_async_exit(callback_exit))
    return callback

  def push_shielded_async_callback(
    self,
    callback: Callable[..., Awaitable[Any]],
    *args: Any,
    shield_timeout: Optional[float] = None,
  ) -> None:
    """Register an async callback protected from surrounding cancellation.

    Args:
      callback: Async cleanup operation to call.
      *args: Positional arguments forwarded to the callback.
      shield_timeout: Optional maximum duration for the shielded cleanup.
    """

    async def shielded_exit(
      exception_type: _ExceptionType,
      exception: Optional[BaseException],
      traceback: Optional[TracebackType],
    ) -> None:
      """Run the callback under a shield, ignoring the failure it unwinds.

      Args:
        exception_type: Type of the failure this cleanup unwinds, if any.
        exception: Failure this cleanup unwinds, if any.
        traceback: Traceback of the failure this cleanup unwinds, if any.

      Raises:
        BaseException: If the callback fails or its shield timeout expires.
      """
      with anyio.CancelScope(shield=True):
        if shield_timeout is None:
          await callback(*args)
        else:
          with anyio.fail_after(shield_timeout):
            await callback(*args)

    self.push_async_exit(_linked_async_exit(shielded_exit))


class SiblingTaskErrors(ExceptionGroup):
  """Ordinary child failures collected without cancelling sibling tasks."""


class NonCancellingTaskGroup:
  """Start async callables and record ordinary failures without stopping siblings."""

  def __init__(self, task_group: anyio.abc.TaskGroup) -> None:
    """Use a structured AnyIO task group to own every submitted child."""
    self._task_group = task_group
    self._errors: list[Optional[Exception]] = []

  async def _run(
    self, index: int, operation: Callable[..., Awaitable[Any]], args: Tuple[Any, ...]
  ) -> None:
    """Create the coroutine inside its child task and retain ordinary failures."""
    try:
      await operation(*args)
    except Exception as error:
      self._errors[index] = error

  def start_soon(self, operation: Callable[..., Awaitable[Any]], *args: Any) -> None:
    """Schedule an async callable, preserving submission order for failures."""
    index = len(self._errors)
    self._errors.append(None)
    try:
      self._task_group.start_soon(self._run, index, operation, args)
    except BaseException:
      self._errors.pop()
      raise

  @property
  def _failures(self) -> list[Exception]:
    """Return captured exceptions in submission order after children finish."""
    return [error for error in self._errors if error is not None]


@asynccontextmanager
async def non_cancelling_task_group() -> AsyncIterator[NonCancellingTaskGroup]:
  """Join every child before raising their ordinary failures as an ExceptionGroup.

  Child errors do not cancel siblings. A body failure or external cancellation retains
  normal task-group cancellation behavior; body and prior child failures are both reported.
  """
  collector: Optional[NonCancellingTaskGroup] = None
  try:
    async with AsyncExitStackWithShielding() as stack:
      group = await stack.enter_task_group()
      collector = NonCancellingTaskGroup(group)
      yield collector
  except BaseException as error:
    if (
      collector is not None
      and collector._failures
      and not isinstance(error, anyio.get_cancelled_exc_class())
    ):
      raise BaseExceptionGroup("Task group body and child failures", [error, *collector._failures])
    raise
  if collector is not None and collector._failures:
    raise SiblingTaskErrors("Task group child failures", collector._failures)


def _chain_cleanup_failures(error: BaseException, earlier: BaseException) -> None:
  """Make an earlier failure reachable from a later one without losing either chain.

  The link goes at the point where the two failures stop sharing a tail, so a failure
  they both already reach, such as the failure their caller unwinds, is kept once
  instead of being overwritten or turned into a cycle.

  Args:
    error: Failure raised after `earlier`.
    earlier: Failure to keep reachable from `error`.
  """
  if _failure_is_reachable(earlier, error):
    return
  node = error
  while node.__context__ is not None and not _failure_is_reachable(earlier, node.__context__):
    node = node.__context__
  node.__context__ = earlier


async def attempt_in_order(*operations: Callable[[], Awaitable[Any]]) -> None:
  """Await every operation in the given order, attempting all of them.

  Every operation is attempted even after an earlier one fails. Use this for independent
  operations that share one transport, and therefore cannot run concurrently in a task
  group. Operations after a delivered cancellation are attempted but return immediately,
  so shield the call when every operation must reach its device.

  Args:
    *operations: Async callables awaited in the order given.

  Raises:
    BaseException: The last failure, with every earlier failure reachable through its
      exception context chain.
  """
  failure: Optional[BaseException] = None
  for operation in operations:
    try:
      await operation()
    except BaseException as error:
      if failure is not None:
        _chain_cleanup_failures(error, failure)
      failure = error
  if failure is None:
    return
  # Raising overwrites the context with the failure this call unwinds, which would drop
  # every earlier operation's failure, so restore the chain built above afterwards.
  chained_context = failure.__context__
  try:
    raise failure
  except BaseException:
    failure.__context__ = chained_context
    raise


class _LifecycleState(Enum):
  """Internal states for one asynchronous lifespan."""

  INACTIVE = auto()
  ENTERING = auto()
  ACTIVE = auto()
  EXITING = auto()


def _managed_method_implementations(cls: Type[Any]) -> Dict[str, Any]:
  """Return the managed lifecycle methods a class resolves through its bases."""
  return {
    "__aenter__": cls.__aenter__,
    "__aexit__": cls.__aexit__,
    "setup": cls.setup,
    "stop": cls.stop,
  }


class _HasLifespanMeta(ABCMeta):
  """Validate lifespan extension points at class definition time."""

  _lifespan_extension_classes: Set[Type[Any]] = set()

  def __new__(
    mcls: Type["_HasLifespanMeta"],
    name: str,
    bases: Tuple[Type[Any], ...],
    namespace: Dict[str, Any],
    *,
    _defer_lifespan: bool = False,
    **kwargs: Any,
  ) -> "_HasLifespanMeta":
    """Validate lifecycle methods, allowing an intermediate base to defer its own entry."""
    declares_lifespan = bool(bases) and "lifespan" in namespace
    cls = super().__new__(mcls, name, bases, namespace, **kwargs)
    if not bases:
      return cls

    managed_methods = _managed_method_implementations(HasLifespan)
    implementations = _managed_method_implementations(cls)
    for method_name, implementation in implementations.items():
      if method_name in namespace:
        raise TypeError(f"HasLifespan subclasses cannot override {method_name}")
      if implementation is not managed_methods[method_name]:
        raise TypeError(f"HasLifespan subclasses cannot override {method_name}")

    provides_lifespan = (
      "_enter_lifespan" in namespace
      or declares_lifespan
      or any(base in mcls._lifespan_extension_classes for base in bases)
    )
    # An intermediate base may defer its own entry; the option is not inherited by subclasses.
    if not inspect.isabstract(cls) and not provides_lifespan and not _defer_lifespan:
      raise TypeError("Concrete HasLifespan subclasses must implement _enter_lifespan or lifespan")
    if provides_lifespan:
      mcls._lifespan_extension_classes.add(cls)
    return cls


class HasLifespan(metaclass=_HasLifespanMeta):
  """Base class for objects with a lexically scoped async lifespan."""

  _lifecycle_state = _LifecycleState.INACTIVE
  _active_lifespan: Optional[AsyncContextManager["HasLifespan"]] = None

  def __init__(self) -> None:
    """Initialize an inactive lifespan owner."""
    self._lifecycle_state = _LifecycleState.INACTIVE
    self._active_lifespan = None

  @property
  def is_active(self) -> bool:
    """Whether base lifespan entry completed and exit has not started."""
    return self._lifecycle_state is _LifecycleState.ACTIVE

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Cooperative no-op entry hook for subclasses acquiring scoped resources.

    Args:
      stack: Exit stack owned by this lifespan.
    """
    pass

  def _lifespan_event_info(self, **kwargs: Any) -> Optional[LifespanEventInfo]:
    """Return transition event metadata when this object publishes lifecycle events."""
    return None

  @asynccontextmanager
  async def _default_lifespan(self, **kwargs: Any) -> AsyncIterator["HasLifespan"]:
    """Acquire and yield this object using the default lifespan implementation."""
    async with AsyncExitStackWithShielding() as stack:
      # The public lifespan forwards device-specific options. Their names are
      # checked by Python against the concrete implementation on entry.
      await cast(Callable[..., Awaitable[None]], self._enter_lifespan)(stack, **kwargs)
      yield self

  @asynccontextmanager
  async def lifespan(self, **kwargs: Any) -> AsyncIterator["HasLifespan"]:
    """Enter one state-managed scoped lifespan using ``_enter_lifespan``.

    Args:
      **kwargs: Lifespan-specific acquisition options.

    Yields:
      The active lifespan owner.

    Raises:
      RuntimeError: If this object already has a lifespan in progress.
    """
    if self._lifecycle_state is not _LifecycleState.INACTIVE:
      raise RuntimeError("This object already has an active lifespan")

    event_info = self._lifespan_event_info(**kwargs)
    self._lifecycle_state = _LifecycleState.ENTERING
    with lifespan_events(event_info) as events:
      try:
        async with self._default_lifespan(**kwargs) as owner:
          self._lifecycle_state = _LifecycleState.ACTIVE
          with events.lifespan_active:
            try:
              yield owner
            finally:
              self._lifecycle_state = _LifecycleState.EXITING
      finally:
        self._lifecycle_state = _LifecycleState.INACTIVE

  async def __aenter__(self) -> "HasLifespan":
    """Enter and retain this object's current lifespan context manager."""
    lifespan = self.lifespan()
    owner = await lifespan.__aenter__()
    self._active_lifespan = lifespan
    return owner

  async def __aexit__(
    self,
    exception_type: Optional[Type[BaseException]],
    exception: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> Optional[bool]:
    """Exit exactly the lifespan entered by this context manager."""
    lifespan = self._active_lifespan
    if lifespan is None:
      raise RuntimeError("Lifespan context manager has not been entered")
    self._active_lifespan = None
    return await lifespan.__aexit__(exception_type, exception, traceback)

  async def setup(self, **lifespan_kwargs: Any) -> None:
    """Enter this object through the deprecated interactive bridge.

    Args:
      **lifespan_kwargs: Lifespan-specific acquisition options.
    """
    warnings.warn(
      "setup() is deprecated; use 'async with device:' instead",
      DeprecationWarning,
      stacklevel=2,
    )
    await global_manager.manage_context(self, **lifespan_kwargs)

  async def stop(self) -> None:
    """Exit the lifespan entered by the deprecated interactive bridge."""
    await global_manager.release_context(self)


class _ManagedContext:
  """Signals and failure state for one interactively managed lifespan."""

  def __init__(self) -> None:
    """Initialize lifecycle synchronization signals."""
    self.started = anyio.Event()
    self.stop = anyio.Event()
    self.exited = anyio.Event()
    self.error: Optional[BaseException] = None


class GlobalManager:
  """Own lifespans for deprecated asyncio interactive usage."""

  def __init__(self) -> None:
    """Initialize an idle interactive lifespan manager."""
    self._task_group: Optional[anyio.abc.TaskGroup] = None
    self._running_task: Optional["asyncio.Task[None]"] = None
    self._runner_started: Optional[anyio.Event] = None
    self._runner_stop: Optional[anyio.Event] = None
    self._runner_lock = asyncio.Lock()
    self._contexts: Dict[HasLifespan, _ManagedContext] = {}

  def _check_backend(self) -> None:
    """Reject interactive ownership outside asyncio before creating tasks."""
    backend = sniffio.current_async_library()
    if backend != "asyncio":
      raise RuntimeError(
        "Interactive setup/stop is only supported on asyncio; use 'async with device:' instead"
      )

  async def _run_task_group(self, started: anyio.Event, stop: anyio.Event) -> None:
    """Host the manager's long-lived AnyIO task group in one asyncio task."""
    try:
      async with anyio.create_task_group() as task_group:
        self._task_group = task_group
        started.set()
        await stop.wait()
    finally:
      self._task_group = None

  async def _ensure_runner(self) -> anyio.abc.TaskGroup:
    """Start the shared manager task and return its task group."""
    async with self._runner_lock:
      if self._running_task is None or self._running_task.done():
        started = anyio.Event()
        stop = anyio.Event()
        self._runner_started = started
        self._runner_stop = stop
        self._running_task = asyncio.get_running_loop().create_task(
          self._run_task_group(started, stop)
        )
      runner_started = self._runner_started
      if runner_started is None:
        raise RuntimeError("Interactive lifespan manager failed to start")
      await runner_started.wait()
      task_group = self._task_group
      if task_group is None:
        raise RuntimeError("Interactive lifespan manager failed to start")
      return task_group

  async def _run_context(
    self,
    owner: HasLifespan,
    managed: _ManagedContext,
    lifespan_kwargs: Dict[str, Any],
  ) -> None:
    """Enter one lifespan and retain it until release is requested."""
    try:
      async with owner.lifespan(**lifespan_kwargs):
        managed.started.set()
        await managed.stop.wait()
    except BaseException as error:
      managed.error = error
    finally:
      managed.started.set()
      managed.exited.set()

  def _remove_context(self, owner: HasLifespan, managed: _ManagedContext) -> None:
    """Remove a managed context if it is still the owner's current entry."""
    if self._contexts.get(owner) is managed:
      self._contexts.pop(owner)

  async def _stop_runner_if_idle(self) -> None:
    """Stop and clear the shared runner after its last owner exits."""
    async with self._runner_lock:
      if self._contexts or self._running_task is None:
        return
      running_task = self._running_task
      runner_stop = self._runner_stop
      if runner_stop is not None:
        runner_stop.set()
      await running_task
      if self._running_task is running_task:
        self._running_task = None
        self._runner_started = None
        self._runner_stop = None
        if not self._contexts:
          self._runner_lock = asyncio.Lock()

  async def manage_context(self, owner: HasLifespan, **lifespan_kwargs: Any) -> None:
    """Enter and retain a lifespan in the shared manager task.

    Args:
      owner: Object whose lifespan should be entered.
      **lifespan_kwargs: Lifespan-specific acquisition options.

    Raises:
      RuntimeError: If the backend is unsupported or the owner is already managed.
      BaseException: If lifespan entry fails.
    """
    self._check_backend()
    if owner in self._contexts:
      raise RuntimeError("This object already has an active lifespan")

    managed = _ManagedContext()
    self._contexts[owner] = managed
    scheduled = False
    try:
      task_group = await self._ensure_runner()
      task_group.start_soon(self._run_context, owner, managed, dict(lifespan_kwargs))
      scheduled = True
      await managed.started.wait()
      if managed.error is not None:
        raise managed.error
    except BaseException as error:
      if scheduled:
        managed.stop.set()
        with anyio.CancelScope(shield=True):
          await managed.exited.wait()
      self._remove_context(owner, managed)
      with anyio.CancelScope(shield=True):
        await self._stop_runner_if_idle()
      if managed.error is not None and managed.error is not error:
        raise managed.error
      raise

  async def _await_context_exit(self, owner: HasLifespan, managed: _ManagedContext) -> None:
    """Wait for one managed lifespan to exit, then drop it and idle the runner."""
    with anyio.CancelScope(shield=True):
      await managed.exited.wait()
    self._remove_context(owner, managed)
    await self._stop_runner_if_idle()

  async def release_context(self, owner: HasLifespan) -> None:
    """Exit a lifespan retained by this manager.

    Args:
      owner: Object whose managed lifespan should exit.

    Raises:
      BaseException: If lifespan exit fails or the caller is cancelled.
    """
    self._check_backend()
    managed = self._contexts.get(owner)
    if managed is None:
      return

    managed.stop.set()
    try:
      await self._await_context_exit(owner, managed)
    except BaseException as error:
      with anyio.CancelScope(shield=True):
        await self._await_context_exit(owner, managed)
      if managed.error is not None and managed.error is not error:
        raise managed.error
      raise
    if managed.error is not None:
      raise managed.error

  async def stop_all(self) -> None:
    """Exit every managed lifespan simultaneously and surface failures."""
    owners = tuple(self._contexts)
    async with anyio.create_task_group() as task_group:
      for owner in owners:
        task_group.start_soon(self.release_context, owner)


global_manager = GlobalManager()

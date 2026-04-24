import abc
import asyncio
import contextlib
import dataclasses
import functools
import sys
import typing
import warnings

if sys.version_info >= (3, 10):
  from typing import Any, Optional, TypeAlias
else:
  from typing_extensions import Any, Optional, TypeAlias

import anyio
import sniffio


class MachineConnectionClosedError(Exception):
  """Raised when a machine task is being aborted because the connection is, or has been closed."""


class AsyncExitStackWithShielding(contextlib.AsyncExitStack):
  def push_shielded_async_callback(self, callback: typing.Callable, *args):
    @functools.wraps(callback)
    async def shielded_callback(*args):
      with anyio.CancelScope(shield=True):
        await callback(*args)

    self.push_async_callback(shielded_callback, *args)


@dataclasses.dataclass(frozen=True)
class _LifespanLifecycleTag:
  """Tags used to represent the lifecycle of a lifespan,
  for accurate double-entry checking."""

  name: str


LifespanEntering = _LifespanLifecycleTag("entering")
LifespanExiting = _LifespanLifecycleTag("exiting")
AnonymousLifespan = _LifespanLifecycleTag("anonymous")


class _AsyncResourceBase:
  """Implementation of `AsyncResource`, but without any `__new__` to implement ABC checking."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    """Helper for the _lifespan implementation; override this instead of _lifespan.

    Note, child classes may add keyword-only arguments to the signature, as _lifespan
    forwards those.
    """
    raise NotImplementedError("Subclasses must override _enter_lifespan or _lifespan.")

  @contextlib.asynccontextmanager
  async def _lifespan(self, **kwargs):
    """The resource's lifespan.

    Subclasses should override this method to provide their own lifespan.
    Alternatively, they can provide `_enter_lifespan(stack)` which gets called with an `AsyncExitStack`.
    """
    # double-entry checking, using _active_lifespan as signalling mechanism.
    # this double-entry checking here isn't strictly necessary, since usually,
    # we always enter through __aenter__.
    active_lifespan = getattr(self, "_active_lifespan", None)
    if active_lifespan is None:
      # This is a direct call to _lifespan, not going through __aenter__.
      # we don't have access to the context manager, so we just store a tag.
      self._active_lifespan = AnonymousLifespan
    elif active_lifespan is not LifespanEntering:
      raise RuntimeError(f"lifespan of {type(self).__name__} is already entered")

    # main implementation
    try:
      async with AsyncExitStackWithShielding() as stack:
        await self._enter_lifespan(stack, **kwargs)
        yield self
        # there shouldn't be anything here; explicit cleanup is difficult to get right
        # in face of exceptions and cancellation; register your cleanup when you enter.
    finally:
      if self._active_lifespan is AnonymousLifespan:
        self._active_lifespan = None  # type: ignore[assignment]

  async def __aenter__(self):
    """Enter the resource's lifespan.
    This method should not be overridden by subclasses;
    separate `__aenter__` and `__aexit__` calls are difficult to implement correctly,
    implement `_lifespan` or `_enter_lifespan` instead.
    """
    if getattr(self, "_active_lifespan", None) is not None:
      raise RuntimeError(f"lifespan of {type(self).__name__} is already entered")

    try:
      self._active_lifespan = LifespanEntering
      active_lifespan = self._lifespan()
      await active_lifespan.__aenter__()
      self._active_lifespan = active_lifespan  # type: ignore[assignment]
    except:
      self._active_lifespan = None  # type: ignore[assignment]
      raise
    return self

  async def __aexit__(self, exc_type, exc_val, exc_tb):
    """Exit the resource's context.
    This method should never be overridden.
    """
    try:
      active_lifespan = self._active_lifespan
      self._active_lifespan = LifespanExiting
      ret = await active_lifespan.__aexit__(exc_type, exc_val, exc_tb)  # type: ignore[attr-defined]
    finally:
      self._active_lifespan = None  # type: ignore[assignment]
    return ret


class AsyncResource(_AsyncResourceBase, abc.ABC):
  """An abstract base class for all resources."""

  def __new__(cls, *args, **kwargs):
    # Check if both methods are still the base implementations
    if (
      cls._enter_lifespan is AsyncResource._enter_lifespan
      and cls._lifespan is _AsyncResourceBase._lifespan
    ):
      raise TypeError(
        f"Can't instantiate abstract class {cls.__name__} "
        "without an implementation for either '_enter_lifespan' or '_lifespan'"
      )

    return super().__new__(cls)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    # Non-throwing base class implementation, so that derived classes can
    # call super()._enter_lifespan() without knowing how many classes are in the chain.
    pass


MachineID: TypeAlias = Any


class GlobalManager:
  """A global task manager to enable interactive (notebook) usage of async context managers."""

  def __init__(self):
    self._tg: Optional[anyio.abc.TaskGroup] = None
    self._running_task: Optional[asyncio.Task] = None
    self._started: Optional[anyio.Event] = None
    self._stop: Optional[anyio.Event] = None
    self._pending: set[MachineID] = set()
    self._stop_events: dict[MachineID, anyio.Event] = {}
    self._exit_events: dict[MachineID, anyio.Event] = {}
    self._errors: dict[MachineID, Exception] = {}

  async def _run_global_task_group(self):
    async with anyio.create_task_group() as tg:
      assert self._tg is None
      self._tg = tg
      self._stop = anyio.Event()
      assert self._started is not None
      self._started.set()
      await self._stop.wait()

  @contextlib.asynccontextmanager
  async def _reserve_runner_for(self, obj):
    try:
      backend = sniffio.current_async_library()
    except sniffio.AsyncLibraryNotFoundError:
      backend = "asyncio"

    if backend != "asyncio":
      raise RuntimeError(
        f"The global manager for interactive setup/stop is currently only supported "
        f"on asyncio (Jupyter). Caught: {backend}. Please use `async with machine:` directly."
      )

    loop = asyncio.get_running_loop()

    try:
      self._pending.add(obj)
      if self._tg is None:
        try:
          self._started = anyio.Event()
          self._running_task = loop.create_task(self._run_global_task_group())
          await self._started.wait()
        finally:
          self._started = None
      yield self._tg
    finally:
      self._pending.discard(obj)

  async def manage_context(self, obj: Any):
    """Schedules an object's async context manager into the global task group."""

    stop_event = self._stop_events.get(obj)
    if stop_event is not None:
      warnings.warn(f"Object {obj} is already managed by the global task group.")
      return
    warnings.warn(
      "Prefer using structured concurrency (`async with resource:`) over `.setup` calls.",
      DeprecationWarning,
    )

    async def wrapper(*, task_status=anyio.TASK_STATUS_IGNORED):
      try:
        print("entering obj context manager")
        async with obj:
          print("entered obj context manager")
          task_status.started()
          assert stop_event is not None
          await stop_event.wait()
      except Exception as e:
        self._errors[obj] = e
      finally:
        self._stop_events.pop(obj, None)
        exit_event = self._exit_events.get(obj)
        if exit_event is not None:
          exit_event.set()
        if not self._pending and not self._stop_events:
          assert self._stop is not None
          self._stop.set()
          self._stop = None
          self._tg = None
          self._running_task = None

    try:
      async with self._reserve_runner_for(obj):
        self._stop_events[obj] = stop_event = anyio.Event()
        assert self._tg is not None
        await self._tg.start(wrapper)
    except Exception:
      self._stop_events.pop(obj, None)
      raise
    finally:
      e = self._errors.pop(obj, None)
      if e is not None:
        raise e

  async def release_context(self, obj: Any):
    """Signals the given object's context manager to gracefully exit."""
    errors = self._errors.pop(obj, None)
    if errors is not None:
      raise errors

    stop_event = self._stop_events.pop(obj, None)

    if stop_event is None:
      warnings.warn(f"Object {obj} is not managed by the global task group. ")
      return

    try:
      self._exit_events[obj] = exit_event = anyio.Event()
      stop_event.set()
      await exit_event.wait()
    finally:
      self._exit_events.pop(obj, None)

  async def stop_all(self):
    """Forcefully stops all managed objects and terminates the global TaskGroup."""

    async def do_release(obj, go, *, task_status=anyio.TASK_STATUS_IGNORED):
      with anyio.CancelScope(shield=True):
        task_status.started()
        await go.wait()
        await self.release_context(obj)

    # Release all managed objects simultaneously in a task group.
    # Each release is shielded from cancellation; this guarantees that
    # all objects attempt to exit, and we get all errors in one ExceptionGroup.
    async with anyio.create_task_group() as tg:
      go = anyio.Event()
      for obj in list(self._stop_events.keys()):
        await tg.start(do_release, obj, go)
      go.set()


global_manager = GlobalManager()

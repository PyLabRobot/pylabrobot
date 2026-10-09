from contextlib import asynccontextmanager
from functools import partial
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, attempt_in_order
from pylabrobot.events import evented_operation, resource_reference
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .backend import ShakerBackend

# A Hamilton heater-shaker stop can wait for three 60-second command exchanges plus margin.
# Bound each independent cleanup action and lock wait without shortening that existing budget.
_SHAKER_CLEANUP_TIMEOUT = 184.0


def _shaker_controller_event_context(self: "Shaker") -> dict[str, Any]:
  context: dict[str, Any] = {"device": resource_reference(self)}
  if self.resource is not None:
    context["resources"] = [resource_reference(self.resource)]
  return context


def _shake_event_context(
  self: "Shaker",
  speed: float,
  duration: Optional[float] = None,
  **backend_kwargs: Any,
) -> dict[str, Any]:
  """Describe a shaker operation and its directly loaded resource, when present."""

  context = _shaker_controller_event_context(self)
  context["speed_rpm"] = float(speed)
  if duration is not None:
    context["duration"] = float(duration)
  return context


def _stop_shaking_event_context(self: "Shaker", **backend_kwargs: Any) -> dict[str, Any]:
  return _shaker_controller_event_context(self)


class Shaker(ResourceHolder, Machine):
  """A shaker machine"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ShakerBackend,
    child_location: Coordinate,
    category: str = "shaker",
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
      child_location=child_location,
    )
    Machine.__init__(self, backend=backend)
    self.backend: ShakerBackend = backend  # fix type
    self._initialize_shaking_state()

  def _initialize_shaking_state(self) -> None:
    """Initialize driver-local shaking transition state."""
    self._shaking_transition_lock = anyio.Lock()
    self._shaking_active = False
    self._shaking_state_unknown = False
    self._shaking_scope_token: Optional[object] = None
    self._shaking_plate_unlock_owed = False

  @evented_operation("shaker.shake", _shake_event_context)
  async def _start_shaking(
    self,
    speed: float,
    duration: Optional[float] = None,
    **backend_kwargs: Any,
  ) -> None:
    """Issue the evented backend transition into shaking."""
    await self.backend.start_shaking(speed=speed, **backend_kwargs)

  @evented_operation("shaker.stop_shaking", _stop_shaking_event_context)
  async def _stop_shaking(self, **backend_kwargs: Any) -> None:
    """Issue the evented backend transition out of shaking."""
    await self.backend.stop_shaking(**backend_kwargs)

  async def _stop_scoped_shaking(self) -> None:
    """Stop one scoped operation and retain retryable state on failure."""
    try:
      await self._stop_shaking()
    except BaseException:
      self._shaking_active = True
      self._shaking_state_unknown = True
      raise
    else:
      self._shaking_active = False
      self._shaking_state_unknown = False

  async def _unlock_scoped_plate(self) -> None:
    """Fulfill a scoped plate-lock obligation, retaining it on failure."""
    await self.backend.unlock_plate()
    self._shaking_plate_unlock_owed = False

  async def _run_cleanup_action(
    self, action: Callable[[], Awaitable[None]], *, stop: bool = False
  ) -> None:
    """Bound one safety action so a stalled stop cannot prevent a plate unlock."""
    try:
      with anyio.fail_after(_SHAKER_CLEANUP_TIMEOUT):
        await action()
    except TimeoutError:
      if stop:
        self._shaking_active = True
        self._shaking_state_unknown = True
      raise

  async def _cleanup_shaking_actions(self, *, scoped: bool, force_stop: bool = False) -> None:
    """Attempt independent stop and plate unlock within separate deadlines."""
    actions: list[Callable[[], Awaitable[None]]] = []
    if force_stop or self._shaking_active or self._shaking_state_unknown:
      stop_action = self._stop_scoped_shaking if scoped else self._force_stop_shaking_locked
      actions.append(partial(self._run_cleanup_action, stop_action, stop=True))
    if self._shaking_plate_unlock_owed:
      actions.append(partial(self._run_cleanup_action, self._unlock_scoped_plate))
    await attempt_in_order(*actions)

  async def _release_shaking_scope(self, token: object) -> None:
    """Undo one shaking scope unless its transition was already claimed elsewhere.

    Args:
      token: Identity of the scope this cleanup belongs to.
    """
    try:
      with anyio.fail_after(_SHAKER_CLEANUP_TIMEOUT):
        await self._shaking_transition_lock.acquire()
      try:
        if self._shaking_scope_token is token:
          await self._cleanup_shaking_actions(scoped=True)
      finally:
        self._shaking_transition_lock.release()
    finally:
      if self._shaking_scope_token is token:
        self._shaking_scope_token = None

  @asynccontextmanager
  async def _shaking(
    self,
    speed: float,
    duration: Optional[float] = None,
    **backend_kwargs: Any,
  ) -> AsyncIterator[None]:
    """Own one temporary shaking transition and its reverse-order cleanup."""
    token = object()
    async with AsyncExitStackWithShielding() as cleanup_stack:
      async with self._shaking_transition_lock:
        if not self.is_active:
          raise RuntimeError("Shaking scopes require an active Shaker lifespan")
        if self._shaking_scope_token is not None:
          raise RuntimeError("Shaking is already scoped")
        if self._shaking_active or self._shaking_state_unknown:
          raise RuntimeError("Shaking is already active")
        if self._shaking_plate_unlock_owed:
          raise RuntimeError("Plate unlock remains unresolved")

        await anyio.lowlevel.checkpoint()
        with anyio.CancelScope(shield=True):
          # Claim the scope before starting: the claim is invisible to everything else until
          # this lock is released, and it is what lets a failed start unwind through the
          # same cleanup a completed scope uses.
          cleanup_stack.push_shielded_async_callback(self._release_shaking_scope, token)
          self._shaking_scope_token = token
          if self.backend.supports_locking:
            self._shaking_plate_unlock_owed = True
            await self.backend.lock_plate()
          self._shaking_state_unknown = True
          await self._start_shaking(speed, duration=duration, **backend_kwargs)
          self._shaking_active = True
          self._shaking_state_unknown = False

      await anyio.lowlevel.checkpoint()
      yield

  @asynccontextmanager
  async def shaking(self, speed: float, **backend_kwargs: Any) -> AsyncIterator[None]:
    """Temporarily shake while owning stop and optional plate-unlock cleanup.

    The shaker must already be inside its active lifespan. Scoped shaking rejects nested,
    concurrent, and durable shaking transitions until cleanup has completed.

    Args:
      speed: Speed of shaking in revolutions per minute (RPM).
      **backend_kwargs: Options forwarded to the backend start transition.

    Yields:
      Once shaking has started and any supported plate lock has been acquired.
    """
    async with self._shaking(speed, **backend_kwargs):
      yield

  async def shake(
    self,
    speed: float,
    duration: Optional[float] = None,
    **backend_kwargs: Any,
  ) -> None:
    """Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
      duration: Duration of shaking in seconds. If None, shake indefinitely (and return immediately).
    """
    if duration is None:
      async with self._shaking_transition_lock:
        if self._shaking_scope_token is not None:
          raise RuntimeError("Cannot start durable shaking while a shaking scope is active")
        await anyio.lowlevel.checkpoint()
        with anyio.CancelScope(shield=True):
          if self.backend.supports_locking:
            await self.backend.lock_plate()
          try:
            await self._start_shaking(speed, **backend_kwargs)
          except BaseException:
            self._shaking_state_unknown = True
            raise
          self._shaking_active = True
          self._shaking_state_unknown = False
      return

    async with self._shaking(speed, duration=duration, **backend_kwargs):
      await anyio.sleep(duration)

  async def stop_shaking(self, **backend_kwargs: Any) -> None:
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot stop shaking while a shaking scope is active")
      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        try:
          await self._stop_shaking(**backend_kwargs)
        except BaseException:
          self._shaking_active = True
          self._shaking_state_unknown = True
          raise
        self._shaking_active = False
        self._shaking_state_unknown = False

  async def lock_plate(self, **backend_kwargs: Any) -> None:
    async with self._shaking_transition_lock:
      await self.backend.lock_plate(**backend_kwargs)

  async def unlock_plate(self, **backend_kwargs: Any) -> None:
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot unlock the plate while a shaking scope is active")
      await self.backend.unlock_plate(**backend_kwargs)

  async def _force_stop_shaking_locked(self) -> None:
    """Stop shaking while the transition lock is held; preserve failed state."""
    try:
      await self._stop_shaking()
    except BaseException:
      self._shaking_active = True
      self._shaking_state_unknown = True
      raise
    self._shaking_active = False
    self._shaking_state_unknown = False
    self._shaking_scope_token = None

  async def _force_stop_shaking(self) -> None:
    """Stop shaking without public scope guards and clear tracked ownership."""
    with anyio.fail_after(_SHAKER_CLEANUP_TIMEOUT):
      await self._shaking_transition_lock.acquire()
    try:
      await self._run_cleanup_action(self._force_stop_shaking_locked, stop=True)
    finally:
      self._shaking_transition_lock.release()

  async def _pre_backend_stop(self, *, force_stop: bool = False) -> None:
    """Fulfill tracked shaking and plate unlock before closing the backend."""
    try:
      with anyio.fail_after(_SHAKER_CLEANUP_TIMEOUT):
        await self._shaking_transition_lock.acquire()
      try:
        await self._cleanup_shaking_actions(scoped=False, force_stop=force_stop)
      finally:
        self._shaking_transition_lock.release()
    finally:
      self._shaking_scope_token = None

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }

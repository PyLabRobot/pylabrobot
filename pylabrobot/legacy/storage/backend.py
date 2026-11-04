from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional

import anyio

from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.resources import Plate, PlateCarrier, PlateHolder


class IncubatorBackend(MachineBackend, metaclass=ABCMeta):
  def __init__(self):
    super().__init__()
    self._racks: Optional[List[PlateCarrier]] = None
    self._shaking_active = False
    self._shaking_state_unknown = False
    self._shaking_scope_token: Optional[object] = None
    self._shaking_transition_lock = anyio.Lock()

  @property
  def racks(self) -> List[PlateCarrier]:
    assert self._racks is not None, "Backend not set up?"
    return self._racks

  async def set_racks(self, racks: List[PlateCarrier]):
    self._racks = racks

  @abstractmethod
  async def open_door(self):
    pass

  @abstractmethod
  async def close_door(self):
    pass

  @abstractmethod
  async def fetch_plate_to_loading_tray(self, plate: Plate, **backend_kwargs):
    pass

  @abstractmethod
  async def take_in_plate(self, plate: Plate, site: PlateHolder, **backend_kwargs):
    pass

  @abstractmethod
  async def set_temperature(self, temperature: float):
    """Set the temperature of the incubator in degrees Celsius."""

  @abstractmethod
  async def get_temperature(self) -> float:
    """Get the temperature of the incubator in degrees Celsius."""

  @abstractmethod
  async def _start_shaking_hardware(
    self, frequency: float, shakers: Optional[List[int]] = None
  ) -> Any:
    """Start the concrete backend's shaking hardware."""

  @abstractmethod
  async def _stop_shaking_hardware(self) -> Any:
    """Stop the concrete backend's shaking hardware."""

  async def start_shaking(self, frequency: float) -> Any:
    """Start durable shaking through the authoritative operation owner."""
    return await self._start_durable_shaking(frequency)

  async def stop_shaking(self) -> Any:
    """Stop durable shaking through the authoritative operation owner."""
    return await self._stop_durable_shaking()

  async def _start_shaking_transition(
    self, frequency: float, *, rollback_on_failure: bool, **backend_kwargs: Any
  ) -> Any:
    """Start shaking while the caller owns the transition lock."""
    await anyio.lowlevel.checkpoint()
    self._shaking_state_unknown = True
    try:
      with anyio.CancelScope(shield=True):
        result = await self._start_shaking_hardware(frequency, **backend_kwargs)
        self._shaking_active = True
        self._shaking_state_unknown = False
      await anyio.lowlevel.checkpoint()
    except anyio.get_cancelled_exc_class():
      with anyio.CancelScope(shield=True):
        await self._stop_shaking_transition()
      raise
    except BaseException:
      self._shaking_active = True
      self._shaking_state_unknown = True
      if rollback_on_failure:
        with anyio.CancelScope(shield=True):
          await self._stop_shaking_transition()
      raise
    return result

  async def _stop_shaking_transition(self, *, force: bool = False) -> Any:
    """Stop shaking and retain retryable state when the result is unknown."""
    if not force and not (self._shaking_active or self._shaking_state_unknown):
      return
    await anyio.lowlevel.checkpoint()
    self._shaking_state_unknown = True
    try:
      with anyio.CancelScope(shield=True):
        result = await self._stop_shaking_hardware()
        self._shaking_active = False
        self._shaking_state_unknown = False
      await anyio.lowlevel.checkpoint()
    except anyio.get_cancelled_exc_class():
      raise
    except BaseException:
      self._shaking_active = True
      self._shaking_state_unknown = True
      raise
    return result

  async def _start_durable_shaking(self, frequency: float, **backend_kwargs: Any) -> Any:
    """Start durable shaking through the shared operation owner."""
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot start shaking while a shaking scope is active")
      return await self._start_shaking_transition(
        frequency, rollback_on_failure=False, **backend_kwargs
      )

  async def _stop_durable_shaking(self) -> Any:
    """Stop durable shaking through the shared operation owner."""
    async with self._shaking_transition_lock:
      if self._shaking_scope_token is not None:
        raise RuntimeError("Cannot stop shaking while a shaking scope is active")
      return await self._stop_shaking_transition(force=True)

  @asynccontextmanager
  async def _shaking(self, frequency: float, **backend_kwargs: Any) -> AsyncIterator[None]:
    """Own one shaking operation, including cancellation-safe reconciliation."""
    token = object()
    async with self._shaking_transition_lock:
      if not self.is_active:
        raise RuntimeError("Shaking scopes require an active incubator backend lifespan")
      if self._shaking_scope_token is not None:
        raise RuntimeError("Shaking is already scoped")
      if self._shaking_active or self._shaking_state_unknown:
        raise RuntimeError("Shaking is already active")
      await self._start_shaking_transition(frequency, rollback_on_failure=True, **backend_kwargs)
      self._shaking_scope_token = token

    try:
      await anyio.lowlevel.checkpoint()
      yield
    finally:
      with anyio.CancelScope(shield=True):
        async with self._shaking_transition_lock:
          if self._shaking_scope_token is token:
            try:
              await self._stop_shaking_transition()
            finally:
              self._shaking_scope_token = None

  @asynccontextmanager
  async def shaking(self, frequency: float = 1.0) -> AsyncIterator[None]:
    """Shake for the lifetime of this context."""
    async with self._shaking(frequency):
      yield

  async def _force_stop_shaking(self) -> None:
    """Stop tracked shaking during parent cleanup and invalidate scope ownership."""
    async with self._shaking_transition_lock:
      try:
        await self._stop_shaking_transition()
      finally:
        self._shaking_scope_token = None

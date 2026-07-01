"""Abstract backend for thermocyclers."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend

from .standard import Protocol


class ThermocyclerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend interface for thermocycler devices."""

  @abstractmethod
  async def run_protocol(
    self,
    protocol: Protocol,
    volume_ul: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Execute a thermocycler protocol. Fire-and-forget by default; backends
    may support a ``wait`` flag via ``backend_params``.

    Args:
      protocol: The protocol to run.
      volume_ul: Maximum sample volume in wells (µL). Backends that apply
        volume-dependent thermal compensation (e.g. ODTC overshoot) use this
        to select the appropriate compensation mode. Ignored by backends that
        do not support it. Overridden by an explicit ``fluid_quantity`` (or
        equivalent) in ``backend_params`` when provided.
      backend_params: Backend-specific per-call parameters.
    """

  @abstractmethod
  async def set_block_temperature(
    self,
    temperature: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Set the block to a target temperature and hold. Fire-and-forget by default."""

  @abstractmethod
  async def deactivate_block(self, backend_params: Optional[BackendParams] = None) -> None:
    """Stop block temperature control."""

  @abstractmethod
  async def request_block_temperature(self) -> float:
    """Return current block temperature in °C."""

  @abstractmethod
  async def request_lid_temperature(self) -> float:
    """Return current lid temperature in °C."""

  async def request_progress(self) -> Optional[Any]:
    """Return backend-specific progress object for the running protocol.

    Returns None if no protocol is running or progress is not available.
    Backends override this to provide rich progress data.
    """
    return None

  async def stop_protocol(self, backend_params: Optional[BackendParams] = None) -> None:
    """Stop the currently running protocol. Default: no-op (override if supported)."""

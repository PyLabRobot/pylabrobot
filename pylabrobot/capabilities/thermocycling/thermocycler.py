"""Thermocycler capability — user-facing API."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready

from .backend import ThermocyclerBackend
from .standard import BlockStatus, LidStatus, Protocol


class Thermocycler(Capability):
  """Thermocycler capability for running PCR and other temperature-cycling protocols.

  Wraps a ThermocyclerBackend and exposes a clean, guarded API.
  Owned by a Device; lifecycle managed via ``_on_setup`` / ``_on_stop``.
  """

  def __init__(self, backend: ThermocyclerBackend) -> None:
    super().__init__(backend=backend)
    self.backend: ThermocyclerBackend = backend
    self._current_protocol: Optional[Protocol] = None

  # ------------------------------------------------------------------
  # Protocol execution
  # ------------------------------------------------------------------

  @need_capability_ready
  async def run_protocol(
    self,
    protocol: Protocol,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Execute a thermocycler protocol.

    Args:
      protocol: The protocol to run.
      backend_params: Optional backend-specific parameters (e.g. variant,
        fluid_quantity for ODTC).
    """
    self._current_protocol = protocol
    await self.backend.run_protocol(protocol, backend_params=backend_params)

  @need_capability_ready
  async def stop_protocol(self, backend_params: Optional[BackendParams] = None) -> None:
    """Stop the currently running protocol."""
    await self.backend.stop_protocol(backend_params=backend_params)
    self._current_protocol = None

  # ------------------------------------------------------------------
  # Temperature control
  # ------------------------------------------------------------------

  @need_capability_ready
  async def set_block_temperature(
    self,
    temperature: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Set block temperature and hold.

    Args:
      temperature: Target block temperature in °C.
      backend_params: Optional backend-specific parameters.
    """
    await self.backend.set_block_temperature(temperature, backend_params=backend_params)

  @need_capability_ready
  async def deactivate_block(self, backend_params: Optional[BackendParams] = None) -> None:
    """Deactivate block temperature control."""
    await self.backend.deactivate_block(backend_params=backend_params)

  @need_capability_ready
  async def request_block_temperature(self) -> float:
    """Return current block temperature in °C."""
    return await self.backend.request_block_temperature()

  @need_capability_ready
  async def request_lid_temperature(self) -> float:
    """Return current lid temperature in °C."""
    return await self.backend.request_lid_temperature()

  # ------------------------------------------------------------------
  # Progress and status
  # ------------------------------------------------------------------

  @need_capability_ready
  async def request_progress(self) -> Optional[Any]:
    """Return backend-specific progress for the running protocol, or None."""
    return await self.backend.request_progress()

  async def wait_for_first_progress(self, timeout: float = 60.0) -> Any:
    """Block until the backend reports non-None progress, or raise TimeoutError.

    Useful for confirming that a protocol has actually started executing.

    Args:
      timeout: Maximum seconds to wait (default 60).

    Returns:
      The first non-None progress object.

    Raises:
      RuntimeError: If capability is not set up.
      TimeoutError: If no progress arrives within ``timeout`` seconds.
    """
    if not self._setup_finished:
      raise RuntimeError("Thermocycler capability is not set up.")
    start = time.time()
    while time.time() - start < timeout:
      progress = await self.backend.request_progress()
      if progress is not None:
        return progress
      await asyncio.sleep(0.5)
    raise TimeoutError(f"No protocol progress received within {timeout}s.")

  async def get_block_status(self) -> BlockStatus:
    """Return current block status (convenience wrapper)."""
    if not self._setup_finished:
      return BlockStatus.IDLE
    try:
      await self.backend.request_block_temperature()
      return BlockStatus.HOLDING_AT_TARGET
    except Exception:
      return BlockStatus.IDLE

  async def get_lid_status(self) -> LidStatus:
    """Return current lid status (convenience wrapper)."""
    if not self._setup_finished:
      return LidStatus.IDLE
    try:
      await self.backend.request_lid_temperature()
      return LidStatus.HOLDING_AT_TARGET
    except Exception:
      return LidStatus.IDLE

  # ------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------

  async def _on_stop(self) -> None:
    if self._setup_finished:
      try:
        await self.backend.deactivate_block()
      except Exception:
        pass
    self._current_protocol = None
    await super()._on_stop()

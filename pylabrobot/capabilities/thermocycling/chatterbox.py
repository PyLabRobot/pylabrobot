"""Chatterbox (in-memory, device-free) backend for thermocycler testing."""

from __future__ import annotations

import logging
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams

from .backend import ThermocyclerBackend
from .standard import Protocol

logger = logging.getLogger(__name__)


class ThermocyclerChatterboxBackend(ThermocyclerBackend):
  """In-memory thermocycler backend for testing and simulation.

  All operations succeed immediately and log at INFO level.
  Stores the last-set temperatures for assertion in tests.
  """

  def __init__(self) -> None:
    self._block_temperature: float = 25.0
    self._lid_temperature: float = 25.0
    self._current_protocol: Optional[Protocol] = None

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    self._block_temperature = 25.0
    self._lid_temperature = 25.0
    self._current_protocol = None

  async def run_protocol(
    self,
    protocol: Protocol,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    logger.info("ThermocyclerChatterbox: run_protocol name=%r", protocol.name)
    self._current_protocol = protocol

  async def set_block_temperature(
    self,
    temperature: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    logger.info("ThermocyclerChatterbox: set_block_temperature %.1f°C", temperature)
    self._block_temperature = temperature

  async def deactivate_block(self, backend_params: Optional[BackendParams] = None) -> None:
    logger.info("ThermocyclerChatterbox: deactivate_block")
    self._current_protocol = None

  async def request_block_temperature(self) -> float:
    return self._block_temperature

  async def request_lid_temperature(self) -> float:
    return self._lid_temperature

  async def request_progress(self) -> Optional[Any]:
    if self._current_protocol is None:
      return None
    return {"protocol_name": self._current_protocol.name, "running": True}

  async def stop_protocol(self, backend_params: Optional[BackendParams] = None) -> None:
    logger.info("ThermocyclerChatterbox: stop_protocol")
    self._current_protocol = None

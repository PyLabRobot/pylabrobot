from __future__ import annotations

import logging

from pylabrobot.capabilities.capability import Capability, need_capability_ready

from .backend import InstrumentStatusBackend
from .standard import InstrumentStatusReading

logger = logging.getLogger(__name__)


class InstrumentStatus(Capability):
  """Instrument status capability — poll the device's state machine."""

  def __init__(self, backend: InstrumentStatusBackend):
    super().__init__(backend=backend)
    self.backend: InstrumentStatusBackend = backend

  @need_capability_ready
  async def read_status(self) -> InstrumentStatusReading:
    """Return the current instrument status snapshot."""
    return await self.backend.read_status()

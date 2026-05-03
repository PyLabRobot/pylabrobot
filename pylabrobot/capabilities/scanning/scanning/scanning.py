from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.serializer import SerializableMixin

from .backend import ScanningBackend

logger = logging.getLogger(__name__)


class Scanning(Capability):
  """Flatbed scanning capability — fluorescence / luminescence imager control."""

  def __init__(self, backend: ScanningBackend):
    super().__init__(backend=backend)
    self.backend: ScanningBackend = backend

  @need_capability_ready
  async def configure(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    """Set up the next scan with vendor-specific parameters."""
    await self.backend.configure(backend_params=backend_params)

  @need_capability_ready
  async def start(self) -> None:
    """Begin acquisition."""
    await self.backend.start()

  @need_capability_ready
  async def stop(self) -> None:
    """Graceful stop — finish current line, save partial output."""
    await self.backend.stop()

  @need_capability_ready
  async def pause(self) -> None:
    """Pause acquisition. Resume by calling :meth:`start` again."""
    await self.backend.pause()

  @need_capability_ready
  async def cancel(self) -> None:
    """Abort acquisition and discard any partial output."""
    await self.backend.cancel()

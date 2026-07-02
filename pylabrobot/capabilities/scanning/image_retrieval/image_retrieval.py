from __future__ import annotations

import logging
from typing import List

from pylabrobot.capabilities.capability import Capability, need_capability_ready

from .backend import ImageRetrievalBackend

logger = logging.getLogger(__name__)


class ImageRetrieval(Capability):
  """Image retrieval capability — list and download saved scans."""

  def __init__(self, backend: ImageRetrievalBackend):
    super().__init__(backend=backend)
    self.backend: ImageRetrievalBackend = backend

  @need_capability_ready
  async def list_groups(self) -> List[str]:
    """Return the names of scan groups available on the instrument."""
    return await self.backend.list_groups()

  @need_capability_ready
  async def list_scans(self, group: str) -> List[str]:
    """Return scan names within ``group``."""
    return await self.backend.list_scans(group)

  @need_capability_ready
  async def download(self, group: str, scan_name: str) -> bytes:
    """Download all channels for a scan, concatenated."""
    return await self.backend.download(group, scan_name)

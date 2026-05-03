from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.capabilities.capability import CapabilityBackend


class ImageRetrievalError(Exception):
  """Capability-generic exception for image retrieval failures."""


class ImageRetrievalBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for the image retrieval capability.

  Lists and downloads previously-acquired scans from instrument
  storage. Independent of the scanning capability — scans persist
  after the session that produced them, and lab users often retrieve
  images they did not acquire themselves.
  """

  @abstractmethod
  async def list_groups(self) -> List[str]:
    """Return the names of scan groups available on the instrument."""

  @abstractmethod
  async def list_scans(self, group: str) -> List[str]:
    """Return scan names within ``group``."""

  @abstractmethod
  async def download(self, group: str, scan_name: str) -> bytes:
    """Download all channels for a scan, concatenated.

    Vendors with multi-channel scans (e.g. Odyssey 700 / 800 nm) may
    expose a ``download_channel`` extension on the concrete backend.
    """

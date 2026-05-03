"""Odyssey image retrieval backend — concrete ImageRetrievalBackend over HTTP.

Downloads scan TIFFs from the instrument's internal storage via the
/scan/image endpoint with XML query parameters, plus the scan-list
HTML at /scanapp/scan/nonjava/.
"""

from __future__ import annotations

import logging
from typing import List

from pylabrobot.capabilities.scanning.image_retrieval import ImageRetrievalBackend

from .driver import OdysseyDriver

logger = logging.getLogger(__name__)


class OdysseyImageRetrievalBackend(ImageRetrievalBackend):
  """Concrete image retrieval backend for the LI-COR Odyssey Classic."""

  def __init__(self, driver: OdysseyDriver) -> None:
    super().__init__()
    self._driver = driver

  async def list_groups(self) -> List[str]:
    html = await self._driver.list_scan_groups()
    return self._driver.parse_select_options(html, "avail")

  async def list_scans(self, group: str) -> List[str]:
    html = await self._driver.list_scan_groups()
    return self._driver.parse_select_options(html, "preset")

  async def download(self, group: str, scan_name: str) -> bytes:
    """Download both 700 and 800 nm TIFFs concatenated."""
    ch700 = await self._driver.download_tiff(group, scan_name, 700)
    ch800 = await self._driver.download_tiff(group, scan_name, 800)
    return ch700 + ch800

  # -- Vendor extensions ---------------------------------------------------

  async def download_channel(
    self, group: str, scan_name: str, channel: int
  ) -> bytes:
    """Download a single channel TIFF (700 or 800)."""
    return await self._driver.download_tiff(group, scan_name, channel)

  async def get_preview(
    self,
    group: str,
    scan_name: str,
    contrast_700: int = 5,
    contrast_800: int = 5,
    channels: str = "700 800",
    background: str = "black",
  ) -> bytes:
    """Fetch a JPEG preview rendered by the instrument."""
    return await self._driver.get_jpeg_preview(
      group, scan_name,
      contrast_700=contrast_700,
      contrast_800=contrast_800,
      channels=channels,
      background=background,
    )

  async def download_scan_log(self, group: str, scan_name: str) -> str:
    """Download the scan log for a completed scan."""
    return await self._driver.download_scan_log(group, scan_name)

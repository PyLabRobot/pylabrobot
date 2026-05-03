"""Odyssey image retrieval backend — protocol logic for /scan/image.

Owns the image-retrieval protocol: XML query encoding, TIFF /
JPEG / log GETs, and HTML parsing of the scan-list dropdown. The
driver only ships the bytes back.
"""

from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import quote

from pylabrobot.capabilities.scanning.image_retrieval import ImageRetrievalBackend

from .driver import OdysseyDriver
from .errors import OdysseyImageError

logger = logging.getLogger(__name__)


_IMAGE_BASE = "/scanapp/imaging/nonjava"
_SCAN_LIST_PATH = "/scanapp/scan/nonjava/"
_SCAN_IMAGE_PATH = "/scan/image"
_SAVELOG_URL_PATH = f"{_IMAGE_BASE}/savelog.pl"


def _tiff_xml(group: str, scan_name: str, channel: int) -> str:
  """Build the XML query string for a TIFF download."""
  return (
    f"<image><in>"
    f"<scangroup>{group}</scangroup>"
    f"<scan>{scan_name}</scan>"
    f"<format>tiff</format>"
    f"<channel>{channel}</channel>"
    f"<clip><x0>0</x0><x1>0</x1><y0>0</y0><y1>0</y1></clip>"
    f"</in></image>"
  )


def _jpeg_xml(
  group: str,
  scan_name: str,
  contrast_700: int = 5,
  contrast_800: int = 5,
  channels: str = "700 800",
  background: str = "black",
  clip: tuple[int, int, int, int] = (0, 0, 0, 0),
  vflip: bool = True,
  hflip: bool = True,
  zoom: int = 1,
) -> str:
  """Build the XML query string for a JPEG preview."""
  x0, x1, y0, y1 = clip
  return (
    f"<image><in>"
    f"<scangroup>{group}</scangroup>"
    f"<scan>{scan_name}</scan>"
    f"<zoom>{zoom}</zoom>"
    f"<contrast700>{contrast_700}</contrast700>"
    f"<contrast800>{contrast_800}</contrast800>"
    f"<channel>{channels}</channel>"
    f"<background>{background}</background>"
    f"<clip><x0>{x0}</x0><x1>{x1}</x1><y0>{y0}</y0><y1>{y1}</y1></clip>"
    f"<vflip>{'true' if vflip else 'false'}</vflip>"
    f"<hflip>{'true' if hflip else 'false'}</hflip>"
    f"</in></image>"
  )


def _parse_select_options(html: str, select_name: str) -> List[str]:
  """Extract <option value="..."> values from an HTML <select>."""
  pattern = (
    rf'<select[^>]*name=["\']?{select_name}["\']?[^>]*>'
    r"(.*?)</select>"
  )
  match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
  if not match:
    return []
  return re.findall(
    r'<option[^>]*value=["\']?([^"\'>\s]+)',
    match.group(1),
    re.IGNORECASE,
  )


class OdysseyImageRetrievalBackend(ImageRetrievalBackend):
  """Concrete image retrieval backend for the LI-COR Odyssey Classic."""

  def __init__(self, driver: OdysseyDriver) -> None:
    super().__init__()
    self._driver = driver

  async def list_groups(self) -> List[str]:
    _, html, _ = await self._driver.get(_SCAN_LIST_PATH)
    return _parse_select_options(html, "avail")

  async def list_scans(self, group: str) -> List[str]:
    _, html, _ = await self._driver.get(_SCAN_LIST_PATH)
    return _parse_select_options(html, "preset")

  async def download(self, group: str, scan_name: str) -> bytes:
    """Download both 700 and 800 nm TIFFs concatenated."""
    ch700 = await self.download_channel(group, scan_name, 700)
    ch800 = await self.download_channel(group, scan_name, 800)
    return ch700 + ch800

  # -- Vendor extensions ---------------------------------------------------

  async def download_channel(
    self, group: str, scan_name: str, channel: int
  ) -> bytes:
    """Download a single channel TIFF (700 or 800).

    Verifies the byte count matches the server's Content-Length when
    present so partial reads don't masquerade as success. Retries
    transient connection errors via the driver.
    """
    xml = _tiff_xml(group, scan_name, channel)
    path = f"{_SCAN_IMAGE_PATH}/{quote(scan_name)}-{channel}.tif"
    logger.info("Downloading TIFF: %s channel %d", scan_name, channel)

    status, data, _, content_length = await self._driver.get_bytes(
      path, params={"xml": xml}, with_retry=True,
    )
    if status != 200:
      raise OdysseyImageError(
        f"TIFF download failed for {scan_name}-{channel}: HTTP {status}"
      )
    if content_length is not None and len(data) != content_length:
      raise OdysseyImageError(
        f"Truncated TIFF for {scan_name}-{channel}: got {len(data)} bytes, "
        f"expected {content_length} (Content-Length)"
      )
    logger.info(
      "Downloaded %s-%d.tif: %d bytes", scan_name, channel, len(data),
    )
    return data

  async def get_preview(
    self,
    group: str,
    scan_name: str,
    contrast_700: int = 5,
    contrast_800: int = 5,
    channels: str = "700 800",
    background: str = "black",
  ) -> bytes:
    """Fetch a JPEG preview rendered server-side."""
    xml = _jpeg_xml(
      group, scan_name,
      contrast_700=contrast_700,
      contrast_800=contrast_800,
      channels=channels,
      background=background,
    )
    status, data, _, _ = await self._driver.get_bytes(
      _SCAN_IMAGE_PATH, params={"xml": xml},
    )
    if status != 200:
      raise OdysseyImageError(f"JPEG preview failed: HTTP {status}")
    return data

  async def download_scan_log(self, group: str, scan_name: str) -> str:
    """Download the scan log for a completed scan."""
    _, body, _ = await self._driver.get(
      _SAVELOG_URL_PATH,
      params={"group": group, "scan": scan_name},
    )
    return body

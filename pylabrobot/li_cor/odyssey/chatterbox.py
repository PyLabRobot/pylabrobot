"""In-memory HTTP responses for exercising Odyssey control without an instrument."""

from __future__ import annotations

import logging
import struct
from html import escape
from typing import Mapping, Optional
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from pylabrobot.io.http import HTTP, HTTPResponse

from . import protocol

logger = logging.getLogger(__name__)


def _tiff() -> bytes:
  """Create a one-pixel, 16-bit TIFF without an imaging dependency."""
  offset = 8 + 2 + 9 * 12 + 4
  entries = [
    (256, 4, 1),
    (257, 4, 1),
    (258, 3, 16),
    (259, 3, 1),
    (262, 3, 1),
    (273, 4, offset),
    (277, 3, 1),
    (278, 4, 1),
    (279, 4, 2),
  ]
  return (
    b"II*\x00"
    + struct.pack("<I", 8)
    + struct.pack("<H", len(entries))
    + b"".join(struct.pack("<HHII", tag, kind, 1, value) for tag, kind, value in entries)
    + struct.pack("<I", 0)
    + b"\x01\x00"
  )


class OdysseyChatterbox(HTTP):
  """HTTP simulation for ``OdysseyClassic(io=OdysseyChatterbox())``.

  Scan progress advances by 25 percent on each status read. The stored TIFFs are one-pixel
  synthetic images. No socket is opened and no background tasks are created.
  """

  def __init__(self) -> None:
    super().__init__("Odyssey chatterbox", "http://chatterbox")
    self._connected = False
    self.state = "Idle"
    self.progress = 0
    self._form: dict[str, str] = {}
    self.scans: dict[str, dict[str, dict[int, bytes]]] = {
      "odyssey": {"test_scan": {700: _tiff(), 800: _tiff()}}
    }

  async def setup(self) -> None:
    """Enable the simulated connection."""
    self._connected = True

  async def stop(self) -> None:
    """Close the simulated connection."""
    self._connected = False

  def _save_scan(self) -> None:
    """Store an image for each enabled channel of the configured scan."""
    images = {ch: _tiff() for ch in (700, 800) if f"chan{ch}" in self._form}
    self.scans.setdefault(self._form["scangroup"], {})[self._form["scan"]] = images

  @staticmethod
  def _response(body: str = "", status: int = 200) -> HTTPResponse:
    """Build a text response from the simulated web server."""
    return HTTPResponse(status, body.encode(), {"content-type": "text/html; charset=utf-8"})

  async def request_raw(
    self,
    method: str,
    path: str,
    body: Optional[bytes] = None,
    *,
    headers: Optional[Mapping[str, str]] = None,
    allow_redirects: bool = True,
  ) -> HTTPResponse:
    """Handle a scan-control, status, or image request entirely in memory."""
    if not self._connected:
      raise RuntimeError("Odyssey chatterbox is not set up")
    parsed = urlsplit(path)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    form = {key: values[0] for key, values in parse_qs((body or b"").decode()).items()}
    path = parsed.path
    logger.info("Odyssey chatterbox %s %s", method, path)
    if path == protocol._CONFIGURE_URL_PATH and method == "POST":
      if self.state in ("Scanning", "Paused"):
        return self._response('<Error shorterror="Busy">Scanner is busy</Error>')
      self._form = form
      self.state = "Initializing"
      self.progress = 0
      return HTTPResponse(302, b"", {"location": f"{protocol._INITIALIZING_URL_PATH}?timeout=7"})
    if path == protocol._INITIALIZING_URL_PATH:
      if query.get("timeout") == "1":
        self.state = "Configured"
        return HTTPResponse(302, b"", {"location": f"{protocol._SCAN_BASE}/console.pl"})
      return self._response("Initializing")
    if path == f"{protocol._SCAN_BASE}/console.pl":
      return self._response("Console")
    if path == protocol._COMMAND_URL_PATH or path == protocol._STOP_FROM_STATUS_PATH:
      action = query.get("action", form.get("action", "")).lower()
      if action == "start":
        if self.state not in ("Configured", "Paused"):
          return self._response('<Error shorterror="Not configured">Configure first</Error>')
        self.state = "Scanning"
      elif action == "pause":
        if self.state == "Scanning":
          self.state = "Paused"
      elif action == "stop":
        if self.state in ("Scanning", "Paused"):
          self._save_scan()
        self.state = "Stopped"
      elif action == "cancel":
        self.state = "Idle"
        self.progress = 0
        self._form = {}
      else:
        return self._response("Unknown action", 400)
      return HTTPResponse(302, b"", {"location": f"{protocol._SCAN_BASE}/console.pl"})
    if path == protocol._STATUS_URL_PATH:
      if self.state == "Scanning":
        self.progress = min(100, self.progress + 25)
        if self.progress == 100:
          self._save_scan()
          self.state = "Idle"
      return self._response(
        f"<p>Scanner Status: {self.state}</p><p>Current User: chatterbox</p>"
        f"<p>Percent Complete: {self.progress}%</p><p>Time Remaining: 0 seconds</p>"
        "<p>Lid Status: Closed</p>"
      )
    if path == protocol._SCAN_LIST_PATH:
      group = query.get("avail", "odyssey")
      groups = "".join(f'<option value="{escape(g)}">{escape(g)}</option>' for g in self.scans)
      scans = "".join(
        f'<option value="{escape(n)}">{escape(n)}</option>' for n in self.scans.get(group, {})
      )
      return self._response(
        f'<select name="avail">{groups}</select><select name="preset">{scans}</select>'
      )
    if path.startswith(protocol._SCAN_IMAGE_PATH):
      xml = ElementTree.fromstring(query["xml"])
      group = xml.findtext("in/scangroup", "")
      name = xml.findtext("in/scan", "")
      channel = xml.findtext("in/channel", "700")
      images = self.scans.get(group, {}).get(name, {})
      if xml.findtext("in/format") != "tiff":
        return self._response("JPEG rendering is not implemented by the chatterbox", 501)
      if int(channel) not in images:
        return self._response("Image not found", 404)
      data = images[int(channel)]
      return HTTPResponse(
        200, data, {"content-type": "image/tiff", "content-length": str(len(data))}
      )
    if path == protocol._INFO_URL_PATH:
      return self._response(
        "<p>Dimensions: 1 x 1</p><p>File Size: 124 bytes</p><p>Time Left: 0 seconds</p>"
      )
    if path == protocol._TIME_URL_PATH:
      return self._response("Estimated Scan Time: 0 hours 0 minutes 1 seconds")
    if path == protocol._SAVELOG_URL_PATH:
      return self._response("Chatterbox scan log")
    if path == "/scanapp/help/instinfo.pl":
      return self._response("Odyssey Classic chatterbox")
    if path == "/scanapp/admin/admin/index":
      self.state = "Idle"
      return self._response("Chatterbox shut down")
    return self._response("Unknown endpoint", 404)

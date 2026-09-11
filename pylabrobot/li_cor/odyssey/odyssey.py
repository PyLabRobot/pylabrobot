"""Direct HTTP control of the LI-COR Odyssey Classic (model 9120)."""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Callable, Literal, Optional, Union
from urllib.parse import quote, urlencode, urljoin

from pylabrobot.io.http import HTTP, HTTPResponse

from . import protocol
from .errors import OdysseyError, OdysseyImageError, OdysseyScanError, OdysseyStatusError

logger = logging.getLogger(__name__)

OdysseyState = Literal[
  "Idle", "Configured", "Initializing", "Scanning", "Paused", "Stopped", "Completed", "Failed"
]
_STATE_MAP: dict[str, OdysseyState] = {
  "idle": "Idle",
  "configured": "Configured",
  "initializing": "Initializing",
  "scanning": "Scanning",
  "escanning": "Scanning",
  "paused": "Paused",
  "stopped": "Stopped",
  "completed": "Completed",
  "failed": "Failed",
  "error": "Failed",
}
_TERMINAL_STATES = {"Idle", "Stopped", "Completed", "Failed"}


@dataclass(frozen=True)
class OdysseyStatus:
  """Instrument state, progress in percent, and the firmware's remaining-time text."""

  state: OdysseyState
  current_user: str = ""
  progress: float = 0
  time_remaining: str = ""
  lid_open: Optional[bool] = None


@dataclass(frozen=True)
class StopResult:
  """Channel images available after a graceful stop; they may contain a partial scan."""

  state: str
  partial: bool
  channels_available: list[int]


def normalize_state(raw: str) -> OdysseyState:
  """Normalize known firmware spellings, rejecting unknown states."""
  try:
    return _STATE_MAP[raw.strip().lower()]
  except KeyError as error:
    raise OdysseyStatusError(f"Unknown Odyssey scanner state: {raw!r}") from error


class OdysseyClassic:
  """LI-COR Odyssey Classic infrared scanner using its embedded HTTP interface.

  Lengths, including resolution, are in millimeters. Credentials can be supplied explicitly or
  through ODYSSEY_USER / ODYSSEY_PASS. ODYSSEY_HOST supplies the host when omitted.
  Pass an HTTP transport as ``io`` to use a simulated or recorded connection.
  """

  def __init__(
    self,
    host: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    port: int = 80,
    timeout: float = 60,
    io: Optional[HTTP] = None,
  ) -> None:
    self.host = host or os.environ.get("ODYSSEY_HOST", "")
    self.port = port
    self.timeout = timeout
    if not 1 <= port <= 65535:
      raise ValueError("port must be between 1 and 65535")
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be finite and greater than zero")
    if io is None:
      if not self.host or any(part in self.host for part in ("://", "/", "?", "#")):
        raise ValueError("Provide an Odyssey hostname or IP address, without a URL scheme or path")
      username = username or os.environ.get("ODYSSEY_USER", "")
      password = password or os.environ.get("ODYSSEY_PASS", "")
      if not username or not password:
        raise ValueError("Provide username and password, or set ODYSSEY_USER / ODYSSEY_PASS")
      if ":" in username:
        raise ValueError("HTTP Basic Auth usernames cannot contain ':'")
      credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
      io = HTTP(
        human_readable_device_name="LI-COR Odyssey Classic",
        base_url=f"http://{self.host}:{port}",
        headers={"Authorization": f"Basic {credentials}", "Connection": "close"},
        timeout=timeout,
      )
    self.io = io
    self._connected = False
    self._current_scan: Optional[tuple[str, str]] = None
    self._channels: tuple[int, ...] = ()
    self._last_status_html = ""
    self._last_status_http = 0

  @property
  def connected(self) -> bool:
    """Whether setup has opened and checked the transport."""
    return self._connected

  async def setup(self) -> None:
    """Open the HTTP transport and verify the authenticated status page."""
    if self._connected:
      return
    logger.warning(
      "Odyssey Classic port is untested on hardware; please report verification results"
    )
    await self.io.setup()
    self._connected = True
    try:
      await self.request_status()
    except BaseException:
      await self.stop()
      raise
    logger.info("Connected to Odyssey at %s", self.io.base_url)

  async def stop(self) -> None:
    """Close the connection. Use stop_scan or cancel_scan to interrupt acquisition."""
    try:
      await self.io.stop()
    finally:
      self._connected = False
    logger.info("Disconnected from Odyssey at %s", self.io.base_url)

  async def __aenter__(self) -> OdysseyClassic:
    """Set up the instrument for an async context."""
    await self.setup()
    return self

  async def __aexit__(self, exc_type, exc_value, traceback) -> None:
    """Close the transport when leaving an async context."""
    await self.stop()

  def serialize(self) -> dict:
    """Return connection settings, excluding credentials."""
    return {"host": self.host, "port": self.port, "timeout": self.timeout}

  async def _request(
    self,
    method: str,
    path: str,
    *,
    params: Optional[dict[str, str]] = None,
    form: Optional[dict[str, str]] = None,
    allow_redirects: bool = True,
  ) -> HTTPResponse:
    """Exchange an Odyssey form or query and reject HTTP and firmware errors."""
    if not self._connected:
      raise RuntimeError("Odyssey is not connected; call setup() first")
    if params:
      path += ("&" if "?" in path else "?") + urlencode(params)
    body = urlencode(form).encode("ascii") if form is not None else None
    response = await self.io.request_raw(
      method,
      path,
      body,
      headers={"Content-Type": "application/x-www-form-urlencoded"} if form is not None else None,
      allow_redirects=allow_redirects,
    )
    if response.status >= 400 or response.status < 200:
      raise OdysseyError(f"{method} {path.split('?')[0]} returned HTTP {response.status}")
    if response.status not in range(200, 300) and response.status != 302:
      raise OdysseyError(f"Unexpected HTTP {response.status} for {path.split('?')[0]}")
    return response

  async def configure_scan(
    self,
    name: str = "scan",
    *,
    group: str = "odyssey",
    resolution: Union[float, Literal["preview"]] = 0.169,
    quality: Literal["lowest", "low", "medium", "high", "highest"] = "medium",
    intensity_700: str = "5",
    intensity_800: str = "5",
    channel_700: bool = True,
    channel_800: bool = True,
    origin_x: float = 0,
    origin_y: float = 0,
    width: float = 100,
    height: float = 100,
    focus: float = 0,
    comment: str = "",
    preset: str = "",
  ) -> None:
    """Configure a scan and complete the firmware's seven-step initialization.

    The scan rectangle and focus are in mm. Resolutions are 0.021, 0.042, 0.084, 0.169,
    or 0.337 mm, or "preview". Intensities accept L2/L1.5/L1/L0.5 or 0.5 through 10 in
    steps of 0.5. Start acquisition separately with start_scan() or scan().
    """
    if not name or re.search(r'[;/?:@=&<>"#%{}|^~\[\]]', name):
      raise ValueError("Scan name is empty or contains a character rejected by the firmware")
    if not group:
      raise ValueError("group cannot be empty")
    if resolution != "preview" and resolution not in (0.021, 0.042, 0.084, 0.169, 0.337):
      raise ValueError("Unsupported resolution in mm")
    if quality not in ("lowest", "low", "medium", "high", "highest"):
      raise ValueError("Unsupported scan quality")
    intensities = {f"{n / 2:g}" for n in range(1, 21)} | {"L2", "L1.5", "L1", "L0.5"}
    if intensity_700 not in intensities or intensity_800 not in intensities:
      raise ValueError("Unsupported laser intensity")
    if not channel_700 and not channel_800:
      raise ValueError("At least one acquisition channel must be enabled")
    if not all(math.isfinite(v) for v in (origin_x, origin_y, width, height, focus)):
      raise ValueError("Scan dimensions and focus must be finite")
    if min(origin_x, origin_y) < 0 or min(width, height) <= 0:
      raise ValueError("Origins must be non-negative and scan dimensions must be positive")
    if origin_x + width > 250 or origin_y + height > 250 or not 0 <= focus <= 4:
      raise ValueError("Scan rectangle must fit within 250 x 250 mm; focus must be 0–4 mm")
    resolution_text = "preview" if resolution == "preview" else f"{resolution * 1000:g}"
    form = {
      "channel": "x",
      "scan": name,
      "scangroup": group,
      "avail": group,
      "preset": preset,
      "resolution": resolution_text,
      "quality": quality,
      "intensity700": intensity_700,
      "intensity800": intensity_800,
      "x0": f"{origin_x / 10:g}",
      "y0": f"{origin_y / 10:g}",
      "width": f"{width / 10:g}",
      "height": f"{height / 10:g}",
      "x1": f"{(origin_x + width) / 10:g}",
      "y1": f"{(origin_y + height) / 10:g}",
      "focus": f"{focus:g}",
      "comment": comment,
      "prename": "",
    }
    if channel_700:
      form["chan700"] = "chan700"
    if channel_800:
      form["chan800"] = "chan800"
    logger.info("Configuring Odyssey scan %s in %s", name, group)
    self._current_scan = None
    self._channels = ()
    response = await self._request(
      "POST", protocol._CONFIGURE_URL_PATH, form=form, allow_redirects=False
    )
    self._check_scan_response(response)
    await self._follow_redirect(response, protocol._CONFIGURE_URL_PATH)
    for step in range(7, 0, -1):
      response = await self._request(
        "GET",
        protocol._INITIALIZING_URL_PATH,
        params={"scan": name, "scangroup": group, "timeout": str(step)},
        allow_redirects=False,
      )
      self._check_scan_response(response)
      if step == 1 and response.status == 302:
        await self._follow_redirect(response, protocol._INITIALIZING_URL_PATH)
      else:
        await asyncio.sleep(1)
    self._current_scan = (group, name)
    self._channels = tuple(
      ch for ch, enabled in ((700, channel_700), (800, channel_800)) if enabled
    )

  @staticmethod
  def _check_scan_response(response: HTTPResponse) -> None:
    """Reject CGI error pages even when the HTTP status is 200."""
    body = response.text()
    error = protocol._parse_error_block(body)
    if error or re.search(r"<error\b|<title>\s*error|\bbusy\b|not configured", body, re.I):
      raise OdysseyScanError(error or body[:500])

  async def _follow_redirect(self, response: HTTPResponse, source_path: str) -> None:
    """Follow the configure/initialization redirect within the device origin."""
    if response.status == 302:
      location = response.headers.get("location")
      if not location:
        raise OdysseyScanError("Odyssey returned a redirect without a Location header")
      redirected = await self._request("GET", urljoin(source_path, location), allow_redirects=False)
      self._check_scan_response(redirected)

  async def _scan_command(self, action: str) -> None:
    """Send a scan-console action once; state-changing requests are not retried."""
    logger.info("Odyssey scan command: %s", action)
    response = await self._request(
      "GET", protocol._COMMAND_URL_PATH, params={"action": action}, allow_redirects=False
    )
    self._check_scan_response(response)

  async def start_scan(self) -> None:
    """Start a configured scan, or resume a paused scan."""
    if self._current_scan is None:
      raise OdysseyScanError("Configure a scan before starting acquisition")
    await self._scan_command("start")

  async def stop_scan(self) -> None:
    """Finish the current line and retain the partially acquired image."""
    await self._scan_command("stop")

  async def pause_scan(self) -> None:
    """Pause acquisition; resume with start_scan()."""
    await self._scan_command("pause")

  async def cancel_scan(self) -> None:
    """Abort acquisition and discard partial output."""
    await self._scan_command("cancel")
    self._current_scan = None
    self._channels = ()

  async def force_stop(self) -> None:
    """Stop a paused or stuck scanner through the status-page control."""
    response = await self._request(
      "POST",
      protocol._STOP_FROM_STATUS_PATH,
      form={"formContext": "1", "action": "Stop"},
      allow_redirects=False,
    )
    self._check_scan_response(response)

  async def request_status(self) -> OdysseyStatus:
    """Read the status page, rejecting unknown states instead of reporting false completion."""
    response = await self._request("GET", protocol._STATUS_URL_PATH)
    self._last_status_html = response.text()
    self._last_status_http = response.status
    parsed = protocol._parse_status_html(self._last_status_html)
    state = normalize_state(parsed["state"])
    progress_text = parsed["progress"].rstrip("% ")
    try:
      progress = float(progress_text) if progress_text else 0.0
    except ValueError as error:
      raise OdysseyStatusError(f"Invalid progress: {parsed['progress']!r}") from error
    if not math.isfinite(progress) or not 0 <= progress <= 100:
      raise OdysseyStatusError(f"Invalid progress: {progress}")
    lid = parsed["lid_status"].lower()
    return OdysseyStatus(
      state=state,
      current_user=parsed["current_user"],
      progress=progress,
      time_remaining=parsed["time_remaining"],
      lid_open=True if lid == "open" else False if lid == "closed" else None,
    )

  @property
  def last_status_html(self) -> str:
    """The most recently parsed status-page HTML, for diagnostics."""
    return self._last_status_html

  @property
  def last_status_http(self) -> int:
    """The HTTP status of the most recently parsed status page."""
    return self._last_status_http

  async def request_progress(self) -> dict[str, str]:
    """Read the configured scan's dimensions, file size, and remaining-time text."""
    if self._current_scan is None:
      raise OdysseyScanError("No scan has been configured")
    group, name = self._current_scan
    response = await self._request(
      "GET",
      protocol._INFO_URL_PATH,
      params={"scan": name, "group": group, "update": "Off", "console": "yes"},
    )
    return protocol._parse_info_html(response.text())

  async def estimate_time(
    self,
    *,
    resolution: float = 0.169,
    quality: str = "medium",
    origin_x: float = 0,
    origin_y: float = 0,
    width: float = 100,
    height: float = 100,
  ) -> str:
    """Request the firmware's scan-time estimate; lengths and resolution are in mm."""
    response = await self._request(
      "GET",
      protocol._TIME_URL_PATH,
      params={
        "resolution": f"{resolution * 1000:g}",
        "quality": quality,
        "x0": f"{origin_x / 10:g}",
        "y0": f"{origin_y / 10:g}",
        "x1": f"{(origin_x + width) / 10:g}",
        "y1": f"{(origin_y + height) / 10:g}",
      },
    )
    match = re.search(
      r"Estimated Scan Time.*?(\d+ hours? \d+ minutes? \d+ seconds?)",
      response.text(),
      re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else response.text()

  async def list_groups(self) -> list[str]:
    """List saved scan groups from the instrument's scan page."""
    response = await self._request("GET", protocol._SCAN_LIST_PATH)
    return protocol._parse_select_options(response.text(), "avail")

  async def list_scans(self, group: str) -> list[str]:
    """List scans in a group using the scan page's group selector."""
    response = await self._request("GET", protocol._SCAN_LIST_PATH, params={"avail": group})
    return protocol._parse_select_options(response.text(), "preset")

  async def download_channel(self, group: str, scan_name: str, channel: int) -> bytes:
    """Download one complete TIFF for the 700 or 800 nm channel."""
    if channel not in (700, 800):
      raise ValueError("channel must be 700 or 800")
    response = await self._request(
      "GET",
      f"{protocol._SCAN_IMAGE_PATH}/{quote(scan_name, safe='')}-{channel}.tif",
      params={"xml": protocol._tiff_xml(group, scan_name, channel)},
    )
    length = response.headers.get("content-length")
    if length is not None and len(response.body) != int(length):
      raise OdysseyImageError(f"Truncated TIFF: expected {length} bytes, got {len(response.body)}")
    if response.body[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
      raise OdysseyImageError("The instrument did not return a TIFF image")
    logger.info(
      "Downloaded Odyssey scan %s channel %d: %d bytes", scan_name, channel, len(response.body)
    )
    return response.body

  async def download(self, group: str, scan_name: str) -> dict[int, bytes]:
    """Download both channels as separate TIFF files, keyed by wavelength."""
    return {
      channel: await self.download_channel(group, scan_name, channel) for channel in (700, 800)
    }

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
    response = await self._request(
      "GET",
      protocol._SCAN_IMAGE_PATH,
      params={
        "xml": protocol._jpeg_xml(
          group,
          scan_name,
          contrast_700=contrast_700,
          contrast_800=contrast_800,
          channels=channels,
          background=background,
        )
      },
    )
    return response.body

  async def download_scan_log(self, group: str, scan_name: str) -> str:
    """Download the saved scan log."""
    response = await self._request(
      "GET", protocol._SAVELOG_URL_PATH, params={"group": group, "scan": scan_name}
    )
    return response.text()

  async def wait_until_done(
    self,
    *,
    timeout: float = 3600,
    poll_interval: float = 1,
    on_progress: Optional[Callable[[OdysseyStatus], None]] = None,
    require_fresh: bool = True,
  ) -> OdysseyStatus:
    """Wait for a terminal state, optionally requiring a transition out of an initial idle state."""
    if (
      not math.isfinite(timeout)
      or timeout <= 0
      or not math.isfinite(poll_interval)
      or poll_interval <= 0
    ):
      raise ValueError("timeout and poll_interval must be finite and positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    require_transition = require_fresh
    while True:
      reading = await self.request_status()
      if on_progress is not None:
        on_progress(reading)
      if reading.state == "Failed":
        raise OdysseyScanError("Odyssey reported a failed scan")
      if reading.state not in _TERMINAL_STATES:
        require_transition = False
      elif not require_transition:
        return reading
      remaining = deadline - loop.time()
      if remaining <= 0:
        raise TimeoutError(
          f"Scan did not complete within {timeout:g} s (last state={reading.state})"
        )
      await asyncio.sleep(min(poll_interval, remaining))

  async def scan(
    self,
    *,
    timeout: float = 3600,
    poll_interval: float = 1,
    on_progress: Optional[Callable[[OdysseyStatus], None]] = None,
  ) -> OdysseyStatus:
    """Start the configured scan and wait for completion. Download its images separately."""
    await self.start_scan()
    return await self.wait_until_done(
      timeout=timeout, poll_interval=poll_interval, on_progress=on_progress, require_fresh=False
    )

  async def stop_and_save(self, timeout: float = 15, poll_interval: float = 1) -> StopResult:
    """Stop acquisition, wait for idle, and report which configured channels have TIFFs."""
    await self.stop_scan()
    await self.wait_until_done(timeout=timeout, poll_interval=poll_interval, require_fresh=False)
    available = []
    if self._current_scan is not None:
      group, name = self._current_scan
      for channel in self._channels:
        try:
          await self.download_channel(group, name, channel)
          available.append(channel)
        except OdysseyError as error:
          logger.info("Channel %d unavailable after stop: %s", channel, error)
    return StopResult(state="Stopped", partial=bool(available), channels_available=available)

  async def shutdown_instrument(self) -> str:
    """Power off the instrument. Restarting the hardware can take 30 minutes."""
    logger.warning("Shutting down Odyssey instrument")
    response = await self._request(
      "GET", "/scanapp/admin/admin/index", params={"action": "InitiateShutdown"}
    )
    return response.text()

  async def get_instrument_info(self) -> str:
    """Read the instrument's serial number and software-version page as HTML."""
    response = await self._request("GET", "/scanapp/help/instinfo.pl")
    return response.text()

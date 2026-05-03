"""LI-COR Odyssey Classic HTTP driver.

The Odyssey Classic (model 9120) runs an embedded Linux web server
(Apache/1.3.27 + mod_perl/1.23 on Red Hat Linux) that serves Perl CGI
scripts. This driver wraps an aiohttp session with HTTP Basic Auth
and provides typed methods for each endpoint.

API reverse-engineered from HAR captures of the browser interface.

Endpoints:
  Scan setup:
    POST /scanapp/scan/nonjava/configure.pl     — configure scan parameters
    GET  /scanapp/scan/nonjava/command.pl       — ?action=start|stop|pause|cancel
    GET  /scanapp/scan/nonjava/console.pl       — scan console page
    GET  /scanapp/scan/nonjava/initializing.pl  — ?scan=...&scangroup=...&timeout=...
    GET  /scanapp/scan/nonjava/time.pl          — scan time estimate
  Imaging:
    GET  /scanapp/imaging/nonjava/info.pl       — scan progress
    POST /scanapp/imaging/nonjava/openimage.pl  — render JPEG preview
    GET  /scan/image?xml=<xml>                  — fetch JPEG preview
    GET  /scan/image/<name>-<ch>.tif?xml=<xml>  — download raw TIFF
    GET  /scanapp/imaging/nonjava/savelog.pl    — download scan log
  Status:
    GET  /scanapp/util/status/                  — instrument status page
    POST /scanapp/util/status/status            — stop scan from status page
  Admin:
    POST /scanapp/admin/admin/index             — ?action=InitiateShutdown

Auth: HTTP Basic Auth, realm "LICOR-Odyssey".
Transport: TCP/IP, 10/100Base-T Ethernet.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver

from .errors import OdysseyImageError, OdysseyScanError

logger = logging.getLogger(__name__)


# Transport errors worth retrying — connection-level transients only.
# Server-level failures (4xx / 5xx) are NOT retried.
_RETRYABLE_EXCEPTIONS = (
  aiohttp.ClientConnectionError,
  asyncio.TimeoutError,
  ConnectionResetError,
  OSError,
)
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_DELAY = 0.25

_SCAN_BASE = "/scanapp/scan/nonjava"
_IMAGE_BASE = "/scanapp/imaging/nonjava"
_STATUS_BASE = "/scanapp/util/status"

# Hardware protocol invariants — contracts with the Odyssey Classic
# firmware 2.1.12. Do NOT change without re-verifying against the unit.
_CONFIGURE_URL_PATH = f"{_SCAN_BASE}/configure.pl"
_CHANNEL_SENTINEL = "x"            # hidden 'channel' field must be literal "x"
_INIT_POLL_STEPS = 7               # full 7→1 countdown after configure
DEFAULT_GROUP = "odyssey"          # default operational group

# Environment variables for HTTP Basic Auth credentials.
_CRED_ENV_USER = "ODYSSEY_USER"
_CRED_ENV_PASS = "ODYSSEY_PASS"


@dataclass
class OdysseyScanningParams(BackendParams):
  """Scan parameters for the Odyssey Classic.

  Field names align with the configure.pl form, captured from the
  browser interface. Pass an instance to :meth:`Scanning.configure` —
  the backend coerces it and forwards to the driver.

  Attributes:
    name: Scan name. Avoid ;/?:@=&<>"#%{}|^~[].
    group: Scan group name (e.g. "odyssey", "public").
    resolution: Resolution in micrometers — "21", "42", "84", "169",
      "337", or "preview".
    quality: "lowest", "low", "medium", "high", "highest".
    intensity_700: 700 nm channel intensity. L2, L1.5, L1, L0.5,
      0.5, 1, 1.5, 2, ..., 10 (in 0.5 steps).
    intensity_800: 800 nm channel intensity (same range).
    channel_700: Enable 700 nm acquisition.
    channel_800: Enable 800 nm acquisition.
    origin_x: Scan origin X in cm (0–25).
    origin_y: Scan origin Y in cm (0–25).
    width: Scan width in cm; origin_x + width <= 25.
    height: Scan height in cm; origin_y + height <= 25.
    focus: Focus offset in mm (0.0–4.0). 0 for membranes,
      ~1.0 for gels, 3.0 for microplates.
    comment: Free-text comment.
    preset: Preset name to load (empty for manual config).
  """

  name: str = "scan"
  group: str = DEFAULT_GROUP
  resolution: str = "169"
  quality: str = "medium"
  intensity_700: str = "5"
  intensity_800: str = "5"
  channel_700: bool = True
  channel_800: bool = True
  origin_x: int = 0
  origin_y: int = 0
  width: int = 10
  height: int = 10
  focus: float = 0.0
  comment: str = ""
  preset: str = ""

  def to_form_data(self) -> dict[str, str]:
    """Render as the form-data dict POSTed to configure.pl.

    Form field names match the HTML form exactly:
      channel, scan, scangroup, avail, preset, resolution, quality,
      intensity700, intensity800, chan700, chan800, x0, y0, width,
      height, x1, y1, focus, comment, prename
    """
    data: dict[str, str] = {
      "channel": _CHANNEL_SENTINEL,  # firmware quirk; must be literal "x"
      "scan": self.name,
      "scangroup": self.group,
      "avail": self.group,
      "preset": self.preset,
      "resolution": str(self.resolution),
      "quality": self.quality,
      "intensity700": str(self.intensity_700),
      "intensity800": str(self.intensity_800),
      "x0": str(self.origin_x),
      "y0": str(self.origin_y),
      "width": str(self.width),
      "height": str(self.height),
      "x1": str(self.origin_x + self.width),
      "y1": str(self.origin_y + self.height),
      "focus": str(self.focus),
      "comment": self.comment,
      "prename": "",
    }
    if self.channel_700:
      data["chan700"] = "chan700"
    if self.channel_800:
      data["chan800"] = "chan800"
    return data

  def to_time_params(self) -> dict[str, str]:
    """Query params for the time.pl scan-time estimate."""
    return {
      "resolution": str(self.resolution),
      "quality": self.quality,
      "x0": str(self.origin_x),
      "y0": str(self.origin_y),
      "x1": str(self.origin_x + self.width),
      "y1": str(self.origin_y + self.height),
    }


def _tiff_xml(group: str, scan_name: str, channel: int) -> str:
  """Build the XML query string for TIFF download."""
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
  """Build the XML query string for JPEG preview."""
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


class OdysseyDriver(Driver):
  """Driver (transport) for the LI-COR Odyssey Classic infrared imager.

  Wraps an aiohttp.ClientSession with HTTP Basic Auth. The capability
  backends share a single OdysseyDriver instance.

  Server: Apache/1.3.27 (Unix) (Red-Hat/Linux) mod_perl/1.23
  Auth realm: LICOR-Odyssey
  """

  def __init__(
    self,
    host: str,
    username: str,
    password: str,
    port: int = 80,
    timeout: float = 60.0,
    group: str = DEFAULT_GROUP,
  ) -> None:
    super().__init__()
    if not username or not password:
      raise ValueError(
        "OdysseyDriver requires both username and password. "
        f"Use OdysseyDriver.from_env() to read them from "
        f"{_CRED_ENV_USER}/{_CRED_ENV_PASS}."
      )
    self._host = host
    self._port = port
    self._timeout_seconds = timeout
    self._username = username
    self._password = password
    self._base_url = f"http://{host}:{port}"
    self._auth = aiohttp.BasicAuth(username, password)
    self._timeout = aiohttp.ClientTimeout(total=timeout)
    self._session: Optional[aiohttp.ClientSession] = None
    self._group = group
    # Diagnostic caches — populated by methods, exposed through helpers.
    self._last_status_html: str = ""
    self._last_status_http: int = 0
    self._last_configure_url: str = ""
    self._last_configure_http: int = 0
    self._last_configure_body: str = ""
    # Never log the password.
    logger.info(
      "OdysseyDriver initialised: host=%s group=%s %s=%s",
      host, group, _CRED_ENV_USER, username,
    )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self._host,
      "port": self._port,
      "timeout": self._timeout_seconds,
      "group": self._group,
    }

  @classmethod
  def from_env(
    cls,
    host: Optional[str] = None,
    port: int = 80,
    timeout: float = 60.0,
    group: str = DEFAULT_GROUP,
  ) -> "OdysseyDriver":
    """Construct from ODYSSEY_USER / ODYSSEY_PASS environment variables.

    Raises ValueError if either is missing — the driver does not silently
    fall back to default credentials. If ``host`` is None, ODYSSEY_HOST is
    read from the environment too.
    """
    username = os.environ.get(_CRED_ENV_USER, "")
    password = os.environ.get(_CRED_ENV_PASS, "")
    if not username or not password:
      missing = [
        name for name, val in (
          (_CRED_ENV_USER, username),
          (_CRED_ENV_PASS, password),
        ) if not val
      ]
      raise ValueError(
        f"Missing required environment variable(s): "
        f"{', '.join(missing)}."
      )
    if host is None:
      host = os.environ.get("ODYSSEY_HOST", "")
      if not host:
        raise ValueError("No host provided and ODYSSEY_HOST is unset.")
    return cls(
      host=host, username=username, password=password,
      port=port, timeout=timeout, group=group,
    )

  @property
  def group(self) -> str:
    return self._group

  @property
  def base_url(self) -> str:
    return self._base_url

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Open the HTTP session and verify connectivity + auth.

    ``Connection: close`` is forced on every request: the Odyssey's
    embedded Apache 1.3.27 doesn't always handle keep-alive cleanly
    when a second request arrives before the first response has been
    fully consumed. Closing per request is a couple of ms slower but
    eliminates the inter-request races we see in the field.
    """
    self._session = aiohttp.ClientSession(
      auth=self._auth,
      timeout=self._timeout,
      headers={"Connection": "close"},
    )
    # Verify connectivity — the home page is unprotected.
    async with self._session.get(self._base_url) as resp:
      if resp.status != 200:
        raise ConnectionError(
          f"Cannot reach Odyssey at {self._base_url} (HTTP {resp.status})"
        )
    # Verify auth — the scan page requires login.
    url = f"{self._base_url}{_SCAN_BASE}/"
    async with self._session.get(url) as resp:
      if resp.status == 401:
        raise ConnectionError(
          "Authentication failed — check username/password "
          "(realm: LICOR-Odyssey)"
        )
    logger.info("Connected to Odyssey at %s", self._base_url)

  async def stop(self) -> None:
    """Close the HTTP session."""
    if self._session is not None:
      await self._session.close()
      self._session = None

  def _check_session(self) -> aiohttp.ClientSession:
    if self._session is None:
      raise RuntimeError("OdysseyDriver not set up")
    return self._session

  # -- Scan control --------------------------------------------------------

  async def configure_scan(self, params: OdysseyScanningParams) -> str:
    """POST scan parameters to configure.pl.

    On success: 302 redirect to initializing.pl (7 s countdown).
    On error (scanner busy / name collision): 200 with HTML error page.

    The initializing.pl countdown takes ~7 seconds as the instrument
    configures the DSP, laser voltages, and motor positions.
    """
    session = self._check_session()
    url = f"{self._base_url}{_CONFIGURE_URL_PATH}"
    form_data = params.to_form_data()
    logger.info(
      "Configuring scan: name=%s group=%s res=%s quality=%s",
      params.name, params.group, params.resolution, params.quality,
    )
    logger.debug("Form data: %s", form_data)

    last_exc: Optional[Exception] = None
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
      try:
        async with session.post(
          url, data=form_data, allow_redirects=False
        ) as resp:
          body = await resp.text()
          self._last_configure_url = url
          self._last_configure_http = resp.status
          self._last_configure_body = body
          logger.info(
            "POST %s → HTTP %d, body length %d",
            url, resp.status, len(body),
          )
          if resp.status == 302:
            redirect = resp.headers.get("Location", "")
            logger.info("configure.pl redirect → %s", redirect)
            # Follow the redirect — this triggers hardware initialization.
            if redirect:
              redirect_url = (
                redirect if redirect.startswith("http")
                else f"{self._base_url}{redirect}"
              )
              async with session.get(
                redirect_url, allow_redirects=False,
              ) as init_resp:
                logger.info(
                  "Followed redirect → HTTP %d", init_resp.status,
                )
            return body
          # 4xx/5xx — server said no.
          if resp.status >= 400:
            raise OdysseyScanError(
              f"Scanner rejected configuration: "
              f"POST {url} → HTTP {resp.status}."
            )
          # 2xx with an explicit error page in the body. The firmware
          # embeds a structured ``<Error shorterror="X">message</Error>``
          # block; pull the short label and message out so callers see
          # "Scan already exists: foo already exists." instead of HTML.
          if "busy" in body.lower() or "<TITLE>Error</TITLE>" in body:
            m = re.search(
              r'<Error\s+shorterror="([^"]+)"\s*>\s*(.*?)\s*</Error>',
              body, re.IGNORECASE | re.DOTALL,
            )
            if m:
              short = m.group(1).strip()
              detail = re.sub(r"\s+", " ", m.group(2)).strip()
              raise OdysseyScanError(
                f"Scanner rejected configuration — {short}: {detail}"
              )
            raise OdysseyScanError(
              f"Scanner rejected configuration: {body[:500]}"
            )
          return body
      except _RETRYABLE_EXCEPTIONS as exc:
        last_exc = exc
        if attempt < _HTTP_RETRY_ATTEMPTS - 1:
          logger.warning(
            "configure_scan attempt %d/%d failed (%s) — retrying",
            attempt + 1, _HTTP_RETRY_ATTEMPTS, exc,
          )
          await asyncio.sleep(_HTTP_RETRY_DELAY)
          continue
    raise OdysseyScanError(
      f"configure_scan failed after {_HTTP_RETRY_ATTEMPTS} attempts: "
      f"{last_exc}"
    ) from last_exc

  async def wait_initialization(
    self,
    scan_name: str,
    group: str,
    timeout_steps: int = _INIT_POLL_STEPS,
  ) -> None:
    """Wait through the 7→1 initialization countdown.

    The instrument prepares motors and laser voltages during this
    window. Skipping the poll skips hardware prep and produces invalid
    scans.
    """
    session = self._check_session()
    logger.info("Waiting %d seconds for hardware initialization...",
                timeout_steps)
    for t in range(timeout_steps, 0, -1):
      url = f"{self._base_url}{_SCAN_BASE}/initializing.pl"
      params = {"scan": scan_name, "scangroup": group, "timeout": str(t)}
      logger.info("initializing.pl?timeout=%d", t)
      async with session.get(url, params=params, allow_redirects=False) as resp:
        if resp.status == 302 and t <= 1:
          redirect = resp.headers.get("Location", "")
          logger.info("Initialization complete → %s", redirect)
          if redirect:
            redirect_url = redirect if redirect.startswith("http") \
              else f"{self._base_url}{redirect}"
            async with session.get(
              redirect_url, allow_redirects=True
            ) as _:
              pass
          return
      await asyncio.sleep(1)
    logger.info("Initialization wait complete")

  async def start_scan(self) -> str:
    """Send start command. Scanner must be configured first."""
    return await self._scan_command("start")

  async def stop_scan(self) -> str:
    """Send stop command (finishes scan, saves files)."""
    return await self._scan_command("stop")

  async def pause_scan(self) -> str:
    """Send pause command."""
    return await self._scan_command("pause")

  async def cancel_scan(self) -> str:
    """Send cancel command (aborts scan, no save)."""
    return await self._scan_command("cancel")

  async def _scan_command(self, action: str) -> str:
    """Send command.pl?action=<action>.

    On success: 302 redirect to console.pl.
    On error (not configured): 200 with structured Error block.
    """
    session = self._check_session()
    url = f"{self._base_url}{_SCAN_BASE}/command.pl"
    params = {"action": action}
    logger.info("Scan command: %s", action)

    async with session.get(url, params=params, allow_redirects=False) as resp:
      body = await resp.text()
      logger.info("command.pl?action=%s → HTTP %d, body: %s",
                  action, resp.status, body[:300])
      if resp.status == 302:
        return body
      if "<Error" in body or "not configured" in body.lower():
        raise OdysseyScanError(
          f"Scan command '{action}' failed: {body[:500]}"
        )
      return body

  async def estimate_scan_time(self, params: OdysseyScanningParams) -> str:
    """Get estimated scan time from time.pl.

    Returns the time string, e.g. "0 hours 2 minutes 15 seconds".
    """
    session = self._check_session()
    url = f"{self._base_url}{_SCAN_BASE}/time.pl"
    async with session.get(url, params=params.to_time_params()) as resp:
      html = await resp.text()
      match = re.search(
        r"Estimated Scan Time.*?(\d+ hours? \d+ minutes? \d+ seconds?)",
        html, re.DOTALL | re.IGNORECASE,
      )
      return match.group(1) if match else html

  # -- Status --------------------------------------------------------------

  async def get_status(self) -> dict[str, str]:
    """Fetch and parse the instrument status page.

    Returns dict with keys: state, current_user, progress,
    time_remaining, lid_status. The most recent raw HTML response is
    cached on ``self._last_status_html`` for diagnostic use.
    """
    session = self._check_session()
    url = f"{self._base_url}{_STATUS_BASE}/"
    async with session.get(url) as resp:
      html = await resp.text()
      self._last_status_http = resp.status
    self._last_status_html = html
    parsed = self._parse_status_html(html)
    if parsed["state"] == "Unknown":
      logger.warning(
        "Status parser missed 'Scanner Status' (HTTP %s, %d bytes).",
        self._last_status_http, len(html),
      )
    return parsed

  async def stop_from_status(self) -> str:
    """Stop the scanner from the status/utilities page.

    The path to release a paused/stuck scanner without going through
    the scan console.
    """
    session = self._check_session()
    url = f"{self._base_url}{_STATUS_BASE}/status"
    data = {"formContext": "1", "action": "Stop"}
    async with session.post(url, data=data) as resp:
      return await resp.text()

  async def get_scan_progress(
    self, scan_name: str, group: str
  ) -> dict[str, str]:
    """Fetch scan progress from the imaging info panel.

    Returns dict with: dimensions, file_size, time_left.
    """
    session = self._check_session()
    url = f"{self._base_url}{_IMAGE_BASE}/info.pl"
    params = {
      "scan": scan_name,
      "group": group,
      "update": "Off",
      "console": "yes",
    }
    async with session.get(url, params=params) as resp:
      html = await resp.text()
    return self._parse_info_html(html)

  # -- Image retrieval -----------------------------------------------------

  async def download_tiff(
    self, group: str, scan_name: str, channel: int
  ) -> bytes:
    """Download a raw TIFF for one channel (700 or 800).

    URL: /scan/image/<name>-<channel>.tif?xml=<encoded-xml>

    Retries up to 3 times on transient connection errors with a 0.25 s
    backoff. HTTP 4xx/5xx fail immediately. Verifies the downloaded
    byte count matches Content-Length when present.
    """
    session = self._check_session()
    xml = _tiff_xml(group, scan_name, channel)
    url = (
      f"{self._base_url}/scan/image/"
      f"{quote(scan_name)}-{channel}.tif"
    )
    logger.info("Downloading TIFF: %s channel %d", scan_name, channel)

    last_exc: Optional[Exception] = None
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
      try:
        async with session.get(url, params={"xml": xml}) as resp:
          if resp.status != 200:
            raise OdysseyImageError(
              f"TIFF download failed for {scan_name}-{channel}: "
              f"HTTP {resp.status}"
            )
          expected = resp.content_length  # may be None
          data = await resp.read()
          if expected is not None and len(data) != expected:
            raise IOError(
              f"Truncated TIFF: got {len(data)} bytes, "
              f"expected {expected} (Content-Length)"
            )
          logger.info(
            "Downloaded %s-%d.tif: %d bytes",
            scan_name, channel, len(data),
          )
          return data
      except _RETRYABLE_EXCEPTIONS as exc:
        last_exc = exc
        if attempt < _HTTP_RETRY_ATTEMPTS - 1:
          logger.warning(
            "TIFF download attempt %d/%d for %s-%d failed (%s) — retrying",
            attempt + 1, _HTTP_RETRY_ATTEMPTS,
            scan_name, channel, exc,
          )
          await asyncio.sleep(_HTTP_RETRY_DELAY)
          continue
    raise OdysseyImageError(
      f"TIFF download for {scan_name}-{channel} failed after "
      f"{_HTTP_RETRY_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc

  async def get_jpeg_preview(
    self,
    group: str,
    scan_name: str,
    contrast_700: int = 5,
    contrast_800: int = 5,
    channels: str = "700 800",
    background: str = "black",
  ) -> bytes:
    """Fetch a JPEG preview with display settings applied server-side."""
    session = self._check_session()
    xml = _jpeg_xml(
      group, scan_name,
      contrast_700=contrast_700,
      contrast_800=contrast_800,
      channels=channels,
      background=background,
    )
    url = f"{self._base_url}/scan/image"
    async with session.get(url, params={"xml": xml}) as resp:
      if resp.status != 200:
        raise OdysseyImageError(
          f"JPEG preview failed: HTTP {resp.status}"
        )
      return await resp.read()

  async def download_scan_log(self, group: str, scan_name: str) -> str:
    """Download the scan log for a completed scan."""
    session = self._check_session()
    url = f"{self._base_url}{_IMAGE_BASE}/savelog.pl"
    params = {"group": group, "scan": scan_name}
    async with session.get(url, params=params) as resp:
      return await resp.text()

  async def list_scan_groups(self) -> str:
    """Fetch the scan setup page HTML.

    Contains a <select name="avail"> dropdown that lists the available
    scan groups; parse with :meth:`parse_select_options`.
    """
    session = self._check_session()
    url = f"{self._base_url}{_SCAN_BASE}/"
    async with session.get(url) as resp:
      return await resp.text()

  # -- Utilities -----------------------------------------------------------

  async def shutdown_instrument(self) -> str:
    """Send shutdown command. Requires Administrator access.

    WARNING: this powers off the instrument. Restart can take up to
    30 minutes.
    """
    session = self._check_session()
    url = f"{self._base_url}/scanapp/admin/admin/index"
    params = {"action": "InitiateShutdown"}
    logger.warning("Shutting down Odyssey instrument")
    async with session.get(url, params=params) as resp:
      return await resp.text()

  async def get_instrument_info(self) -> str:
    """Fetch instrument info page (serial, software version, etc.)."""
    session = self._check_session()
    url = f"{self._base_url}/scanapp/help/instinfo.pl"
    async with session.get(url) as resp:
      return await resp.text()

  # -- Raw request (for discovery / debugging) -----------------------------

  async def get(self, path: str, **kwargs: Any) -> str:
    """Raw GET request to any path on the instrument."""
    session = self._check_session()
    url = f"{self._base_url}{path}"
    async with session.get(url, **kwargs) as resp:
      return await resp.text()

  async def post(self, path: str, data: dict, **kwargs: Any) -> str:
    """Raw POST request to any path on the instrument."""
    session = self._check_session()
    url = f"{self._base_url}{path}"
    async with session.post(url, data=data, **kwargs) as resp:
      return await resp.text()

  # -- HTML parsers --------------------------------------------------------

  @staticmethod
  def _parse_status_html(html: str) -> dict[str, str]:
    """Parse the instrument status page HTML.

    Robust to tag layout: finds the label anywhere in the HTML
    (case-insensitive), then walks forward skipping any tags and
    whitespace until it hits the first non-empty text run.
    """
    def _extract(label: str) -> str:
      idx = html.lower().find(label.lower())
      if idx < 0:
        return ""
      rest = html[idx + len(label):]
      rest = re.sub(r"^(?:\s|<[^>]+>)*:?", "", rest, count=1)
      text = re.sub(r"<[^>]+>", "|", rest)
      for fragment in text.split("|"):
        trimmed = fragment.strip()
        if trimmed:
          return trimmed.split("\n")[0].strip()
      return ""

    return {
      "state": _extract("Scanner Status") or "Unknown",
      "current_user": _extract("Current User"),
      "progress": _extract("Percent Complete"),
      "time_remaining": _extract("Time Remaining"),
      "lid_status": _extract("Lid Status"),
    }

  @staticmethod
  def _parse_info_html(html: str) -> dict[str, str]:
    """Parse the imaging info panel HTML."""
    def _extract(label: str) -> str:
      pattern = rf"{label}\s*[:]\s*(?:<[^>]+>)*\s*([^<\n]+)"
      match = re.search(pattern, html, re.IGNORECASE)
      return match.group(1).strip() if match else ""

    return {
      "dimensions": _extract("Dimensions"),
      "file_size": _extract("File Size"),
      "time_left": _extract("Time Left"),
    }

  @staticmethod
  def parse_select_options(html: str, select_name: str) -> list[str]:
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

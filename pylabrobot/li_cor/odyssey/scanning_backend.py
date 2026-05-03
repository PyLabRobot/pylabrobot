"""Odyssey scanning backend — protocol logic for the Odyssey CGI.

Owns the scan-control protocol: form-encoded POST to configure.pl,
the 7-second initialization countdown, and command.pl GETs for
start / stop / pause / cancel. Driver only ships the bytes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.scanning.scanning import ScanningBackend
from pylabrobot.serializer import SerializableMixin

from .driver import OdysseyDriver
from .errors import OdysseyScanError

logger = logging.getLogger(__name__)


# CGI base + protocol invariants — contracts with Odyssey firmware 2.1.12.
_SCAN_BASE = "/scanapp/scan/nonjava"
_CONFIGURE_URL_PATH = f"{_SCAN_BASE}/configure.pl"
_COMMAND_URL_PATH = f"{_SCAN_BASE}/command.pl"
_INITIALIZING_URL_PATH = f"{_SCAN_BASE}/initializing.pl"
_TIME_URL_PATH = f"{_SCAN_BASE}/time.pl"
_INFO_URL_PATH = "/scanapp/imaging/nonjava/info.pl"

# Hidden ``channel`` field must be the literal "x" — the form has
# VAUE="x" (typo for VALUE), and the firmware rejects any other value.
_CHANNEL_SENTINEL = "x"

# Full 7→1 initialization countdown after configure.pl. Skipping
# steps skips DSP / laser / motor preparation and produces invalid scans.
_INIT_POLL_STEPS = 7

# Default operational scan group on the Odyssey.
DEFAULT_GROUP = "odyssey"

# Bound on the post-Stop "settle to Idle" wait.
_STOP_IDLE_TIMEOUT_SEC = 15.0
_STOP_IDLE_POLL_SEC = 1.0


@dataclass
class OdysseyScanningParams(BackendParams):
  """Scan parameters for the Odyssey Classic.

  Field names align with the configure.pl form, captured from the
  browser interface. Pass an instance to :meth:`Scanning.configure`.

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
    """Render as the form-data dict POSTed to configure.pl."""
    data: dict[str, str] = {
      "channel": _CHANNEL_SENTINEL,
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


@dataclass
class StopResult:
  """Outcome of a graceful Stop.

  ``state`` is "Stopped"; ``partial`` is True if the instrument wrote
  any channel TIFFs before the interrupt; ``channels_available`` lists
  which channels were written.
  """

  state: str
  partial: bool
  channels_available: list[int]


def _parse_error_block(body: str) -> Optional[str]:
  """Extract <Error shorterror="X">message</Error> if present."""
  m = re.search(
    r'<Error\s+shorterror="([^"]+)"\s*>\s*(.*?)\s*</Error>',
    body, re.IGNORECASE | re.DOTALL,
  )
  if m:
    short = m.group(1).strip()
    detail = re.sub(r"\s+", " ", m.group(2)).strip()
    return f"{short}: {detail}"
  return None


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


class OdysseyScanningBackend(ScanningBackend):
  """Concrete scanning backend for the LI-COR Odyssey Classic.

  Encodes the scan-control protocol over the driver's generic HTTP
  transport: form-encoded POST to configure.pl with structured-error
  parsing, the 7→1 initialization countdown, and command.pl GETs for
  the four control verbs.
  """

  def __init__(self, driver: OdysseyDriver) -> None:
    super().__init__()
    self._driver = driver
    self._current_scan: str = ""
    self._current_group: str = ""

  def _coerce_params(
    self, backend_params: Optional[SerializableMixin]
  ) -> OdysseyScanningParams:
    """Accept None / OdysseyScanningParams; reject anything else."""
    if backend_params is None:
      return OdysseyScanningParams()
    if isinstance(backend_params, OdysseyScanningParams):
      return backend_params
    raise TypeError(
      f"OdysseyScanningBackend.configure expects OdysseyScanningParams, "
      f"got {type(backend_params).__name__}"
    )

  async def configure(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    params = self._coerce_params(backend_params)

    # If a previous session left the scanner Paused, release it first.
    # We probe via the InstrumentStatus capability path — but the
    # backend doesn't have a reference to the status backend, so
    # we just check for the structured error in the configure
    # response. (Status preflight is a backend's right to do, but
    # complicates the wiring; defer to the device-level helper.)
    await self._post_configure(params)
    self._current_scan = params.name
    self._current_group = params.group

    await self._wait_initialization(params.name, params.group)

  async def _post_configure(self, params: OdysseyScanningParams) -> None:
    """POST configure.pl, follow redirect on 302, parse Error on 2xx body."""
    form_data = params.to_form_data()
    logger.info(
      "Configuring scan: name=%s group=%s res=%s quality=%s",
      params.name, params.group, params.resolution, params.quality,
    )
    logger.debug("Form data: %s", form_data)

    status, body, headers = await self._driver.post(
      _CONFIGURE_URL_PATH,
      form_data=form_data,
      allow_redirects=False,
      with_retry=True,
    )
    logger.info("POST configure.pl → HTTP %d, body length %d", status, len(body))

    if status == 302:
      redirect = headers.get("Location", "")
      logger.info("configure.pl redirect → %s", redirect)
      # Follow the redirect — this triggers hardware initialization.
      if redirect:
        await self._driver.get(redirect, allow_redirects=False)
      return

    if status >= 400:
      raise OdysseyScanError(
        f"Scanner rejected configuration: HTTP {status}."
      )

    # 2xx body — instrument may have embedded a structured error.
    if "busy" in body.lower() or "<TITLE>Error</TITLE>" in body:
      parsed = _parse_error_block(body)
      if parsed:
        raise OdysseyScanError(f"Scanner rejected configuration — {parsed}")
      raise OdysseyScanError(
        f"Scanner rejected configuration: {body[:500]}"
      )

  async def _wait_initialization(
    self,
    scan_name: str,
    group: str,
    timeout_steps: int = _INIT_POLL_STEPS,
  ) -> None:
    """Poll initializing.pl through the 7→1 countdown."""
    logger.info("Waiting %d s for hardware initialization...", timeout_steps)
    for t in range(timeout_steps, 0, -1):
      params = {"scan": scan_name, "scangroup": group, "timeout": str(t)}
      logger.info("initializing.pl?timeout=%d", t)
      status, _, headers = await self._driver.get(
        _INITIALIZING_URL_PATH, params=params, allow_redirects=False,
      )
      if status == 302 and t <= 1:
        redirect = headers.get("Location", "")
        logger.info("Initialization complete → %s", redirect)
        if redirect:
          await self._driver.get(redirect, allow_redirects=True)
        return
      await asyncio.sleep(1)
    logger.info("Initialization wait complete")

  async def start(self) -> None:
    await self._scan_command("start")

  async def stop(self) -> None:
    """Graceful stop — finish current line, save partial output."""
    await self._scan_command("stop")

  async def pause(self) -> None:
    await self._scan_command("pause")

  async def cancel(self) -> None:
    await self._scan_command("cancel")

  async def _scan_command(self, action: str) -> None:
    """Send command.pl?action=<action> and parse the response.

    On success: 302 redirect to console.pl (no body to consume).
    On error (e.g. not configured): 200 with a structured Error block.
    """
    logger.info("Scan command: %s", action)
    status, body, _ = await self._driver.get(
      _COMMAND_URL_PATH,
      params={"action": action},
      allow_redirects=False,
    )
    logger.info("command.pl?action=%s → HTTP %d", action, status)
    if status == 302:
      return
    if "<Error" in body or "not configured" in body.lower():
      parsed = _parse_error_block(body)
      raise OdysseyScanError(
        f"Scan command '{action}' failed: {parsed or body[:500]}"
      )

  async def _on_stop(self) -> None:
    """Safety on lifecycle stop: cancel any in-flight scan."""
    try:
      await self.cancel()
    except Exception:
      logger.exception("Failed to cancel scan during _on_stop")

  # -- Vendor extensions ---------------------------------------------------

  async def estimate_time(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> str:
    """Return the instrument's time estimate for the configured scan.

    Returns the time string, e.g. "0 hours 2 minutes 15 seconds".
    """
    params = self._coerce_params(backend_params)
    _, html, _ = await self._driver.get(
      _TIME_URL_PATH, params=params.to_time_params(),
    )
    match = re.search(
      r"Estimated Scan Time.*?(\d+ hours? \d+ minutes? \d+ seconds?)",
      html, re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else html

  async def get_progress(self) -> dict[str, str]:
    """Return current scan progress (dimensions, file_size, time_left)."""
    if not self._current_scan:
      return {"dimensions": "", "file_size": "", "time_left": ""}
    _, html, _ = await self._driver.get(
      _INFO_URL_PATH,
      params={
        "scan": self._current_scan,
        "group": self._current_group,
        "update": "Off",
        "console": "yes",
      },
    )
    return _parse_info_html(html)

  async def stop_and_save(
    self,
    image_retrieval_backend,
    instrument_status_backend,
  ) -> StopResult:
    """Graceful Stop that saves whatever has been acquired.

    1. Issue the Stop command.
    2. Poll status until the instrument reports Idle (bounded by
       _STOP_IDLE_TIMEOUT_SEC) — what makes "auto-return to idle" real.
    3. Probe which channel TIFFs were written.

    Cross-capability — needs the image_retrieval and instrument_status
    backends as collaborators (the scanning backend doesn't own those
    protocols). Pass them in from the device or call site.

    Raises :class:`OdysseyScanError` if the instrument does not
    settle at Idle within the timeout.
    """
    if not self._current_scan or not self._current_group:
      await self.stop()
      return StopResult(state="Stopped", partial=False, channels_available=[])

    await self.stop()

    deadline = asyncio.get_event_loop().time() + _STOP_IDLE_TIMEOUT_SEC
    while True:
      reading = await instrument_status_backend.read_status()
      state = reading.state.strip().lower()
      if state in ("idle", "stopped"):
        break
      if asyncio.get_event_loop().time() > deadline:
        raise OdysseyScanError(
          f"Instrument did not return to Idle within "
          f"{_STOP_IDLE_TIMEOUT_SEC:.0f} s after Stop "
          f"(last state={reading.state!r})"
        )
      await asyncio.sleep(_STOP_IDLE_POLL_SEC)

    channels_available: list[int] = []
    for ch in (700, 800):
      try:
        data = await image_retrieval_backend.download_channel(
          self._current_group, self._current_scan, ch,
        )
        if data:
          channels_available.append(ch)
      except Exception as e:
        logger.info("Channel %d not available after Stop: %s", ch, e)

    return StopResult(
      state="Stopped",
      partial=bool(channels_available),
      channels_available=channels_available,
    )

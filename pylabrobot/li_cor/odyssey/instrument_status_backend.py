"""Odyssey instrument status backend — protocol logic for /scanapp/util/status/.

Owns the status protocol: GET the status page, parse the HTML,
normalise the raw state string to a canonical literal so downstream
consumers can rely on exact string equality.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pylabrobot.capabilities.scanning.instrument_status import (
  InstrumentStatusBackend,
  InstrumentStatusReading,
)

from .driver import OdysseyDriver

logger = logging.getLogger(__name__)


_STATUS_BASE = "/scanapp/util/status"
_STATUS_URL_PATH = f"{_STATUS_BASE}/"
_STOP_FROM_STATUS_PATH = f"{_STATUS_BASE}/status"


# Canonical Odyssey state literal exposed in :class:`InstrumentStatusReading`.
OdysseyState = Literal[
  "Idle", "Configured", "Initializing", "Scanning",
  "Paused", "Stopped", "Completed", "Failed",
]

# Map raw instrument strings (lowercase) to the canonical literal.
_STATE_MAP: dict[str, OdysseyState] = {
  "idle": "Idle",
  "configured": "Configured",
  "initializing": "Initializing",
  "scanning": "Scanning",
  "escanning": "Scanning",  # firmware quirk
  "paused": "Paused",
  "stopped": "Stopped",
  "completed": "Completed",
  "failed": "Failed",
  "error": "Failed",
}


def normalize_state(raw: str) -> OdysseyState:
  """Map a raw instrument state string to the canonical literal.

  Unrecognized values fall back to ``"Idle"`` (rather than ``"Failed"``)
  so a parser miss does not lock the UI into a phantom error state.
  Callers needing fresh-vs-stale terminal-state semantics should
  combine this with a transition-out guard.
  """
  key = (raw or "").strip().lower()
  if key in _STATE_MAP:
    return _STATE_MAP[key]
  logger.warning(
    "Unknown instrument state %r — mapping to 'Idle' (parser miss?)", raw
  )
  return "Idle"


def _parse_status_html(html: str) -> dict[str, str]:
  """Parse the instrument status page HTML.

  Robust to tag layout: finds the label anywhere in the HTML
  (case-insensitive), then walks forward skipping any tags and
  whitespace until it hits the first non-empty text run. Handles
  plain text, tags between label and colon, and table layouts.
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


class OdysseyInstrumentStatusBackend(InstrumentStatusBackend):
  """Concrete status backend for the LI-COR Odyssey Classic."""

  def __init__(self, driver: OdysseyDriver) -> None:
    super().__init__()
    self._driver = driver
    # Diagnostic cache — last raw HTML, populated on read.
    self._last_status_html: str = ""
    self._last_status_http: int = 0

  async def read_status(self) -> InstrumentStatusReading:
    status, html, _ = await self._driver.get(_STATUS_URL_PATH)
    self._last_status_html = html
    self._last_status_http = status
    parsed = _parse_status_html(html)
    if parsed["state"] == "Unknown":
      logger.warning(
        "Status parser missed 'Scanner Status' (HTTP %s, %d bytes).",
        status, len(html),
      )
    state = normalize_state(parsed["state"])
    try:
      progress = float(parsed.get("progress") or 0.0)
    except (ValueError, TypeError):
      progress = 0.0
    lid_status = parsed.get("lid_status", "closed")
    return InstrumentStatusReading(
      state=state,
      current_user=parsed.get("current_user", ""),
      progress=progress,
      time_remaining=parsed.get("time_remaining", ""),
      lid_open=lid_status.lower() != "closed",
    )

  # -- Vendor extensions ---------------------------------------------------

  async def force_stop(self) -> None:
    """Stop the scanner via the status page.

    The path to release a paused / stuck scanner without going through
    the scan console.
    """
    logger.warning("Force-stopping scanner from status page")
    await self._driver.post(
      _STOP_FROM_STATUS_PATH,
      form_data={"formContext": "1", "action": "Stop"},
    )

  @property
  def last_status_html(self) -> str:
    """Last raw status-page HTML — for diagnostic endpoints."""
    return self._last_status_html

  @property
  def last_status_http(self) -> int:
    """Last status-page HTTP status code — for diagnostic endpoints."""
    return self._last_status_http

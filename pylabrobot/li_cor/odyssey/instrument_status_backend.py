"""Odyssey instrument status backend.

Polls /scanapp/util/status/ and parses the HTML, normalising the raw
state string onto a canonical ``OdysseyState`` literal so downstream
consumers can rely on exact string equality.
"""

from __future__ import annotations

import logging
from typing import Literal

from pylabrobot.capabilities.scanning.instrument_status import (
  InstrumentStatusBackend,
  InstrumentStatusReading,
)

from .driver import OdysseyDriver

logger = logging.getLogger(__name__)


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
  "escanning": "Scanning",  # Odyssey firmware quirk
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


class OdysseyInstrumentStatusBackend(InstrumentStatusBackend):
  """Concrete status backend for the LI-COR Odyssey Classic."""

  def __init__(self, driver: OdysseyDriver) -> None:
    super().__init__()
    self._driver = driver

  async def read_status(self) -> InstrumentStatusReading:
    raw = await self._driver.get_status()
    state = normalize_state(raw["state"])
    try:
      progress = float(raw.get("progress") or 0.0)
    except (ValueError, TypeError):
      progress = 0.0
    lid_status = raw.get("lid_status", "closed")
    return InstrumentStatusReading(
      state=state,
      current_user=raw.get("current_user", ""),
      progress=progress,
      time_remaining=raw.get("time_remaining", ""),
      lid_open=lid_status.lower() != "closed",
    )

  # -- Vendor extensions ---------------------------------------------------

  async def force_stop(self) -> None:
    """Stop the scanner via the status page."""
    logger.warning("Force-stopping scanner from status page")
    await self._driver.stop_from_status()

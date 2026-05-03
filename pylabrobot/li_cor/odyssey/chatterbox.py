"""Chatterbox path for the Odyssey Classic — no instrument required.

Three backend-tier chatterbox classes (one per capability) share an
:class:`_OdysseyChatterboxState` object that simulates the
instrument's state machine and stored scans. A minimal
:class:`OdysseyChatterboxDriver` overrides ``setup`` / ``stop`` to
no-ops; it exists only because :class:`pylabrobot.device.Device`
requires a :class:`Driver` instance — the chatterbox backends do
not call it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.scanning.image_retrieval import ImageRetrievalBackend
from pylabrobot.capabilities.scanning.instrument_status import (
  InstrumentStatusBackend,
  InstrumentStatusReading,
)
from pylabrobot.capabilities.scanning.scanning import ScanningBackend
from pylabrobot.serializer import SerializableMixin

from .driver import OdysseyDriver
from .instrument_status_backend import OdysseyState
from .scanning_backend import DEFAULT_GROUP, OdysseyScanningParams

logger = logging.getLogger(__name__)


class _OdysseyChatterboxState:
  """Shared mutable state for the three chatterbox backends."""

  def __init__(self) -> None:
    self.scanner_state: OdysseyState = "Idle"
    self.progress: float = 0.0
    self.lid_open: bool = False
    self.current_user: str = ""
    self.current_scan_name: str = ""
    self.current_group: str = ""
    self.configured: bool = False
    self.stop_was_partial: bool = False
    # Pre-seed the default working group so the chatterbox mirrors
    # the lab instrument's name space.
    self.scans: dict[str, dict[str, bytes]] = {
      DEFAULT_GROUP: {
        "test_scan": b"CHATTERBOX_TIFF_DATA_700nm",
      },
    }


class OdysseyChatterboxDriver(OdysseyDriver):
  """No-op driver for chatterbox runs.

  Bypasses the OdysseyDriver constructor's credential check and
  overrides ``setup`` / ``stop`` so a Device can be wired with this
  driver + chatterbox backends without contacting any instrument.
  """

  def __init__(self) -> None:
    # Skip OdysseyDriver.__init__ — it requires real credentials.
    # Call Driver.__init__ directly for instance-set registration.
    OdysseyDriver.__bases__[0].__init__(self)
    self._host = "chatterbox"
    self._port = 0
    self._timeout_seconds = 0.0
    self._username = ""
    self._password = ""
    self._base_url = ""
    self._auth = None
    self._timeout = None
    self._session = None

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    return None

  async def stop(self) -> None:
    return None

  def serialize(self) -> dict:
    return {"type": self.__class__.__name__}


class OdysseyScanningChatterboxBackend(ScanningBackend):
  """Chatterbox scanning backend — drives state with simulated progress."""

  def __init__(self, state: _OdysseyChatterboxState) -> None:
    super().__init__()
    self._state = state

  async def configure(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> None:
    params = (
      backend_params if isinstance(backend_params, OdysseyScanningParams)
      else OdysseyScanningParams()
    )
    self._state.configured = True
    self._state.current_scan_name = params.name
    self._state.current_group = params.group
    self._state.scanner_state = "Configured"
    self._state.stop_was_partial = False
    logger.info("Chatterbox configured scan: %s", params.name)

  async def start(self) -> None:
    if not self._state.configured:
      raise RuntimeError("Chatterbox scan not configured")
    self._state.scanner_state = "Scanning"
    self._state.progress = 0.0
    for i in range(0, 101, 10):
      if self._state.scanner_state != "Scanning":
        return
      self._state.progress = float(i)
      await asyncio.sleep(0.05)
    self._state.scanner_state = "Completed"
    self._state.progress = 100.0
    self._save_scan(partial=False)
    self._state.configured = False

  async def stop(self) -> None:
    """Graceful stop — write a partial TIFF, transition to Stopped."""
    if self._state.scanner_state == "Scanning":
      self._save_scan(partial=True)
      self._state.stop_was_partial = True
    self._state.scanner_state = "Stopped"
    self._state.configured = False

  async def pause(self) -> None:
    self._state.scanner_state = "Paused"

  async def cancel(self) -> None:
    self._state.scanner_state = "Idle"
    self._state.progress = 0.0
    self._state.configured = False
    self._state.stop_was_partial = False

  @property
  def current_scan(self) -> tuple[str, str]:
    """Return ``(group, name)`` for the most recently configured scan."""
    return self._state.current_group, self._state.current_scan_name

  def _save_scan(self, partial: bool) -> None:
    group = self._state.current_group or DEFAULT_GROUP
    name = self._state.current_scan_name or "scan"
    if group not in self._state.scans:
      self._state.scans[group] = {}
    payload = (
      b"CHATTERBOX_PARTIAL_TIFF_DATA" if partial
      else b"CHATTERBOX_TIFF_DATA"
    )
    self._state.scans[group][name] = payload


class OdysseyImageRetrievalChatterboxBackend(ImageRetrievalBackend):
  """Chatterbox image retrieval — reads from the shared state."""

  def __init__(self, state: _OdysseyChatterboxState) -> None:
    super().__init__()
    self._state = state

  async def list_groups(self) -> List[str]:
    return list(self._state.scans.keys())

  async def list_scans(self, group: str) -> List[str]:
    return list(self._state.scans.get(group, {}).keys())

  async def download(self, group: str, scan_name: str) -> bytes:
    scans = self._state.scans.get(group, {})
    if scan_name not in scans:
      raise FileNotFoundError(
        f"Scan '{scan_name}' not found in group '{group}'"
      )
    return scans[scan_name]

  async def download_channel(
    self, group: str, scan_name: str, channel: int
  ) -> bytes:
    """Mirrors the real backend's per-channel download.

    The chatterbox stores one blob per scan rather than per channel,
    so we return that blob for any requested channel — sufficient for
    cross-capability orchestration tests (e.g. ``stop_and_save``).
    """
    return await self.download(group, scan_name)


class OdysseyInstrumentStatusChatterboxBackend(InstrumentStatusBackend):
  """Chatterbox status — reflects the shared state."""

  def __init__(self, state: _OdysseyChatterboxState) -> None:
    super().__init__()
    self._state = state

  async def read_status(self) -> InstrumentStatusReading:
    return InstrumentStatusReading(
      state=self._state.scanner_state,
      current_user=self._state.current_user,
      progress=self._state.progress,
      time_remaining="",
      lid_open=self._state.lid_open,
    )


__all__ = [
  "OdysseyChatterboxDriver",
  "OdysseyScanningChatterboxBackend",
  "OdysseyImageRetrievalChatterboxBackend",
  "OdysseyInstrumentStatusChatterboxBackend",
]

"""Odyssey scanning backend — concrete ScanningBackend over HTTP.

Translates the capability's ``configure / start / stop / pause /
cancel`` verbs into the Odyssey CGI sequence:

  configure.pl → initializing.pl (7 s) → command.pl?action=start
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.scanning.scanning import ScanningBackend
from pylabrobot.serializer import SerializableMixin

from .driver import OdysseyDriver, OdysseyScanningParams
from .errors import OdysseyScanError

logger = logging.getLogger(__name__)


# Max seconds to wait for the instrument to settle at Idle after Stop.
_STOP_IDLE_TIMEOUT_SEC = 15.0
_STOP_IDLE_POLL_SEC = 1.0


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


class OdysseyScanningBackend(ScanningBackend):
  """Concrete scanning backend for the LI-COR Odyssey Classic.

  Sends scan parameters via HTTP POST to configure.pl, polls
  initializing.pl through the 7→1 countdown, then controls the scan
  via command.pl?action=start|stop|pause|cancel.
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
    status = await self._driver.get_status()
    if status["state"].lower() == "paused":
      logger.warning(
        "Scanner is paused from a previous session — releasing first"
      )
      await self._driver.stop_from_status()

    await self._driver.configure_scan(params)
    self._current_scan = params.name
    self._current_group = params.group

    await self._driver.wait_initialization(params.name, params.group)

  async def start(self) -> None:
    await self._driver.start_scan()

  async def stop(self) -> None:
    """Graceful stop — finish current line, save partial output."""
    await self._driver.stop_scan()

  async def pause(self) -> None:
    await self._driver.pause_scan()

  async def cancel(self) -> None:
    await self._driver.cancel_scan()

  async def _on_stop(self) -> None:
    """Safety on lifecycle stop: cancel any in-flight scan."""
    try:
      await self.cancel()
    except Exception:
      logger.exception("Failed to cancel scan during _on_stop")

  # -- Vendor extensions ---------------------------------------------------

  async def stop_and_save(self) -> StopResult:
    """Graceful Stop that saves whatever has been acquired.

    1. Issue command.pl?action=stop.
    2. Poll status until the instrument reports Idle (bounded by
       _STOP_IDLE_TIMEOUT_SEC) — this is what makes the Stop
       "auto-return to idle" semantics real.
    3. Probe which channel TIFFs were written.

    Raises :class:`OdysseyScanError` if the instrument does not
    settle at Idle within the timeout.
    """
    if not self._current_scan or not self._current_group:
      await self._driver.stop_scan()
      return StopResult(
        state="Stopped", partial=False, channels_available=[]
      )

    await self._driver.stop_scan()

    deadline = asyncio.get_event_loop().time() + _STOP_IDLE_TIMEOUT_SEC
    while True:
      status = await self._driver.get_status()
      state = (status.get("state") or "").strip().lower()
      if state in ("idle", "stopped"):
        break
      if asyncio.get_event_loop().time() > deadline:
        raise OdysseyScanError(
          f"Instrument did not return to Idle within "
          f"{_STOP_IDLE_TIMEOUT_SEC:.0f} s after Stop "
          f"(last state={status.get('state')!r})"
        )
      await asyncio.sleep(_STOP_IDLE_POLL_SEC)

    # Probe channel availability by attempting a small TIFF download
    # for each. The instrument writes partial TIFFs at whatever row
    # the scan reached; a failed download means the channel produced
    # no data (e.g. channel disabled or stop too early).
    channels_available: list[int] = []
    for ch in (700, 800):
      try:
        data = await self._driver.download_tiff(
          self._current_group, self._current_scan, ch
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

  async def estimate_time(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> str:
    """Return the instrument's estimate for the configured scan."""
    params = self._coerce_params(backend_params)
    return await self._driver.estimate_scan_time(params)

  async def get_progress(self) -> dict[str, str]:
    """Return current scan progress (dimensions, file_size, time_left)."""
    if self._current_scan:
      return await self._driver.get_scan_progress(
        self._current_scan, self._current_group
      )
    return {"dimensions": "", "file_size": "", "time_left": ""}

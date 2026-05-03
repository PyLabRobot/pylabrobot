"""LI-COR Odyssey Classic device class.

Wires the Odyssey driver and three capability backends. The default
mode is real hardware (HTTP); pass ``chatterbox=True`` for an
in-memory chatterbox path that runs anywhere — useful for CI and
notebooks.

Usage::

  import asyncio
  from pylabrobot.li_cor.odyssey import OdysseyClassic, OdysseyScanningParams

  async def main():
    # Chatterbox — no instrument required.
    odyssey = OdysseyClassic(chatterbox=True)
    async with odyssey:
      await odyssey.scanning.configure(OdysseyScanningParams(name="demo"))
      await odyssey.scanning.start()
      # ... poll status, then download:
      tiff = await odyssey.images.download("odyssey", "demo")
      print(f"{len(tiff)} bytes")

  asyncio.run(main())

Real hardware reads ODYSSEY_USER / ODYSSEY_PASS from the environment::

  odyssey = OdysseyClassic(host="169.254.206.190")  # credentials from env
  async with odyssey:
    ...
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable, Optional

from pylabrobot.capabilities.scanning.image_retrieval import ImageRetrieval
from pylabrobot.capabilities.scanning.instrument_status import (
  InstrumentStatus,
  InstrumentStatusReading,
)
from pylabrobot.capabilities.scanning.scanning import Scanning
from pylabrobot.device import Device
from pylabrobot.device_card import DeviceCard, HasDeviceCard

from .chatterbox import (
  OdysseyChatterboxDriver,
  OdysseyImageRetrievalChatterboxBackend,
  OdysseyInstrumentStatusChatterboxBackend,
  OdysseyScanningChatterboxBackend,
  _OdysseyChatterboxState,
)
from .device_card import ODYSSEY_CLASSIC_BASE
from .driver import OdysseyDriver
from .image_retrieval_backend import OdysseyImageRetrievalBackend
from .instrument_status_backend import (
  OdysseyInstrumentStatusBackend,
  normalize_state,
)
from .errors import OdysseyScanError
from .scanning_backend import (
  DEFAULT_GROUP,
  OdysseyScanningBackend,
  OdysseyScanningParams,
  StopResult,
)

logger = logging.getLogger(__name__)

# Terminal states that end a scan session. ``Idle`` is included
# because the real Odyssey transitions back to Idle when a scan
# finishes — it never reports ``Completed``. The fresh-terminal-state
# guard in :meth:`OdysseyClassic.wait_until_done` makes Idle safe.
_TERMINAL_STATES = frozenset({"Idle", "Completed", "Stopped", "Failed"})

# Bound on the post-Stop "settle to Idle" wait used by :meth:`stop_and_save`.
_STOP_IDLE_TIMEOUT_SEC = 15.0
_STOP_IDLE_POLL_SEC = 1.0


class OdysseyClassic(Device, HasDeviceCard):
  """LI-COR Odyssey Classic (model 9120) infrared imaging system.

  Capabilities:
    scanning: configure and control fluorescence scans.
    images:   download saved scan TIFFs.
    status:   poll the instrument's state machine.

  Pass ``chatterbox=True`` for an in-memory test path; otherwise the
  device connects to the instrument over HTTP using credentials from
  ODYSSEY_USER / ODYSSEY_PASS (or supplied directly).

  ``card`` accepts an instance-level :class:`DeviceCard` (typically
  carrying this unit's PIDInst identity). When provided, it is merged
  on top of :data:`ODYSSEY_CLASSIC_BASE` and exposed as ``self.card``.
  When omitted, ``self.card`` is the model-base card.
  """

  def __init__(
    self,
    host: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    port: int = 80,
    timeout: float = 60.0,
    chatterbox: bool = False,
    card: Optional[DeviceCard] = None,
  ) -> None:
    if chatterbox:
      driver: OdysseyDriver = OdysseyChatterboxDriver()
      state = _OdysseyChatterboxState()
      scanning_backend = OdysseyScanningChatterboxBackend(state)
      image_backend = OdysseyImageRetrievalChatterboxBackend(state)
      status_backend = OdysseyInstrumentStatusChatterboxBackend(state)
    else:
      resolved_host = host or os.environ.get("ODYSSEY_HOST", "")
      if not resolved_host:
        raise ValueError(
          "OdysseyClassic requires a host (or ODYSSEY_HOST env var)."
        )
      resolved_user = username or os.environ.get("ODYSSEY_USER", "")
      resolved_pass = password or os.environ.get("ODYSSEY_PASS", "")
      if not resolved_user or not resolved_pass:
        raise ValueError(
          "OdysseyClassic requires both username and password "
          "(or ODYSSEY_USER / ODYSSEY_PASS env vars)."
        )
      driver = OdysseyDriver(
        host=resolved_host,
        username=resolved_user,
        password=resolved_pass,
        port=port,
        timeout=timeout,
      )
      scanning_backend = OdysseyScanningBackend(driver)
      image_backend = OdysseyImageRetrievalBackend(driver)
      status_backend = OdysseyInstrumentStatusBackend(driver)

    super().__init__(driver=driver)

    self.card = (
      ODYSSEY_CLASSIC_BASE.merge(card) if card is not None
      else ODYSSEY_CLASSIC_BASE
    )

    self.scanning = Scanning(backend=scanning_backend)
    self.images = ImageRetrieval(backend=image_backend)
    self.status = InstrumentStatus(backend=status_backend)
    self._capabilities = [self.scanning, self.images, self.status]

  # -- Convenience helpers -------------------------------------------------

  async def wait_until_done(
    self,
    poll_interval: float = 1.0,
    timeout: Optional[float] = None,
    on_progress: Optional[Callable[[InstrumentStatusReading], None]] = None,
    require_fresh: bool = True,
  ) -> InstrumentStatusReading:
    """Poll status until the instrument reaches a terminal state.

    With ``require_fresh=True`` (default) the method requires the
    instrument to transition out of any initial terminal state before
    accepting the next terminal as "this scan finished". Without this
    guard, a caller racing against a just-completed scan would silently
    receive that previous run's status.

    Set ``require_fresh=False`` when the caller knows the wait was
    triggered by an action that just initiated a new run — :meth:`scan`
    does this since it owns the configure-then-start sequence.

    Returns the final :class:`InstrumentStatusReading`. Raises
    :class:`asyncio.TimeoutError` if ``timeout`` elapses first.
    """
    loop = asyncio.get_event_loop()
    deadline = None if timeout is None else loop.time() + timeout

    initial = await self.status.read_status()
    if on_progress is not None:
      on_progress(initial)
    require_state_change = (
      require_fresh
      and normalize_state(initial.state) in _TERMINAL_STATES
    )

    while True:
      status = await self.status.read_status()
      if on_progress is not None:
        on_progress(status)
      state = normalize_state(status.state)
      if state not in _TERMINAL_STATES:
        require_state_change = False
      elif not require_state_change:
        return status
      if deadline is not None and loop.time() > deadline:
        raise asyncio.TimeoutError(
          f"Scan did not reach a "
          f"{'fresh ' if require_fresh else ''}terminal state within "
          f"{timeout:.0f} s (last state={status.state!r})"
        )
      await asyncio.sleep(poll_interval)

  async def scan(
    self,
    backend_params: Optional[OdysseyScanningParams] = None,
    poll_interval: float = 1.0,
    on_progress: Optional[Callable[[InstrumentStatusReading], None]] = None,
  ) -> InstrumentStatusReading:
    """Configure → Start → wait for completion. Returns the final status.

    One-shot helper for the common notebook flow. Does not download
    the result — call ``odyssey.images.download(group, name)``
    afterwards.
    """
    await self.scanning.configure(backend_params=backend_params)
    await self.scanning.start()
    # scan() owns the configure+start sequence — no risk of latching
    # onto a previous run's terminal state, so opt out of the
    # fresh-terminal guard. Standalone wait_until_done() callers still
    # get protection by default.
    return await self.wait_until_done(
      poll_interval=poll_interval,
      on_progress=on_progress,
      require_fresh=False,
    )

  async def stop_and_save(self) -> StopResult:
    """Graceful Stop that saves whatever has been acquired.

    Cross-capability orchestration:
      1. Issue the scanning Stop command (saves partial output).
      2. Poll status until the instrument reports Idle (bounded by
         ``_STOP_IDLE_TIMEOUT_SEC``) — what makes "auto-return to
         idle" real.
      3. Probe which channel TIFFs were written.

    Lives on the device because it spans three capabilities; no
    single backend should hold references to the other two.

    Raises :class:`OdysseyScanError` if the instrument does not
    settle at Idle within the timeout.
    """
    scanning_backend = self.scanning.backend
    group, name = scanning_backend.current_scan  # type: ignore[attr-defined]
    if not group or not name:
      await self.scanning.stop()
      return StopResult(state="Stopped", partial=False, channels_available=[])

    await self.scanning.stop()

    deadline = asyncio.get_event_loop().time() + _STOP_IDLE_TIMEOUT_SEC
    while True:
      reading = await self.status.read_status()
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

    image_backend = self.images.backend
    channels_available: list[int] = []
    for ch in (700, 800):
      try:
        data = await image_backend.download_channel(group, name, ch)  # type: ignore[attr-defined]
        if data:
          channels_available.append(ch)
      except Exception as e:
        logger.info("Channel %d not available after Stop: %s", ch, e)

    return StopResult(
      state="Stopped",
      partial=bool(channels_available),
      channels_available=channels_available,
    )

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, cast

try:
  import numpy as np

  _HAS_NUMPY = True
except ImportError as e:
  _HAS_NUMPY = False
  _NUMPY_IMPORT_ERROR = e
  np = cast(Any, None)

try:
  from PIL import Image

  _HAS_PIL = True
except ImportError as e:
  _HAS_PIL = False
  _PIL_IMPORT_ERROR = e
  Image = cast(Any, None)

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

FRAME_W = 800
FRAME_H = 480
FRAME_BYTES = FRAME_W * FRAME_H * 4

STATE_MAIN_MENU = "main_menu"
STATE_USER_PROTOCOLS = "user_protocols"
STATE_PROTOCOL_RUN_VIEW = "protocol_run_view"
STATE_PROTOCOL_DETAILS = "protocol_details"
STATE_PROTOCOL_RAN = "protocol_ran"
STATE_PROTOCOL_FINISH = "protocol_finish"
STATE_UNKNOWN = "unknown"

HOME_COORD = (726, 326)
USER_PROTOCOLS_MENU_COORD = (164, 183)
USER_PROTOCOLS_SCROLL_DOUBLE_UP_COORD = (449, 127)
USER_PROTOCOLS_DOUBLE_UP_BBOX = (395, 88, 478, 165)
USER_PROTOCOLS_FIRST_ROW_COORD = (176, 183)
DETAIL_CONFIRM_COORD = (739, 414)
GO_COORD = (739, 414)
SET_COLUMNS_OPEN_COORD = (660, 239)
SET_COLUMNS_CHECK_COORD = (739, 414)
SET_COLUMNS_KEY_COORDS = {
  "7": (85, 261),
  "8": (178, 261),
  "9": (272, 261),
  "4": (85, 314),
  "5": (178, 314),
  "6": (272, 314),
  "1": (85, 367),
  "2": (178, 367),
  "3": (272, 367),
  "0": (178, 420),
  "delete": (272, 420),
}


@dataclass
class FrameCapture:
  """One raw RSI frame plus hashes used for debugging and stability checks."""

  rgba: np.ndarray
  raw_len: int
  frame_sha1: str
  stable_sha1: str


def _decode_rsi_framebuffer(framebuffer: bytes) -> np.ndarray:
  """Convert one Gemini RSI `scap` framebuffer into opaque RGBA pixels."""
  arr = np.frombuffer(framebuffer, dtype=np.uint8).reshape((FRAME_H, FRAME_W, 4))
  rgba = np.empty((FRAME_H, FRAME_W, 4), dtype=np.uint8)
  # Live captures and the original RSI pcap previews decode correctly as BGRX/BGRA.
  # The fourth byte is not a usable PNG alpha channel, so snapshots are saved opaque.
  rgba[:, :, :3] = arr[:, :, [2, 1, 0]]
  rgba[:, :, 3] = 255
  return rgba


@dataclass
class Detection:
  """OCR-derived interpretation of a Gemini screen snapshot."""

  state: str
  confidence: float
  matched: list[str]
  text: str
  text_norm: str


@dataclass
class Snapshot:
  """Saved frame plus the screen-state detection produced from it."""

  frame: FrameCapture
  image_path: str
  detection: Detection


@dataclass(frozen=True)
class ScreenSnapshotResult:
  state: str
  image_path: str

  def as_dict(self) -> dict[str, str]:
    return {
      "state": self.state,
      "image_path": self.image_path,
    }


@dataclass(frozen=True)
class PreparedUserProtocolResult:
  protocol_name: str
  plate_columns: Optional[int]
  run_view: ScreenSnapshotResult
  after_set_plate_columns: Optional[ScreenSnapshotResult]
  prepared_verification: ScreenSnapshotResult

  def as_dict(self) -> dict[str, Any]:
    result = {
      "protocol_name": self.protocol_name,
      "plate_columns": self.plate_columns,
      "run_view": self.run_view.as_dict(),
      "prepared_verification": self.prepared_verification.as_dict(),
    }
    if self.after_set_plate_columns is not None:
      result["after_set_plate_columns"] = self.after_set_plate_columns.as_dict()
    return result


@dataclass(frozen=True)
class StartedPreparedUserProtocolResult:
  protocol_name: str
  verification: ScreenSnapshotResult
  after_start: ScreenSnapshotResult
  completed: ScreenSnapshotResult
  completed_at_utc: str
  home: Optional[ScreenSnapshotResult]

  def as_dict(self) -> dict[str, Any]:
    result = {
      "protocol_name": self.protocol_name,
      "verification": self.verification.as_dict(),
      "after_start": self.after_start.as_dict(),
      "completed": self.completed.as_dict(),
      "completed_at_utc": self.completed_at_utc,
    }
    if self.home is not None:
      result["home"] = self.home.as_dict()
    return result


@dataclass(frozen=True)
class CancelledPreparedUserProtocolResult:
  cancelled: bool
  home_after: bool
  final_state: ScreenSnapshotResult

  def as_dict(self) -> dict[str, Any]:
    return {
      "cancelled": self.cancelled,
      "home_after": self.home_after,
      "final_state": self.final_state.as_dict(),
    }


class _TheGhostTouch:
  """Verified RSI touchscreen control for the BTX Gemini X2.

  This control intentionally supports only the user-protocol path used by the BTX end-to-end
  workflow: Home -> User Protocols -> first sorted protocol -> Run Protocol -> optional plate
  columns -> GO -> wait done.
  """

  def __init__(
    self,
    port: str,
    baud: int = 115200,
    artifact_dir: Optional[str] = None,
    timeout: float = 15.0,
    retries: int = 5,
    min_conf: float = 0.70,
    down_ms: int = 70,
  ) -> None:
    if down_ms < 0:
      raise ValueError("down_ms must be non-negative.")
    self.down_ms = down_ms
    if artifact_dir is None:
      artifact_dir = str(Path(tempfile.gettempdir()) / "pylabrobot-btx-gemini-x2")
    self.artifact_dir = artifact_dir
    self._transport = _RSITransport(port=port, baud=baud, timeout=timeout, retries=retries)
    self._detector = _GeminiScreenDetector(min_conf=min_conf)

  @property
  def port(self) -> str:
    return self._transport.port

  @property
  def min_conf(self) -> float:
    return self._detector.min_conf

  async def setup(self) -> None:
    """Set up the RSI serial session."""
    logger.info("Setting up Gemini X2 touchscreen control on port %s", self.port)
    self._require_dependencies()
    await self._transport.setup()
    logger.info("Gemini X2 touchscreen control ready on port %s", self.port)

  async def stop(self) -> None:
    """Stop the RSI serial session."""
    logger.info("Stopping Gemini X2 touchscreen control on port %s", self.port)
    await self._transport.stop()
    logger.info("Gemini X2 touchscreen control stopped")

  def _require_dependencies(self) -> None:
    if not _HAS_NUMPY:
      raise RuntimeError(
        "numpy is required for Gemini X2 touchscreen handling. Install with: pip install pylabrobot[btx]. "
        f"Import error: {_NUMPY_IMPORT_ERROR}"
      )
    if not _HAS_PIL:
      raise RuntimeError(
        "Pillow is required for Gemini X2 touchscreen handling. Install with: pip install pylabrobot[btx]. "
        f"Import error: {_PIL_IMPORT_ERROR}"
      )
    if shutil.which("tesseract") is None:
      raise RuntimeError(
        "Gemini X2 touchscreen control requires the external `tesseract` command for OCR. "
        "Install the Python dependencies with `pip install pylabrobot[btx]`, then install "
        "Tesseract for your operating system and make the `tesseract` command available on PATH."
      )

  async def prepare_user_protocol(
    self,
    protocol_name: str,
    plate_columns: Optional[int] = None,
  ) -> PreparedUserProtocolResult:
    """Navigate to ``Run Protocol`` and optionally configure HT-200 plate columns."""
    logger.info("Arming Gemini X2 protocol %s (plate_columns=%s)", protocol_name, plate_columns)
    run_view = await self.goto_user_protocol_run_view(protocol_name)
    after_set_plate_columns: ScreenSnapshotResult | None = None
    if plate_columns is not None:
      after_columns = await self.set_plate_columns(plate_columns)
      after_set_plate_columns = self._snapshot_result(after_columns)

    verified = await self.verify_prepared_user_protocol(protocol_name)
    return PreparedUserProtocolResult(
      protocol_name=protocol_name,
      plate_columns=plate_columns,
      run_view=self._snapshot_result(run_view),
      after_set_plate_columns=after_set_plate_columns,
      prepared_verification=self._snapshot_result(verified),
    )

  async def start_prepared_user_protocol(
    self,
    protocol_name: str,
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> StartedPreparedUserProtocolResult:
    """Verify the armed screen, press ``GO``, wait until done, and optionally return home."""
    if max_run_seconds <= 0:
      raise ValueError("max_run_seconds must be greater than zero.")
    logger.info("Starting Gemini X2 electroporation protocol %s", protocol_name)
    verified = await self.verify_prepared_user_protocol(protocol_name)
    start = await self.start_run()
    done = await self.wait_run_done(max_seconds=max_run_seconds)
    completed_at_utc = datetime.now(timezone.utc).isoformat()
    home = None if not home_after else await self.ensure_home()

    return StartedPreparedUserProtocolResult(
      protocol_name=protocol_name,
      verification=self._snapshot_result(verified),
      after_start=self._snapshot_result(start),
      completed=self._snapshot_result(done),
      completed_at_utc=completed_at_utc,
      home=None if home is None else self._snapshot_result(home),
    )

  async def cancel_prepared_user_protocol(self) -> CancelledPreparedUserProtocolResult:
    """Leave the prepared UI state without starting electroporation."""
    logger.info("Cancelling prepared Gemini X2 touchscreen run")
    home = await self.ensure_home()
    return CancelledPreparedUserProtocolResult(
      cancelled=True,
      home_after=True,
      final_state=self._snapshot_result(home),
    )

  async def ensure_home(self) -> Snapshot:
    """Return the Gemini UI to ``Main Menu`` using the fixed Home control."""
    current = await self.snapshot("ensure-home-start")
    if current.detection.state == STATE_MAIN_MENU and current.detection.confidence >= self.min_conf:
      return current
    if current.detection.state == STATE_PROTOCOL_DETAILS:
      current = await self._close_protocol_details(current)
      if (
        current.detection.state == STATE_MAIN_MENU and current.detection.confidence >= self.min_conf
      ):
        return current

    for idx in range(6):
      snap = await self.tap_and_wait(
        HOME_COORD[0],
        HOME_COORD[1],
        expected_states={STATE_MAIN_MENU},
        timeout=6.0,
        interval=0.4,
        prefix=f"ensure-home-{idx}",
      )
      if snap is not None:
        return snap

    raise RuntimeError("Failed to reach Main Menu via Home.")

  async def _close_protocol_details(self, current: Snapshot) -> Snapshot:
    """Close the protocol-details modal before trying fixed-position Home."""
    if current.detection.state != STATE_PROTOCOL_DETAILS:
      return current

    for attempt in range(3):
      closed = await self.tap_and_wait(
        SET_COLUMNS_CHECK_COORD[0],
        SET_COLUMNS_CHECK_COORD[1],
        expected_states={STATE_PROTOCOL_RUN_VIEW, STATE_PROTOCOL_DETAILS},
        timeout=8.0,
        interval=0.45,
        prefix=f"close-protocol-details-{attempt}",
        down_ms=max(self.down_ms, 90),
        initial_delay=0.4,
      )
      if closed is None:
        raise RuntimeError("Lost screen state while closing Protocol Details.")
      current = closed
      if current.detection.state == STATE_PROTOCOL_RUN_VIEW:
        return current

    raise RuntimeError("Failed to close Protocol Details.")

  async def goto_user_protocol_run_view(self, protocol_name: str) -> Snapshot:
    """Open the first sorted user protocol and reach its ``Run Protocol`` screen."""
    current = await self.snapshot("goto-user-run-start")
    if current.detection.state == STATE_PROTOCOL_RUN_VIEW:
      if await self._run_view_matches_protocol(current.image_path, protocol_name) is not False:
        return current

    last_error = "not attempted"
    for attempt in range(3):
      if current.detection.state != STATE_MAIN_MENU:
        current = await self.ensure_home()
      if current.detection.state != STATE_MAIN_MENU:
        raise RuntimeError(f"Expected Main Menu, got {current.detection.state}.")

      try:
        current = await self._open_user_protocols(attempt)
        current = await self._select_first_user_protocol(attempt)
        current = await self._confirm_user_protocol_summary(current, protocol_name, attempt)
        await self._verify_run_view_protocol(current, protocol_name)
      except RuntimeError as exc:
        last_error = str(exc)
        current = await self.ensure_home()
        await asyncio.sleep(1.0)
        continue
      return current

    raise RuntimeError(f"Failed to reach Run Protocol for '{protocol_name}': {last_error}")

  async def set_plate_columns(self, columns: int) -> Snapshot:
    """Open ``Set Plate Columns`` and confirm the requested HT-200 column count."""
    if isinstance(columns, bool) or not 0 <= columns <= 12:
      raise ValueError("plate_columns must be in the range 0..12.")

    current = await self.snapshot("set-cols-start")
    if current.detection.state != STATE_PROTOCOL_RUN_VIEW:
      raise RuntimeError(f"Expected Run Protocol view, got {current.detection.state}.")

    opened = await self.tap_and_wait(
      SET_COLUMNS_OPEN_COORD[0],
      SET_COLUMNS_OPEN_COORD[1],
      expected_states={STATE_PROTOCOL_DETAILS},
      timeout=8.0,
      interval=0.45,
      prefix="set-cols-open",
      down_ms=max(self.down_ms, 80),
    )
    if opened is None:
      raise RuntimeError("Failed to open Set Plate Columns.")

    await self._enter_set_columns_value(columns)
    closed = await self.tap_and_wait(
      SET_COLUMNS_CHECK_COORD[0],
      SET_COLUMNS_CHECK_COORD[1],
      expected_states={STATE_PROTOCOL_RUN_VIEW, STATE_PROTOCOL_DETAILS},
      timeout=8.0,
      interval=0.45,
      prefix="set-cols-check",
      down_ms=max(self.down_ms, 90),
    )
    if closed is not None and closed.detection.state == STATE_PROTOCOL_RUN_VIEW:
      return closed
    if closed is None or closed.detection.state != STATE_PROTOCOL_DETAILS:
      raise RuntimeError("Unexpected state after first Set Plate Columns confirm.")

    confirmed = await self.tap_and_wait(
      SET_COLUMNS_CHECK_COORD[0],
      SET_COLUMNS_CHECK_COORD[1],
      expected_states={STATE_PROTOCOL_RUN_VIEW, STATE_PROTOCOL_DETAILS},
      timeout=8.0,
      interval=0.45,
      prefix="set-cols-check-confirm",
      down_ms=max(self.down_ms, 90),
    )
    if confirmed is not None and confirmed.detection.state == STATE_PROTOCOL_RUN_VIEW:
      return confirmed
    raise RuntimeError("Second Set Plate Columns confirm did not return to Run Protocol.")

  async def verify_prepared_user_protocol(self, protocol_name: str) -> Snapshot:
    """Confirm that the current screen is the expected pre-run view for ``protocol_name``."""
    last_reason = "unknown"
    for attempt in range(3):
      snap = await self.snapshot(f"verify-prepared-{attempt}")
      if snap.detection.state != STATE_PROTOCOL_RUN_VIEW:
        last_reason = f"Expected Run Protocol view, got {snap.detection.state}."
        await asyncio.sleep(0.35)
        continue

      protocol_match = await self._run_view_matches_protocol(snap.image_path, protocol_name)
      if protocol_match is False:
        header = (await asyncio.to_thread(self._detector.run_header_text, snap.image_path)).strip()
        raise RuntimeError(
          f"Prepared run screen does not match protocol '{protocol_name}'. header='{header}'"
        )
      if protocol_match is None:
        last_reason = "Could not verify the protocol header on the prepared run screen."
        await asyncio.sleep(0.35)
        continue

      if not self._detector.looks_prerun(snap.detection):
        last_reason = "Run screen is not in the pre-run state."
        await asyncio.sleep(0.35)
        continue

      return snap

    raise RuntimeError(f"Prepared run verification failed for '{protocol_name}': {last_reason}")

  async def start_run(self) -> Snapshot:
    """Press ``GO`` from the prepared run screen and wait for visible run start feedback."""
    before = await self.snapshot("run-start-before-go")
    if not self._detector.looks_prerun(before.detection):
      raise RuntimeError(
        f"Expected a verified pre-run Run Protocol view before GO, got {before.detection.state}."
      )

    await self.tap(GO_COORD[0], GO_COORD[1], down_ms=90)
    after = await self._wait_for_run_transition(
      timeout=8.0,
      interval=0.45,
      prefix="run-start-after-go",
    )
    if after is None:
      raise RuntimeError("No visible response after GO.")
    if self._detector.is_run_done(after.detection):
      return after

    if self._detector.has_confirm_dialog(after.detection):
      await self.tap(GO_COORD[0], GO_COORD[1], down_ms=90)
      after_confirm = await self._wait_for_run_transition(
        timeout=8.0,
        interval=0.45,
        prefix="run-start-after-confirm",
      )
      if (
        after_confirm is not None
        and not self._detector.has_confirm_dialog(after_confirm.detection)
        and not self._detector.looks_prerun(after_confirm.detection)
      ):
        return after_confirm
      raise RuntimeError("The Gemini did not leave its confirmation/pre-run screen after GO.")

    if self._detector.looks_prerun(after.detection):
      raise RuntimeError(
        "The Gemini remained on the pre-run screen after GO; refusing to tap again."
      )
    return after

  async def _wait_for_run_transition(
    self,
    *,
    timeout: float,
    interval: float,
    prefix: str,
  ) -> Snapshot | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    idx = 0
    while loop.time() < deadline:
      snap = await self.snapshot(f"{prefix}-{idx:02d}")
      detection = snap.detection
      if (
        self._detector.is_run_done(detection)
        or self._detector.has_confirm_dialog(detection)
        or (
          detection.state == STATE_PROTOCOL_RUN_VIEW
          and not self._detector.looks_prerun(detection)
          and detection.confidence >= self.min_conf
        )
      ):
        return snap
      idx += 1
      await asyncio.sleep(interval)
    return None

  async def wait_run_done(self, max_seconds: float) -> Snapshot:
    """Poll the RSI screen until the run has finished."""
    if max_seconds <= 0:
      raise ValueError("max_seconds must be greater than zero.")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_seconds
    idx = 0
    while loop.time() < deadline:
      # Use one frame attempt per poll. Retried requests can accumulate during pulse delivery.
      snap = await self._snapshot_run_poll(f"run-wait-{idx:02d}")
      if self._detector.is_run_done(snap.detection):
        return snap
      idx += 1
      await asyncio.sleep(0.7)
    raise TimeoutError(f"Timed out waiting for run completion after {max_seconds} seconds.")

  async def read_frame(self) -> FrameCapture:
    """Read one full RGB frame from the RSI ``scap`` stream."""
    return await self._transport.read_frame()

  def _save_frame(self, frame: FrameCapture, prefix: str) -> str:
    os.makedirs(self.artifact_dir, exist_ok=True)
    path = os.path.join(
      self.artifact_dir,
      f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.png",
    )
    Image.fromarray(frame.rgba, mode="RGBA").save(path)
    return path

  async def snapshot(self, prefix: str) -> Snapshot:
    """Capture a frame, save it, OCR it, and classify the current screen state."""
    return await self._snapshot_from_frame(prefix=prefix, frame=await self.read_frame())

  async def _snapshot_run_poll(self, prefix: str) -> Snapshot:
    """Capture one run-state frame without retrying the framebuffer request."""
    frame = await self._transport.read_frame(retry=False)
    return await self._snapshot_from_frame(prefix=prefix, frame=frame)

  async def _snapshot_from_frame(self, prefix: str, frame: FrameCapture) -> Snapshot:
    """Save and classify a captured framebuffer."""
    image_path = await asyncio.to_thread(self._save_frame, frame, prefix)
    detection = await asyncio.to_thread(self._detector.classify_image, image_path)
    return Snapshot(frame=frame, image_path=image_path, detection=detection)

  async def tap(self, x: int, y: int, down_ms: Optional[int] = None) -> None:
    """Send one touchscreen tap at the given screen coordinate."""
    hold = self.down_ms if down_ms is None else down_ms
    await self._transport.tap(x, y, hold_ms=hold)

  async def wait_for_states(
    self,
    states: set[str],
    timeout: float,
    interval: float,
    prefix: str,
    initial_delay: float = 0.0,
  ) -> Snapshot | None:
    """Poll screenshots until one of the expected screen states is visible."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    idx = 0
    if initial_delay > 0:
      await asyncio.sleep(initial_delay)
    while loop.time() < deadline:
      snap = await self.snapshot(f"{prefix}-{idx:02d}")
      if snap.detection.state in states and (
        snap.detection.state == STATE_UNKNOWN or snap.detection.confidence >= self.min_conf
      ):
        return snap
      idx += 1
      await asyncio.sleep(interval)
    return None

  async def tap_and_wait(
    self,
    x: int,
    y: int,
    expected_states: set[str],
    timeout: float,
    interval: float,
    prefix: str,
    down_ms: Optional[int] = None,
    initial_delay: float = 1.0,
  ) -> Snapshot | None:
    """Tap a fixed control and wait for one of the expected states."""
    await self.tap(x, y, down_ms=down_ms)
    return await self.wait_for_states(
      expected_states,
      timeout=timeout,
      interval=interval,
      prefix=prefix,
      initial_delay=initial_delay,
    )

  async def _summary_matches_protocol(self, image_path: str, protocol_name: str) -> bool | None:
    return await asyncio.to_thread(
      self._detector.summary_matches_protocol, image_path, protocol_name
    )

  async def _run_view_matches_protocol(self, image_path: str, protocol_name: str) -> bool | None:
    return await asyncio.to_thread(
      self._detector.run_view_matches_protocol, image_path, protocol_name
    )

  async def _scroll_user_protocols_to_top(self, current: Snapshot) -> Snapshot:
    if current.detection.state != STATE_USER_PROTOCOLS:
      raise RuntimeError(f"Expected User Protocols screen, got {current.detection.state}.")
    if await asyncio.to_thread(self._detector.user_protocols_at_top, current):
      return current

    for attempt in range(8):
      next_snapshot = await self.tap_and_wait(
        USER_PROTOCOLS_SCROLL_DOUBLE_UP_COORD[0],
        USER_PROTOCOLS_SCROLL_DOUBLE_UP_COORD[1],
        expected_states={STATE_USER_PROTOCOLS},
        timeout=6.0,
        interval=0.45,
        prefix=f"user-top-{attempt}",
        down_ms=max(self.down_ms, 80),
      )
      if next_snapshot is None:
        raise RuntimeError("Lost User Protocols screen while scrolling to top.")
      current = next_snapshot
      if await asyncio.to_thread(self._detector.user_protocols_at_top, current):
        return current

    raise RuntimeError("Failed to reach the top of User Protocols.")

  async def _open_user_protocols(self, attempt: int) -> Snapshot:
    current = await self.tap_and_wait(
      USER_PROTOCOLS_MENU_COORD[0],
      USER_PROTOCOLS_MENU_COORD[1],
      expected_states={STATE_USER_PROTOCOLS},
      timeout=8.0,
      interval=0.45,
      prefix=f"goto-user-protocols-{attempt}",
      down_ms=max(self.down_ms, 80),
    )
    if current is None:
      raise RuntimeError("Failed to open User Protocols.")
    return await self._scroll_user_protocols_to_top(current)

  async def _select_first_user_protocol(self, attempt: int) -> Snapshot:
    await self.tap(
      USER_PROTOCOLS_FIRST_ROW_COORD[0],
      USER_PROTOCOLS_FIRST_ROW_COORD[1],
      down_ms=max(self.down_ms, 80),
    )
    await asyncio.sleep(1.0)
    current = await self.snapshot(f"goto-user-first-row-selected-{attempt}")
    detector = self._detector
    if current.detection.state == STATE_USER_PROTOCOLS:
      await self.tap(
        DETAIL_CONFIRM_COORD[0],
        DETAIL_CONFIRM_COORD[1],
        down_ms=max(self.down_ms, 80),
      )
      await asyncio.sleep(1.0)
      current = await self.snapshot(f"goto-user-summary-{attempt}-00")
      if (
        current.detection.state != STATE_PROTOCOL_RUN_VIEW
        and not detector.looks_user_protocol_summary(current.detection)
      ):
        await asyncio.sleep(0.45)
        current = await self.snapshot(f"goto-user-summary-{attempt}-01")
    elif (
      current.detection.state != STATE_PROTOCOL_RUN_VIEW
      and not detector.looks_user_protocol_summary(current.detection)
    ):
      await asyncio.sleep(0.45)
      current = await self.snapshot(f"goto-user-summary-{attempt}-01")
    return current

  async def _confirm_user_protocol_summary(
    self,
    current: Snapshot,
    protocol_name: str,
    attempt: int,
  ) -> Snapshot:
    detector = self._detector
    if (
      current.detection.state != STATE_PROTOCOL_RUN_VIEW
      and not detector.looks_user_protocol_summary(current.detection)
    ):
      raise RuntimeError("Failed to open the selected user protocol summary.")

    if current.detection.state == STATE_PROTOCOL_RUN_VIEW:
      return current

    summary_match = await self._summary_matches_protocol(current.image_path, protocol_name)
    if summary_match is False:
      header = detector.summary_header_text(current.image_path).strip()
      raise RuntimeError(
        f"Summary header does not match target protocol '{protocol_name}'. header='{header}'"
      )

    next_snapshot = await self.tap_and_wait(
      DETAIL_CONFIRM_COORD[0],
      DETAIL_CONFIRM_COORD[1],
      expected_states={STATE_PROTOCOL_RUN_VIEW},
      timeout=8.0,
      interval=0.45,
      prefix=f"goto-user-summary-confirm-{attempt}",
      down_ms=max(self.down_ms, 80),
    )
    if next_snapshot is None:
      raise RuntimeError("Failed to reach Run Protocol from the user protocol summary.")
    return next_snapshot

  async def _verify_run_view_protocol(self, current: Snapshot, protocol_name: str) -> None:
    protocol_match = await self._run_view_matches_protocol(current.image_path, protocol_name)
    if protocol_match is False:
      header = (await asyncio.to_thread(self._detector.run_header_text, current.image_path)).strip()
      raise RuntimeError(
        f"Run header does not match target protocol '{protocol_name}'. header='{header}'"
      )

  async def _tap_set_columns_key(self, key: str, pause_s: float = 0.08) -> None:
    if key not in SET_COLUMNS_KEY_COORDS:
      raise RuntimeError(f"Unsupported Set Plate Columns keypad key '{key}'.")
    x, y = SET_COLUMNS_KEY_COORDS[key]
    await self.tap(x, y, down_ms=max(self.down_ms, 70))
    await asyncio.sleep(pause_s)

  async def _enter_set_columns_value(self, columns: int) -> None:
    for _ in range(4):
      await self._tap_set_columns_key("delete")
    for digit in str(columns):
      await self._tap_set_columns_key(digit)
    await asyncio.sleep(0.04)

  def _snapshot_result(self, snap: Snapshot) -> ScreenSnapshotResult:
    return ScreenSnapshotResult(
      state=snap.detection.state,
      image_path=snap.image_path,
    )


class _AsyncSerialLike(Protocol):
  @property
  def port(self) -> str:
    pass

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def write(self, data: bytes) -> None:
    pass

  async def read(self, num_bytes: int = 1) -> bytes:
    pass

  async def reset_input_buffer(self) -> None:
    pass


class _RSITransport:
  """RSI transport built on PLR Serial plus Gemini-specific frame handling."""

  READ_CHUNK_BYTES = 8192

  def __init__(
    self,
    port: str,
    baud: int,
    timeout: float,
    retries: int,
    serial_io: Optional[_AsyncSerialLike] = None,
  ) -> None:
    if timeout <= 0:
      raise ValueError("timeout must be greater than zero.")
    if retries <= 0:
      raise ValueError("retries must be greater than zero.")
    self.timeout = timeout
    self.retries = retries
    self._serial = (
      serial_io
      if serial_io is not None
      else Serial(
        human_readable_device_name="BTX Gemini X2 touchscreen control",
        port=port,
        baudrate=baud,
        timeout=0.05,
      )
    )
    self._is_setup = False

  @property
  def port(self) -> str:
    return self._serial.port

  async def setup(self) -> None:
    if self._is_setup:
      return
    try:
      await self._serial.setup()
    except Exception:
      try:
        await self._serial.stop()
      except Exception:
        logger.debug("Failed to close Gemini RSI serial after setup failure", exc_info=True)
      raise
    self._is_setup = True

  async def stop(self) -> None:
    if not self._is_setup:
      return
    try:
      await self._serial.stop()
    finally:
      self._is_setup = False

  def ensure_open(self) -> _AsyncSerialLike:
    if not self._is_setup:
      raise RuntimeError("Gemini X2 touchscreen serial session is not open.")
    return self._serial

  async def reset_input_buffer(self) -> None:
    await self.ensure_open().reset_input_buffer()

  async def write_line(self, line: str) -> None:
    await self.ensure_open().write(line.encode("ascii") + b"\r")

  async def _read_frame_once(self) -> FrameCapture:
    self.ensure_open()
    await self.reset_input_buffer()
    await self.write_line("echo off")
    await asyncio.sleep(0.03)
    await self.reset_input_buffer()
    await self.write_line("scap")

    buf = bytearray()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + self.timeout
    while loop.time() < deadline:
      chunk = await self.ensure_open().read(self.READ_CHUNK_BYTES)
      if chunk:
        buf.extend(chunk)
      else:
        await asyncio.sleep(0.01)

      if len(buf) < FRAME_BYTES + 1:
        continue

      end = buf.rfind(b":")
      if end >= FRAME_BYTES:
        fb = bytes(buf[end - FRAME_BYTES : end])
        rgba = _decode_rsi_framebuffer(fb)
        stable = rgba[0:160, 0:430, :]
        return FrameCapture(
          rgba=rgba,
          raw_len=len(buf),
          frame_sha1=hashlib.sha1(fb).hexdigest(),
          stable_sha1=hashlib.sha1(stable.tobytes()).hexdigest(),
        )

    raise TimeoutError(f"Failed to read full scap frame, collected {len(buf)} bytes")

  async def read_frame(self, *, retry: bool = True) -> FrameCapture:
    attempts = self.retries if retry else 1
    for attempt in range(attempts):
      try:
        return await self._read_frame_once()
      except Exception:  # pragma: no cover - live hardware path
        if attempt == attempts - 1:
          raise
        await self.reset_input_buffer()
        await asyncio.sleep(0.06)
    raise RuntimeError("Unreachable RSI retry state.")  # pragma: no cover

  async def tap(self, x: int, y: int, hold_ms: int) -> None:
    await self.write_line(f"@key {x} {y}")
    await asyncio.sleep(hold_ms / 1000.0)
    await self.write_line("@key")


class _GeminiScreenDetector:
  """OCR and state classification for Gemini RSI screenshots."""

  def __init__(self, min_conf: float, ocr_timeout: float = 10.0) -> None:
    if not 0 <= min_conf <= 1:
      raise ValueError("min_conf must be in the range 0..1.")
    if ocr_timeout <= 0:
      raise ValueError("ocr_timeout must be greater than zero.")
    self.min_conf = min_conf
    self.ocr_timeout = ocr_timeout

  def ocr_text(self, image_path: str, psm: int) -> str:
    try:
      out = subprocess.check_output(
        ["tesseract", image_path, "stdout", "--psm", str(psm)],
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=self.ocr_timeout,
      )
    except subprocess.TimeoutExpired:
      logger.warning("Tesseract timed out after %.1f seconds for %s", self.ocr_timeout, image_path)
      return ""
    except subprocess.CalledProcessError as exc:
      logger.warning("Tesseract failed for %s with exit code %s", image_path, exc.returncode)
      return ""
    except OSError as exc:
      logger.warning("Could not run Tesseract for %s: %s", image_path, exc)
      return ""
    return "\n".join([ln.strip() for ln in out.splitlines() if ln.strip()])

  def normalize_text(self, text: str) -> str:
    lowered = text.lower()
    lowered = lowered.replace("geminix2", "gemini x2")
    lowered = lowered.replace("protocois", "protocols")
    lowered = lowered.replace("protocals", "protocols")
    lowered = lowered.replace("protocal", "protocol")
    # Tesseract commonly reads the leading `!` in PLR's temporary protocol names as `I`.
    lowered = re.sub(r"(?<![a-z0-9])i?plr(?=[^a-z0-9])", "plr", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()

  def contains_marker(self, text_norm: str, marker: str) -> bool:
    marker_norm = self.normalize_text(marker)
    if not marker_norm:
      return False
    normalized = self.normalize_text(text_norm)
    marker_pattern = r"\s*".join(re.escape(part) for part in marker_norm.split())
    return re.search(rf"(?<![a-z0-9]){marker_pattern}(?![a-z0-9])", normalized) is not None

  def protocol_name_matches(self, header_text: str, protocol_name: str) -> bool | None:
    header_norm = self.normalize_text(header_text)
    target_norm = self.normalize_text(protocol_name)
    if not header_norm or not target_norm:
      return None
    target_parts = target_norm.split()
    target_pattern = r"\s*".join(re.escape(part) for part in target_parts)
    return (
      re.search(
        rf"(?<![a-z0-9]){target_pattern}(?![a-z0-9])",
        header_norm,
      )
      is not None
    )

  def detect_state(self, text: str) -> Detection:
    normalized = self.normalize_text(text)

    if self.contains_marker(normalized, "main menu"):
      return Detection(STATE_MAIN_MENU, 1.0, ["main menu"], text, normalized)

    if self.contains_marker(normalized, "run protocol"):
      if self.contains_marker(normalized, "pulses delivered"):
        finish_markers = []
        for marker in ("press to clear message", "run complete", "finished", "completed"):
          if self.contains_marker(normalized, marker):
            finish_markers.append(marker)
        if finish_markers:
          return Detection(
            STATE_PROTOCOL_FINISH,
            1.0,
            ["run protocol", "pulses delivered", *finish_markers],
            text,
            normalized,
          )
        return Detection(
          STATE_PROTOCOL_RAN, 0.9, ["run protocol", "pulses delivered"], text, normalized
        )

      markers = ["run protocol"]
      for marker in ("set meas", "go", "delivering pulse", "in progress", "current column", "stop"):
        if self.contains_marker(normalized, marker):
          markers.append(marker)
      confidence = min(1.0, 0.70 + 0.06 * (len(markers) - 1))
      return Detection(STATE_PROTOCOL_RUN_VIEW, confidence, markers, text, normalized)

    if (
      self.contains_marker(normalized, "set plate columns")
      or self.contains_marker(normalized, "set the plate handler")
      or self.contains_marker(normalized, "number of columns")
      or self.contains_marker(normalized, "protocol details")
    ):
      return Detection(STATE_PROTOCOL_DETAILS, 1.0, ["protocol details marker"], text, normalized)

    if self.contains_marker(normalized, "user protocols"):
      return Detection(STATE_USER_PROTOCOLS, 1.0, ["user protocols"], text, normalized)

    return Detection(STATE_UNKNOWN, 0.0, [], text, normalized)

  def classify_image(self, image_path: str) -> Detection:
    text = self.ocr_text(image_path, psm=6)
    detection = self.detect_state(text)
    if (
      detection.state == STATE_UNKNOWN
      or detection.confidence < self.min_conf
      or (detection.state == STATE_PROTOCOL_RUN_VIEW and detection.matched == ["run protocol"])
    ):
      sparse = self.ocr_text(image_path, psm=11)
      if sparse:
        merged = "\n".join(part for part in [text, sparse] if part)
        detection = self.detect_state(merged)
    return detection

  def crop_ocr_text(self, image_path: str, bbox: tuple[int, int, int, int], psm: int) -> str:
    temp_path = ""
    try:
      with Image.open(image_path) as img:
        crop = img.crop(bbox)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
          temp_path = tmp.name
        crop.save(temp_path)
      return self.ocr_text(temp_path, psm=psm)
    finally:
      if temp_path and os.path.exists(temp_path):
        os.unlink(temp_path)

  def summary_header_text(self, image_path: str) -> str:
    return self.crop_ocr_text(image_path, (10, 10, 360, 130), psm=11)

  def run_header_text(self, image_path: str) -> str:
    return self.crop_ocr_text(image_path, (10, 80, 350, 170), psm=11)

  def summary_matches_protocol(self, image_path: str, protocol_name: str) -> bool | None:
    return self.protocol_name_matches(self.summary_header_text(image_path), protocol_name)

  def run_view_matches_protocol(self, image_path: str, protocol_name: str) -> bool | None:
    return self.protocol_name_matches(self.run_header_text(image_path), protocol_name)

  def looks_user_protocol_summary(self, detection: Detection) -> bool:
    if self.contains_marker(detection.text_norm, "set protocol"):
      return False
    if self.contains_marker(detection.text_norm, "run protocol"):
      return False
    markers = (
      "square wave",
      "exponential decay",
      "voltage",
      "duration",
      "number of pulses",
      "pulse interval",
      "electrode gap",
      "resistance",
      "capacitance",
    )
    hits = sum(1 for marker in markers if self.contains_marker(detection.text_norm, marker))
    return hits >= 3

  def user_protocols_double_up_active(self, image_path: str) -> bool:
    with Image.open(image_path) as img:
      crop = np.array(img.crop(USER_PROTOCOLS_DOUBLE_UP_BBOX).convert("RGB"))
    active_pixels = ((crop[:, :, 1] >= 180) & (crop[:, :, 2] >= 180)).sum()
    return int(active_pixels) >= 80

  def user_protocols_at_top(self, snap: Snapshot) -> bool:
    # "New Protocol" stays visible even when scrolled, so top-of-list is keyed off the
    # double-up control becoming grey/inactive.
    return not self.user_protocols_double_up_active(snap.image_path)

  def has_confirm_dialog(self, detection: Detection) -> bool:
    return (
      self.contains_marker(detection.text_norm, "are you sure")
      or self.contains_marker(detection.text_norm, "confirm")
      or (
        self.contains_marker(detection.text_norm, "yes")
        and self.contains_marker(detection.text_norm, "no")
      )
    )

  def looks_prerun(self, detection: Detection) -> bool:
    if detection.state != STATE_PROTOCOL_RUN_VIEW:
      return False
    return (
      self.contains_marker(detection.text_norm, "go")
      and not self.contains_marker(detection.text_norm, "delivering pulse")
      and not self.contains_marker(detection.text_norm, "pulses delivered")
    )

  def is_run_done(self, detection: Detection) -> bool:
    return detection.state in {STATE_PROTOCOL_RAN, STATE_PROTOCOL_FINISH} or self.contains_marker(
      detection.text_norm, "pulses delivered"
    )

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, cast, runtime_checkable

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

try:
  import serial

  _HAS_SERIAL = True
except ImportError as e:
  _HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e
  serial = cast(Any, None)

from pylabrobot.io.serial import Serial

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
  home: Optional[ScreenSnapshotResult]

  def as_dict(self) -> dict[str, Any]:
    result = {
      "protocol_name": self.protocol_name,
      "verification": self.verification.as_dict(),
      "after_start": self.after_start.as_dict(),
      "completed": self.completed.as_dict(),
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


class TheGhostTouch:
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
    self.port = port
    self.baud = baud
    self.timeout = timeout
    self.retries = retries
    self.min_conf = min_conf
    self.down_ms = down_ms
    if artifact_dir is None:
      artifact_dir = str(Path(tempfile.gettempdir()) / "pylabrobot-btx-the-ghost-touch")
    self.artifact_dir = artifact_dir
    self._transport = _RSITransport(port=port, baud=baud, timeout=timeout, retries=retries)
    self._detector = _GeminiScreenDetector(min_conf=min_conf)
    self.ser: serial.Serial | None = None

  def __enter__(self) -> "TheGhostTouch":
    """Open the RSI serial session."""
    self._require_dependencies()
    self._get_transport().open()
    self.ser = self._get_transport().ser
    return self

  def __exit__(self, exc_type, exc, tb) -> None:
    """Close the RSI serial session."""
    self._get_transport().close()
    self.ser = None

  def _require_dependencies(self) -> None:
    if not _HAS_SERIAL:
      raise RuntimeError(
        "pyserial is required for TheGhostTouch. Install with: pip install pylabrobot[btx]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    if not _HAS_NUMPY:
      raise RuntimeError(
        "numpy is required for TheGhostTouch frame handling. Install with: pip install pylabrobot[btx]. "
        f"Import error: {_NUMPY_IMPORT_ERROR}"
      )
    if not _HAS_PIL:
      raise RuntimeError(
        "Pillow is required for TheGhostTouch image handling. Install with: pip install pylabrobot[btx]. "
        f"Import error: {_PIL_IMPORT_ERROR}"
      )
    if shutil.which("tesseract") is None:
      raise RuntimeError(
        "TheGhostTouch requires the external `tesseract` command for OCR. "
        "Install the Python dependencies with `pip install pylabrobot[btx]`, then install "
        "Tesseract for your operating system and make the `tesseract` command available on PATH."
      )

  def _get_transport(self) -> _RSITransport:
    transport = getattr(self, "_transport", None)
    if transport is None:
      transport = _RSITransport(
        port=self.port,
        baud=self.baud,
        timeout=self.timeout,
        retries=self.retries,
      )
      self._transport = transport
    return cast(_RSITransport, transport)

  def _get_detector(self) -> _GeminiScreenDetector:
    detector = getattr(self, "_detector", None)
    if detector is None:
      detector = _GeminiScreenDetector(min_conf=self.min_conf)
      self._detector = detector
    return cast(_GeminiScreenDetector, detector)

  def prepare_user_protocol(
    self,
    protocol_name: str,
    plate_columns: Optional[int] = None,
  ) -> PreparedUserProtocolResult:
    """Navigate to ``Run Protocol`` and optionally configure HT-200 plate columns."""
    run_view = self.goto_user_protocol_run_view(protocol_name)
    after_set_plate_columns: ScreenSnapshotResult | None = None
    if plate_columns is not None:
      after_columns = self.set_plate_columns(plate_columns)
      after_set_plate_columns = self._snapshot_result(after_columns)

    verified = self.verify_prepared_user_protocol(protocol_name)
    return PreparedUserProtocolResult(
      protocol_name=protocol_name,
      plate_columns=plate_columns,
      run_view=self._snapshot_result(run_view),
      after_set_plate_columns=after_set_plate_columns,
      prepared_verification=self._snapshot_result(verified),
    )

  def start_prepared_user_protocol(
    self,
    protocol_name: str,
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> StartedPreparedUserProtocolResult:
    """Verify the armed screen, press ``GO``, wait until done, and optionally return home."""
    verified = self.verify_prepared_user_protocol(protocol_name)
    start = self.start_run()
    done = self.wait_run_done(max_seconds=max_run_seconds)
    home = None if not home_after else self.ensure_home()

    return StartedPreparedUserProtocolResult(
      protocol_name=protocol_name,
      verification=self._snapshot_result(verified),
      after_start=self._snapshot_result(start),
      completed=self._snapshot_result(done),
      home=None if home is None else self._snapshot_result(home),
    )

  def cancel_prepared_user_protocol(
    self, home_after: bool = True
  ) -> CancelledPreparedUserProtocolResult:
    """Leave the prepared UI state without starting electroporation."""
    home = self.ensure_home() if home_after else self.snapshot("cancel-prepared-current")
    return CancelledPreparedUserProtocolResult(
      cancelled=True,
      home_after=home_after,
      final_state=self._snapshot_result(home),
    )

  def ensure_home(self) -> Snapshot:
    """Return the Gemini UI to ``Main Menu`` using the fixed Home control."""
    current = self.snapshot("ensure-home-start")
    if current.detection.state == STATE_MAIN_MENU and current.detection.confidence >= self.min_conf:
      return current
    if current.detection.state == STATE_PROTOCOL_DETAILS:
      current = self._close_protocol_details(current)
      if (
        current.detection.state == STATE_MAIN_MENU and current.detection.confidence >= self.min_conf
      ):
        return current

    for idx in range(6):
      snap = self.tap_and_wait(
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

  def _close_protocol_details(self, current: Snapshot) -> Snapshot:
    """Close the protocol-details modal before trying fixed-position Home."""
    if current.detection.state != STATE_PROTOCOL_DETAILS:
      return current

    for attempt in range(3):
      closed = self.tap_and_wait(
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

  def goto_user_protocol_run_view(self, protocol_name: str) -> Snapshot:
    """Open the first sorted user protocol and reach its ``Run Protocol`` screen."""
    current = self.snapshot("goto-user-run-start")
    if current.detection.state == STATE_PROTOCOL_RUN_VIEW:
      if self._run_view_matches_protocol(current.image_path, protocol_name) is not False:
        return current

    last_error = "not attempted"
    for attempt in range(3):
      if current.detection.state != STATE_MAIN_MENU:
        current = self.ensure_home()
      if current.detection.state != STATE_MAIN_MENU:
        raise RuntimeError(f"Expected Main Menu, got {current.detection.state}.")

      try:
        current = self._open_user_protocols(attempt)
        current = self._select_first_user_protocol(attempt)
        current = self._confirm_user_protocol_summary(current, protocol_name, attempt)
        self._verify_run_view_protocol(current, protocol_name)
      except RuntimeError as exc:
        last_error = str(exc)
        current = self.ensure_home()
        time.sleep(1.0)
        continue
      return current

    raise RuntimeError(f"Failed to reach Run Protocol for '{protocol_name}': {last_error}")

  def set_plate_columns(self, columns: int) -> Snapshot:
    """Open ``Set Plate Columns`` and confirm the requested HT-200 column count."""
    if not 0 <= columns <= 12:
      raise RuntimeError("plate_columns must be in the range 0..12.")

    current = self.snapshot("set-cols-start")
    if current.detection.state != STATE_PROTOCOL_RUN_VIEW:
      raise RuntimeError(f"Expected Run Protocol view, got {current.detection.state}.")

    opened = self.tap_and_wait(
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

    self._enter_set_columns_value(columns)
    closed = self.tap_and_wait(
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

    confirmed = self.tap_and_wait(
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

  def verify_prepared_user_protocol(self, protocol_name: str) -> Snapshot:
    """Confirm that the current screen is the expected pre-run view for ``protocol_name``."""
    last_reason = "unknown"
    for attempt in range(3):
      snap = self.snapshot(f"verify-prepared-{attempt}")
      if snap.detection.state != STATE_PROTOCOL_RUN_VIEW:
        last_reason = f"Expected Run Protocol view, got {snap.detection.state}."
        time.sleep(0.35)
        continue

      protocol_match = self._run_view_matches_protocol(snap.image_path, protocol_name)
      if protocol_match is False:
        header = self._get_detector().run_header_text(snap.image_path).strip()
        raise RuntimeError(
          f"Prepared run screen does not match protocol '{protocol_name}'. header='{header}'"
        )
      if protocol_match is None:
        last_reason = "Could not verify the protocol header on the prepared run screen."
        time.sleep(0.35)
        continue

      if not self._get_detector().looks_prerun(snap.detection):
        last_reason = "Run screen is not in the pre-run state."
        time.sleep(0.35)
        continue

      return snap

    raise RuntimeError(f"Prepared run verification failed for '{protocol_name}': {last_reason}")

  def start_run(self) -> Snapshot:
    """Press ``GO`` from the prepared run screen and wait for visible run start feedback."""
    before = self.snapshot("run-start-before-go")
    if before.detection.state != STATE_PROTOCOL_RUN_VIEW:
      raise RuntimeError(f"Expected Run Protocol view before GO, got {before.detection.state}.")

    self.tap(GO_COORD[0], GO_COORD[1], down_ms=90)
    after = self.wait_for_states(
      states={STATE_PROTOCOL_RUN_VIEW, STATE_PROTOCOL_RAN, STATE_PROTOCOL_FINISH, STATE_UNKNOWN},
      timeout=8.0,
      interval=0.45,
      prefix="run-start-after-go",
    )
    if after is None:
      raise RuntimeError("No visible response after GO.")
    if self._get_detector().is_run_done(after.detection):
      return after

    if self._get_detector().has_confirm_dialog(
      after.detection
    ) or self._get_detector().looks_prerun(after.detection):
      self.tap(GO_COORD[0], GO_COORD[1], down_ms=90)
      after_confirm = self.wait_for_states(
        states={STATE_PROTOCOL_RUN_VIEW, STATE_PROTOCOL_RAN, STATE_PROTOCOL_FINISH, STATE_UNKNOWN},
        timeout=8.0,
        interval=0.45,
        prefix="run-start-after-confirm",
      )
      if after_confirm is not None:
        return after_confirm

    return after

  def wait_run_done(self, max_seconds: float) -> Snapshot:
    """Poll the RSI screen until the run has finished."""
    deadline = time.time() + max_seconds
    idx = 0
    while time.time() < deadline:
      snap = self.snapshot(f"run-wait-{idx:02d}")
      if self._get_detector().is_run_done(snap.detection):
        return snap
      idx += 1
      time.sleep(0.7)
    raise TimeoutError(f"Timed out waiting for run completion after {max_seconds} seconds.")

  def read_frame(self) -> FrameCapture:
    """Read one full RGB frame from the RSI ``scap`` stream."""
    return self._get_transport().read_frame()

  def _save_frame(self, frame: FrameCapture, prefix: str) -> str:
    os.makedirs(self.artifact_dir, exist_ok=True)
    path = os.path.join(self.artifact_dir, f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.png")
    Image.fromarray(frame.rgba, mode="RGBA").save(path)
    return path

  def snapshot(self, prefix: str) -> Snapshot:
    """Capture a frame, save it, OCR it, and classify the current screen state."""
    frame = self.read_frame()
    image_path = self._save_frame(frame, prefix)
    detection = self._get_detector().classify_image(image_path)
    return Snapshot(frame=frame, image_path=image_path, detection=detection)

  def tap(self, x: int, y: int, down_ms: Optional[int] = None) -> None:
    """Send one touchscreen tap at the given screen coordinate."""
    hold = self.down_ms if down_ms is None else down_ms
    self._get_transport().tap(x, y, hold_ms=hold)

  def wait_for_states(
    self,
    states: set[str],
    timeout: float,
    interval: float,
    prefix: str,
    initial_delay: float = 0.0,
  ) -> Snapshot | None:
    """Poll screenshots until one of the expected screen states is visible."""
    deadline = time.time() + timeout
    idx = 0
    if initial_delay > 0:
      time.sleep(initial_delay)
    while time.time() < deadline:
      snap = self.snapshot(f"{prefix}-{idx:02d}")
      if snap.detection.state in states and snap.detection.confidence >= self.min_conf:
        return snap
      idx += 1
      time.sleep(interval)
    return None

  def tap_and_wait(
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
    self.tap(x, y, down_ms=down_ms)
    return self.wait_for_states(
      expected_states,
      timeout=timeout,
      interval=interval,
      prefix=prefix,
      initial_delay=initial_delay,
    )

  def _summary_matches_protocol(self, image_path: str, protocol_name: str) -> bool | None:
    return self._get_detector().summary_matches_protocol(image_path, protocol_name)

  def _run_view_matches_protocol(self, image_path: str, protocol_name: str) -> bool | None:
    return self._get_detector().run_view_matches_protocol(image_path, protocol_name)

  def _scroll_user_protocols_to_top(self, current: Snapshot) -> Snapshot:
    if current.detection.state != STATE_USER_PROTOCOLS:
      raise RuntimeError(f"Expected User Protocols screen, got {current.detection.state}.")
    if self._get_detector().user_protocols_at_top(current):
      return current

    for attempt in range(8):
      next_snapshot = self.tap_and_wait(
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
      if self._get_detector().user_protocols_at_top(current):
        return current

    raise RuntimeError("Failed to reach the top of User Protocols.")

  def _open_user_protocols(self, attempt: int) -> Snapshot:
    current = self.tap_and_wait(
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
    return self._scroll_user_protocols_to_top(current)

  def _select_first_user_protocol(self, attempt: int) -> Snapshot:
    self.tap(
      USER_PROTOCOLS_FIRST_ROW_COORD[0],
      USER_PROTOCOLS_FIRST_ROW_COORD[1],
      down_ms=max(self.down_ms, 80),
    )
    time.sleep(1.0)
    current = self.snapshot(f"goto-user-first-row-selected-{attempt}")
    detector = self._get_detector()
    if current.detection.state == STATE_USER_PROTOCOLS:
      self.tap(
        DETAIL_CONFIRM_COORD[0],
        DETAIL_CONFIRM_COORD[1],
        down_ms=max(self.down_ms, 80),
      )
      time.sleep(1.0)
      current = self.snapshot(f"goto-user-summary-{attempt}-00")
      if (
        current.detection.state != STATE_PROTOCOL_RUN_VIEW
        and not detector.looks_user_protocol_summary(current.detection)
      ):
        time.sleep(0.45)
        current = self.snapshot(f"goto-user-summary-{attempt}-01")
    elif (
      current.detection.state != STATE_PROTOCOL_RUN_VIEW
      and not detector.looks_user_protocol_summary(current.detection)
    ):
      time.sleep(0.45)
      current = self.snapshot(f"goto-user-summary-{attempt}-01")
    return current

  def _confirm_user_protocol_summary(
    self,
    current: Snapshot,
    protocol_name: str,
    attempt: int,
  ) -> Snapshot:
    detector = self._get_detector()
    if (
      current.detection.state != STATE_PROTOCOL_RUN_VIEW
      and not detector.looks_user_protocol_summary(current.detection)
    ):
      raise RuntimeError("Failed to open the selected user protocol summary.")

    if current.detection.state == STATE_PROTOCOL_RUN_VIEW:
      return current

    summary_match = self._summary_matches_protocol(current.image_path, protocol_name)
    if summary_match is False:
      header = detector.summary_header_text(current.image_path).strip()
      raise RuntimeError(
        f"Summary header does not match target protocol '{protocol_name}'. header='{header}'"
      )

    next_snapshot = self.tap_and_wait(
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

  def _verify_run_view_protocol(self, current: Snapshot, protocol_name: str) -> None:
    protocol_match = self._run_view_matches_protocol(current.image_path, protocol_name)
    if protocol_match is False:
      header = self._get_detector().run_header_text(current.image_path).strip()
      raise RuntimeError(
        f"Run header does not match target protocol '{protocol_name}'. header='{header}'"
      )

  def _tap_set_columns_key(self, key: str, pause_s: float = 0.08) -> None:
    if key not in SET_COLUMNS_KEY_COORDS:
      raise RuntimeError(f"Unsupported Set Plate Columns keypad key '{key}'.")
    x, y = SET_COLUMNS_KEY_COORDS[key]
    self.tap(x, y, down_ms=max(self.down_ms, 70))
    time.sleep(pause_s)

  def _enter_set_columns_value(self, columns: int) -> None:
    for _ in range(4):
      self._tap_set_columns_key("delete")
    for digit in str(columns):
      self._tap_set_columns_key(digit)
    time.sleep(0.04)

  def _snapshot_result(self, snap: Snapshot) -> ScreenSnapshotResult:
    return ScreenSnapshotResult(
      state=snap.detection.state,
      image_path=snap.image_path,
    )


@runtime_checkable
class _AsyncSerialLike(Protocol):
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
    self.port = port
    self.baud = baud
    self.timeout = timeout
    self.retries = retries
    self._serial = serial_io or Serial(
      human_readable_device_name="BTX Gemini X2 TheGhostTouch",
      port=port,
      baudrate=baud,
      timeout=0.05,
    )
    self._loop: asyncio.AbstractEventLoop | None = None
    self._loop_thread: threading.Thread | None = None
    self.ser: Any | None = None

  def open(self) -> None:
    if self._loop is not None:
      return

    ready = threading.Event()
    transport = self

    def _loop_main() -> None:
      loop = asyncio.new_event_loop()
      asyncio.set_event_loop(loop)
      transport._loop = loop
      ready.set()
      loop.run_forever()
      pending = asyncio.all_tasks(loop)
      for task in pending:
        task.cancel()
      if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
      loop.close()

    self._loop_thread = threading.Thread(
      target=_loop_main,
      name="TheGhostTouch-RSITransport",
      daemon=True,
    )
    self._loop_thread.start()
    ready.wait()
    self._run(self._serial.setup())
    self.ser = self._serial

  def close(self) -> None:
    if self._loop is None:
      self.ser = None
      return

    try:
      self._run(self._serial.stop())
    finally:
      loop = self._loop
      thread = self._loop_thread
      self._loop = None
      self._loop_thread = None
      self.ser = None
      if loop is not None:
        loop.call_soon_threadsafe(loop.stop)
      if thread is not None:
        thread.join(timeout=2.0)

  def _run(self, awaitable: Any) -> Any:
    if self._loop is None:
      raise RuntimeError("TheGhostTouch serial session is not open.")
    future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
    return future.result()

  def ensure_open(self) -> _AsyncSerialLike:
    if self._loop is None:
      raise RuntimeError("TheGhostTouch serial session is not open.")
    return self._serial

  def drain_input(self, seconds: float = 0.12) -> int:
    del seconds
    self._run(self.ensure_open().reset_input_buffer())
    return 0

  def write_line(self, line: str) -> None:
    self._run(self.ensure_open().write(line.encode("ascii") + b"\r"))

  def _read_frame_once(self) -> FrameCapture:
    self.ensure_open()
    self.drain_input(0.12)
    self.write_line("echo off")
    time.sleep(0.03)
    self._run(self._serial.reset_input_buffer())
    self.write_line("scap")

    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < self.timeout:
      chunk = self._run(self._serial.read(self.READ_CHUNK_BYTES))
      if chunk:
        buf.extend(chunk)
      else:
        time.sleep(0.01)

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

  def read_frame(self) -> FrameCapture:
    last_err: Exception | None = None
    for _ in range(self.retries):
      try:
        return self._read_frame_once()
      except Exception as exc:  # pragma: no cover - live hardware path
        last_err = exc
        self.drain_input(0.15)
        time.sleep(0.06)
    assert last_err is not None
    raise last_err

  def tap(self, x: int, y: int, hold_ms: int) -> None:
    self.write_line(f"@key {x} {y}")
    time.sleep(hold_ms / 1000.0)
    self.write_line("@key")


class _GeminiScreenDetector:
  """OCR and state classification for Gemini RSI screenshots."""

  def __init__(self, min_conf: float) -> None:
    self.min_conf = min_conf

  def ocr_text(self, image_path: str, psm: int) -> str:
    try:
      out = subprocess.check_output(
        ["tesseract", image_path, "stdout", "--psm", str(psm)],
        stderr=subprocess.DEVNULL,
        text=True,
      )
    except Exception:
      return ""
    return "\n".join([ln.strip() for ln in out.splitlines() if ln.strip()])

  def normalize_text(self, text: str) -> str:
    lowered = text.lower()
    lowered = lowered.replace("geminix2", "gemini x2")
    lowered = lowered.replace("protocois", "protocols")
    lowered = lowered.replace("protocals", "protocols")
    lowered = lowered.replace("protocal", "protocol")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()

  def contains_marker(self, text_norm: str, marker: str) -> bool:
    marker_norm = self.normalize_text(marker)
    if not marker_norm:
      return False
    if marker_norm in text_norm:
      return True
    return marker_norm.replace(" ", "") in text_norm.replace(" ", "")

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
    if detection.state == STATE_UNKNOWN or detection.confidence < self.min_conf:
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
    header_norm = self.normalize_text(self.summary_header_text(image_path))
    if not header_norm:
      return None
    target_norm = self.normalize_text(protocol_name)
    if not target_norm:
      return None
    return target_norm.replace(" ", "") in header_norm.replace(" ", "")

  def run_view_matches_protocol(self, image_path: str, protocol_name: str) -> bool | None:
    header_norm = self.normalize_text(self.run_header_text(image_path))
    if not header_norm:
      return None
    target_norm = self.normalize_text(protocol_name)
    if not target_norm:
      return None
    return target_norm.replace(" ", "") in header_norm.replace(" ", "")

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
    return any(
      self.contains_marker(detection.text_norm, marker)
      for marker in ("are you sure", "confirm", "yes", "no")
    )

  def looks_prerun(self, detection: Detection) -> bool:
    if detection.state != STATE_PROTOCOL_RUN_VIEW:
      return False
    return (
      self.contains_marker(detection.text_norm, "set meas")
      and self.contains_marker(detection.text_norm, "go")
      and not self.contains_marker(detection.text_norm, "delivering pulse")
      and not self.contains_marker(detection.text_norm, "pulses delivered")
    )

  def is_run_done(self, detection: Detection) -> bool:
    return detection.state in {STATE_PROTOCOL_RAN, STATE_PROTOCOL_FINISH} or self.contains_marker(
      detection.text_norm, "pulses delivered"
    )

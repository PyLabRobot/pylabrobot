import json
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Optional, cast
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("serial")

from pylabrobot.thermo_fisher.btx.gemini.X2.the_ghost_touch import (
  FRAME_BYTES,
  FRAME_H,
  FRAME_W,
  STATE_MAIN_MENU,
  STATE_PROTOCOL_DETAILS,
  STATE_PROTOCOL_FINISH,
  STATE_PROTOCOL_RUN_VIEW,
  STATE_UNKNOWN,
  STATE_USER_PROTOCOLS,
  Detection,
  FrameCapture,
  Snapshot,
  _decode_rsi_framebuffer,
  _GeminiScreenDetector,
  _RSITransport,
  _TheGhostTouch,
)

SCREEN_FIXTURES = Path(__file__).parent / "test_data/gemini_x2/screens"


class _FakeAsyncSerial:
  def __init__(self, reads: Optional[list[bytes]] = None, port: str = "/dev/test"):
    self.port = port
    self.reads: list[bytes] = list(reads or [])
    self.writes: list[bytes] = []
    self.setup = AsyncMock()
    self.stop = AsyncMock()
    self.reset_calls = 0

  async def write(self, data: bytes) -> None:
    self.writes.append(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    if not self.reads:
      return b""
    return self.reads.pop(0)

  async def reset_input_buffer(self) -> None:
    self.reset_calls += 1


class _TestGhostTouch(_TheGhostTouch):
  def __init__(self) -> None:
    super().__init__(port="/dev/test", artifact_dir="/tmp", retries=1)
    self._snapshots: list[Snapshot] = []
    self.taps: list[tuple[int, int, Optional[int]]] = []

  def queue_snapshot(
    self, state: str, text: str = "", text_norm: str = "", image_path: str = "img"
  ) -> None:
    detection = Detection(
      state=state,
      confidence=1.0 if state != STATE_UNKNOWN else 0.0,
      matched=[],
      text=text,
      text_norm=text_norm or text,
    )
    self._snapshots.append(
      Snapshot(frame=cast(FrameCapture, None), image_path=image_path, detection=detection)
    )

  async def snapshot(self, prefix: str) -> Snapshot:
    del prefix
    if not self._snapshots:
      raise AssertionError("No queued snapshots left")
    return self._snapshots.pop(0)

  async def _snapshot_run_poll(self, prefix: str) -> Snapshot:
    return await self.snapshot(prefix)

  async def tap(self, x: int, y: int, down_ms=None) -> None:
    self.taps.append((x, y, down_ms))

  async def tap_and_wait(
    self,
    x: int,
    y: int,
    expected_states,
    timeout,
    interval,
    prefix,
    down_ms=None,
    initial_delay=1.0,
  ):
    del expected_states, timeout, interval, prefix, initial_delay
    self.taps.append((x, y, down_ms))
    return await self.snapshot("tap-and-wait")

  async def _scroll_user_protocols_to_top(self, current: Snapshot) -> Snapshot:
    return current

  async def _summary_matches_protocol(self, image_path: str, protocol_name: str):
    del image_path, protocol_name
    return True

  async def _run_view_matches_protocol(self, image_path: str, protocol_name: str):
    del image_path, protocol_name
    return True


class TestTheGhostTouch(unittest.IsolatedAsyncioTestCase):
  def _fixture_protocol_name(self) -> str:
    metadata = json.loads((SCREEN_FIXTURES / "metadata.json").read_text())
    return str(metadata["temporary_protocol"]["name"])

  def test_require_dependencies_reports_missing_tesseract(self):
    touch = _TheGhostTouch(port="/dev/test")

    with patch(
      "pylabrobot.thermo_fisher.btx.gemini.X2.the_ghost_touch.shutil.which",
      return_value=None,
    ):
      with self.assertRaisesRegex(RuntimeError, "external `tesseract` command"):
        touch._require_dependencies()

  def test_constructor_rejects_invalid_retry_and_confidence_settings(self):
    with self.assertRaisesRegex(ValueError, "retries"):
      _TheGhostTouch(port="/dev/test", retries=0)
    with self.assertRaisesRegex(ValueError, "min_conf"):
      _TheGhostTouch(port="/dev/test", min_conf=1.1)

  def test_ocr_timeout_and_process_failures_return_empty_text(self):
    detector = _GeminiScreenDetector(min_conf=0.70, ocr_timeout=0.01)

    with patch(
      "pylabrobot.thermo_fisher.btx.gemini.X2.the_ghost_touch.subprocess.check_output",
      side_effect=subprocess.TimeoutExpired("tesseract", 0.01),
    ):
      with self.assertLogs(
        "pylabrobot.thermo_fisher.btx.gemini.X2.the_ghost_touch", level="WARNING"
      ):
        self.assertEqual(detector.ocr_text("missing.png", psm=6), "")

  def test_marker_and_protocol_matching_require_token_boundaries(self):
    detector = _GeminiScreenDetector(min_conf=0.70)

    self.assertFalse(detector.contains_marker("number of columns", "no"))
    self.assertTrue(detector.contains_marker("answer yes or no", "no"))
    self.assertTrue(detector.contains_marker("the mainmenu screen", "main menu"))
    self.assertTrue(detector.protocol_name_matches("Run !PLR_123", "!PLR_123"))
    self.assertTrue(detector.protocol_name_matches("Run IPLR_123", "!PLR_123"))
    self.assertFalse(detector.protocol_name_matches("Run !PLR_1234", "!PLR_123"))

  def test_confirm_dialog_does_not_match_unrelated_no_substrings(self):
    detector = _GeminiScreenDetector(min_conf=0.70)
    detection = detector.detect_state("Run Protocol number of columns GO Set Meas")

    self.assertFalse(detector.has_confirm_dialog(detection))

  def test_fixture_metadata_markers_classify_without_tesseract(self):
    detector = _GeminiScreenDetector(min_conf=0.70)
    metadata = json.loads((SCREEN_FIXTURES / "metadata.json").read_text())

    for screen in metadata["screens"]:
      text = " ".join(screen["matched"]) or "electroporation method summary"
      with self.subTest(image=screen["image"]):
        self.assertEqual(detector.detect_state(text).state, screen["state"])

  def test_decode_rsi_framebuffer_uses_bgrx_pixels_and_opaque_alpha(self):
    framebuffer = bytes((12, 34, 56, 0)) * (FRAME_W * FRAME_H)

    rgba = _decode_rsi_framebuffer(framebuffer)

    self.assertEqual(rgba.shape, (FRAME_H, FRAME_W, 4))
    self.assertEqual(rgba[0, 0].tolist(), [56, 34, 12, 255])
    self.assertEqual(int(rgba[:, :, 3].min()), 255)
    self.assertEqual(int(rgba[:, :, 3].max()), 255)

  async def test_rsi_transport_reads_bgrx_frame_via_shared_serial_interface(self):
    framebuffer = bytes((12, 34, 56, 0)) * (FRAME_W * FRAME_H)
    fake = _FakeAsyncSerial(reads=[framebuffer[:900000], framebuffer[900000:] + b":"])
    transport = _RSITransport(
      port="/dev/test",
      baud=115200,
      timeout=0.2,
      retries=1,
      serial_io=fake,
    )

    await transport.setup()
    try:
      frame = await transport.read_frame()
    finally:
      await transport.stop()

    fake.setup.assert_awaited_once_with()
    fake.stop.assert_awaited_once_with()
    self.assertGreaterEqual(fake.reset_calls, 2)
    self.assertEqual(fake.writes[:2], [b"echo off\r", b"scap\r"])
    self.assertEqual(frame.raw_len, FRAME_BYTES + 1)
    self.assertEqual(frame.rgba.shape, (FRAME_H, FRAME_W, 4))
    self.assertEqual(frame.rgba[0, 0].tolist(), [56, 34, 12, 255])

  async def test_rsi_transport_can_disable_frame_retries(self):
    transport = _RSITransport(
      port="/dev/test",
      baud=115200,
      timeout=0.2,
      retries=5,
      serial_io=_FakeAsyncSerial(),
    )

    with patch.object(
      transport,
      "_read_frame_once",
      AsyncMock(side_effect=TimeoutError("frame unavailable")),
    ) as read_once:
      with self.assertRaisesRegex(TimeoutError, "frame unavailable"):
        await transport.read_frame(retry=False)

    read_once.assert_awaited_once_with()

  async def test_rsi_transport_cleans_up_a_partial_setup_failure(self):
    fake = _FakeAsyncSerial()
    fake.setup.side_effect = RuntimeError("open failed")
    transport = _RSITransport(
      port="/dev/test",
      baud=115200,
      timeout=0.2,
      retries=1,
      serial_io=fake,
    )

    with self.assertRaisesRegex(RuntimeError, "open failed"):
      await transport.setup()

    fake.stop.assert_awaited_once_with()

  async def test_wait_for_states_can_explicitly_accept_unknown(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(STATE_UNKNOWN, image_path="unknown")

    result = await touch.wait_for_states({STATE_UNKNOWN}, timeout=0.1, interval=0, prefix="unknown")

    self.assertIsNotNone(result)
    assert result is not None
    self.assertEqual(result.image_path, "unknown")

  async def test_start_run_refuses_a_blind_second_go_tap(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
    )
    still_prerun = Snapshot(
      frame=cast(FrameCapture, None),
      image_path="still-prerun",
      detection=Detection(
        state=STATE_PROTOCOL_RUN_VIEW,
        confidence=1.0,
        matched=[],
        text="Run Protocol GO Set Meas",
        text_norm="run protocol go set meas",
      ),
    )

    with patch.object(touch, "_wait_for_run_transition", AsyncMock(return_value=still_prerun)):
      with self.assertRaisesRegex(RuntimeError, "refusing to tap again"):
        await touch.start_run()

    self.assertEqual(len(touch.taps), 1)

  def test_user_protocols_top_detector_uses_double_up_arrow_state(self):
    detector = _GeminiScreenDetector(min_conf=0.70)

    self.assertTrue(
      detector.user_protocols_double_up_active(
        str(SCREEN_FIXTURES / "user_protocols_double_up_active.png")
      )
    )
    self.assertFalse(
      detector.user_protocols_double_up_active(
        str(SCREEN_FIXTURES / "user_protocols_double_up_inactive.png")
      )
    )

  @pytest.mark.skipif(shutil.which("tesseract") is None, reason="requires tesseract OCR")
  def test_selected_screen_fixtures_match_detector_states(self):
    detector = _GeminiScreenDetector(min_conf=0.70)
    cases = (
      ("00_main_menu.png", STATE_MAIN_MENU),
      ("01_user_protocols_top.png", STATE_USER_PROTOCOLS),
      ("02_protocol_summary.png", STATE_UNKNOWN),
      ("03_run_protocol_prerun.png", STATE_PROTOCOL_RUN_VIEW),
      ("04_set_plate_columns_open.png", STATE_PROTOCOL_DETAILS),
      ("05_set_plate_columns_after_first_confirm.png", STATE_PROTOCOL_DETAILS),
      ("06_set_plate_columns_confirmed_run_view.png", STATE_PROTOCOL_RUN_VIEW),
      ("07_go_prerun.png", STATE_PROTOCOL_RUN_VIEW),
      ("08_go_delivering_pulse.png", STATE_PROTOCOL_RUN_VIEW),
      ("09_go_pulses_delivered.png", STATE_PROTOCOL_FINISH),
      ("10_returned_home_after_go.png", STATE_MAIN_MENU),
    )

    for filename, expected_state in cases:
      with self.subTest(filename=filename):
        detection = detector.classify_image(str(SCREEN_FIXTURES / filename))

        self.assertEqual(detection.state, expected_state)
        if expected_state != STATE_UNKNOWN:
          self.assertGreaterEqual(detection.confidence, 0.70)

  @pytest.mark.skipif(shutil.which("tesseract") is None, reason="requires tesseract OCR")
  def test_selected_screen_fixtures_cover_protocol_name_crops(self):
    detector = _GeminiScreenDetector(min_conf=0.70)
    protocol_name = self._fixture_protocol_name()

    summary = detector.classify_image(str(SCREEN_FIXTURES / "02_protocol_summary.png"))

    self.assertTrue(detector.looks_user_protocol_summary(summary))
    self.assertTrue(
      detector.summary_matches_protocol(
        str(SCREEN_FIXTURES / "02_protocol_summary.png"), protocol_name
      )
    )
    run_view_fixtures = (
      "03_run_protocol_prerun.png",
      "06_set_plate_columns_confirmed_run_view.png",
      "07_go_prerun.png",
      "08_go_delivering_pulse.png",
      "09_go_pulses_delivered.png",
    )
    for filename in run_view_fixtures:
      with self.subTest(filename=filename):
        self.assertTrue(
          detector.run_view_matches_protocol(str(SCREEN_FIXTURES / filename), protocol_name)
        )

  @pytest.mark.skipif(shutil.which("tesseract") is None, reason="requires tesseract OCR")
  def test_selected_screen_fixtures_cover_two_step_plate_columns_confirm(self):
    detector = _GeminiScreenDetector(min_conf=0.70)

    opened = detector.classify_image(str(SCREEN_FIXTURES / "04_set_plate_columns_open.png"))
    first_confirm = detector.classify_image(
      str(SCREEN_FIXTURES / "05_set_plate_columns_after_first_confirm.png")
    )
    confirmed = detector.classify_image(
      str(SCREEN_FIXTURES / "06_set_plate_columns_confirmed_run_view.png")
    )

    self.assertEqual(opened.state, STATE_PROTOCOL_DETAILS)
    self.assertEqual(first_confirm.state, STATE_PROTOCOL_DETAILS)
    self.assertEqual(confirmed.state, STATE_PROTOCOL_RUN_VIEW)

  @pytest.mark.skipif(shutil.which("tesseract") is None, reason="requires tesseract OCR")
  def test_selected_screen_fixtures_cover_go_to_completion(self):
    detector = _GeminiScreenDetector(min_conf=0.70)

    prerun = detector.classify_image(str(SCREEN_FIXTURES / "07_go_prerun.png"))
    delivering = detector.classify_image(str(SCREEN_FIXTURES / "08_go_delivering_pulse.png"))
    finished = detector.classify_image(str(SCREEN_FIXTURES / "09_go_pulses_delivered.png"))
    home = detector.classify_image(str(SCREEN_FIXTURES / "10_returned_home_after_go.png"))

    self.assertTrue(detector.looks_prerun(prerun))
    self.assertEqual(delivering.state, STATE_PROTOCOL_RUN_VIEW)
    self.assertIn("delivering pulse", delivering.matched)
    self.assertFalse(detector.looks_prerun(delivering))
    self.assertTrue(detector.is_run_done(finished))
    self.assertIn("pulses delivered", finished.matched)
    self.assertEqual(home.state, STATE_MAIN_MENU)

  async def test_prepare_user_protocol_accepts_direct_summary_after_row_tap(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(STATE_MAIN_MENU, text="Main Menu", text_norm="main menu")
    touch.queue_snapshot(STATE_USER_PROTOCOLS, text="User Protocols", text_norm="user protocols")
    touch.queue_snapshot(
      STATE_UNKNOWN,
      text="Exponential Decay Voltage Resistance Capacitance Number of Pulses",
      text_norm="exponential decay voltage resistance capacitance number of pulses",
      image_path="summary",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
      image_path="run-view",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
      image_path="verify",
    )

    result = await touch.prepare_user_protocol("!PLR_123")

    self.assertEqual(result.run_view.state, STATE_PROTOCOL_RUN_VIEW)
    self.assertEqual(result.prepared_verification.state, STATE_PROTOCOL_RUN_VIEW)
    self.assertGreaterEqual(len(touch.taps), 3)

  async def test_start_prepared_user_protocol_verifies_then_waits_done(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
      image_path="verify",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
      image_path="before-go",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol delivering pulse",
      text_norm="run protocol delivering pulse",
      image_path="after-go",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_FINISH,
      text="Run Protocol pulses delivered completed",
      text_norm="run protocol pulses delivered completed",
      image_path="done",
    )
    touch.queue_snapshot(
      STATE_MAIN_MENU, text="Main Menu", text_norm="main menu", image_path="home"
    )

    with patch.object(
      touch,
      "_snapshot_run_poll",
      wraps=touch._snapshot_run_poll,
    ) as run_poll:
      result = await touch.start_prepared_user_protocol(
        "!PLR_123", home_after=True, max_run_seconds=10.0
      )

    self.assertEqual(result.verification.image_path, "verify")
    self.assertEqual(result.completed.state, STATE_PROTOCOL_FINISH)
    self.assertIsNotNone(result.home)
    assert result.home is not None
    self.assertEqual(result.home.state, STATE_MAIN_MENU)
    run_poll.assert_awaited_once_with("run-wait-00")

  async def test_ensure_home_closes_protocol_details_before_home(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(
      STATE_PROTOCOL_DETAILS,
      text="Set Plate Columns",
      text_norm="set plate columns",
      image_path="details",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
      image_path="run-view",
    )
    touch.queue_snapshot(
      STATE_MAIN_MENU,
      text="Main Menu",
      text_norm="main menu",
      image_path="home",
    )

    result = await touch.ensure_home()

    self.assertEqual(result.image_path, "home")
    self.assertEqual(touch.taps[0][:2], (739, 414))
    self.assertEqual(touch.taps[1][:2], (726, 326))

  async def test_set_plate_columns_confirms_again_when_details_remains_open(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
      image_path="run-view-start",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_DETAILS,
      text="Set Plate Columns",
      text_norm="set plate columns",
      image_path="details-open",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_DETAILS,
      text="Set Plate Columns",
      text_norm="set plate columns",
      image_path="details-after-first-confirm",
    )
    touch.queue_snapshot(
      STATE_PROTOCOL_RUN_VIEW,
      text="Run Protocol GO Set Meas",
      text_norm="run protocol go set meas",
      image_path="run-view-confirmed",
    )

    result = await touch.set_plate_columns(3)

    self.assertEqual(result.image_path, "run-view-confirmed")
    self.assertEqual(touch.taps[-2][:2], (739, 414))
    self.assertEqual(touch.taps[-1][:2], (739, 414))

  async def test_cancel_prepared_user_protocol_homes(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(
      STATE_MAIN_MENU, text="Main Menu", text_norm="main menu", image_path="home"
    )

    result = await touch.cancel_prepared_user_protocol()

    self.assertTrue(result.cancelled)
    self.assertEqual(result.final_state.image_path, "home")

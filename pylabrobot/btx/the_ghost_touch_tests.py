import json
from pathlib import Path
import shutil
import unittest
from typing import Optional, cast
from unittest.mock import patch

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("serial")

from pylabrobot.btx.the_ghost_touch import (
  Detection,
  FRAME_BYTES,
  FRAME_H,
  FRAME_W,
  FrameCapture,
  _RSITransport,
  _GeminiScreenDetector,
  _decode_rsi_framebuffer,
  Snapshot,
  STATE_MAIN_MENU,
  STATE_PROTOCOL_DETAILS,
  STATE_PROTOCOL_FINISH,
  STATE_PROTOCOL_RUN_VIEW,
  STATE_UNKNOWN,
  STATE_USER_PROTOCOLS,
  TheGhostTouch,
)

SCREEN_FIXTURES = Path(__file__).parent / "test_data/gemini_x2/screens"


class _FakeAsyncSerial:
  def __init__(self, reads: Optional[list[bytes]] = None):
    self.reads: list[bytes] = list(reads or [])
    self.writes: list[bytes] = []
    self.setup_calls = 0
    self.stop_calls = 0
    self.reset_calls = 0

  async def setup(self) -> None:
    self.setup_calls += 1

  async def stop(self) -> None:
    self.stop_calls += 1

  async def write(self, data: bytes) -> None:
    self.writes.append(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    if not self.reads:
      return b""
    return self.reads.pop(0)

  async def reset_input_buffer(self) -> None:
    self.reset_calls += 1


class _TestGhostTouch(TheGhostTouch):
  def __init__(self) -> None:
    self.port = "/dev/test"
    self.baud = 115200
    self.timeout = 15.0
    self.retries = 1
    self.min_conf = 0.70
    self.down_ms = 70
    self.artifact_dir = "/tmp"
    self.ser = None
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

  def snapshot(self, prefix: str) -> Snapshot:
    del prefix
    if not self._snapshots:
      raise AssertionError("No queued snapshots left")
    return self._snapshots.pop(0)

  def tap(self, x: int, y: int, down_ms=None) -> None:
    self.taps.append((x, y, down_ms))

  def tap_and_wait(
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
    return self.snapshot("tap-and-wait")

  def _scroll_user_protocols_to_top(self, current: Snapshot) -> Snapshot:
    return current

  def _summary_matches_protocol(self, image_path: str, protocol_name: str):
    del image_path, protocol_name
    return True

  def _run_view_matches_protocol(self, image_path: str, protocol_name: str):
    del image_path, protocol_name
    return True


class TestTheGhostTouch(unittest.TestCase):
  def _fixture_protocol_name(self) -> str:
    metadata = json.loads((SCREEN_FIXTURES / "metadata.json").read_text())
    return str(metadata["temporary_protocol"]["name"])

  def test_require_dependencies_reports_missing_tesseract(self):
    touch = TheGhostTouch(port="/dev/test")

    with patch("pylabrobot.btx.the_ghost_touch.shutil.which", return_value=None):
      with self.assertRaisesRegex(RuntimeError, "external `tesseract` command"):
        touch._require_dependencies()

  def test_decode_rsi_framebuffer_uses_bgrx_pixels_and_opaque_alpha(self):
    framebuffer = bytes((12, 34, 56, 0)) * (FRAME_W * FRAME_H)

    rgba = _decode_rsi_framebuffer(framebuffer)

    self.assertEqual(rgba.shape, (FRAME_H, FRAME_W, 4))
    self.assertEqual(rgba[0, 0].tolist(), [56, 34, 12, 255])
    self.assertEqual(int(rgba[:, :, 3].min()), 255)
    self.assertEqual(int(rgba[:, :, 3].max()), 255)

  def test_rsi_transport_reads_bgrx_frame_via_shared_serial_interface(self):
    framebuffer = bytes((12, 34, 56, 0)) * (FRAME_W * FRAME_H)
    fake = _FakeAsyncSerial(reads=[framebuffer[:900000], framebuffer[900000:] + b":"])
    transport = _RSITransport(
      port="/dev/test",
      baud=115200,
      timeout=0.2,
      retries=1,
      serial_io=fake,
    )

    transport.open()
    try:
      frame = transport.read_frame()
    finally:
      transport.close()

    self.assertEqual(fake.setup_calls, 1)
    self.assertEqual(fake.stop_calls, 1)
    self.assertGreaterEqual(fake.reset_calls, 2)
    self.assertEqual(fake.writes[:2], [b"echo off\r", b"scap\r"])
    self.assertEqual(frame.raw_len, FRAME_BYTES + 1)
    self.assertEqual(frame.rgba.shape, (FRAME_H, FRAME_W, 4))
    self.assertEqual(frame.rgba[0, 0].tolist(), [56, 34, 12, 255])

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

  def test_prepare_user_protocol_accepts_direct_summary_after_row_tap(self):
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

    result = touch.prepare_user_protocol("!PLR_123")

    self.assertEqual(result.run_view.state, STATE_PROTOCOL_RUN_VIEW)
    self.assertEqual(result.prepared_verification.state, STATE_PROTOCOL_RUN_VIEW)
    self.assertGreaterEqual(len(touch.taps), 3)

  def test_start_prepared_user_protocol_verifies_then_waits_done(self):
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

    result = touch.start_prepared_user_protocol("!PLR_123", home_after=True, max_run_seconds=10.0)

    self.assertEqual(result.verification.image_path, "verify")
    self.assertEqual(result.completed.state, STATE_PROTOCOL_FINISH)
    self.assertIsNotNone(result.home)
    assert result.home is not None
    self.assertEqual(result.home.state, STATE_MAIN_MENU)

  def test_ensure_home_closes_protocol_details_before_home(self):
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

    result = touch.ensure_home()

    self.assertEqual(result.image_path, "home")
    self.assertEqual(touch.taps[0][:2], (739, 414))
    self.assertEqual(touch.taps[1][:2], (726, 326))

  def test_set_plate_columns_confirms_again_when_details_remains_open(self):
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

    result = touch.set_plate_columns(3)

    self.assertEqual(result.image_path, "run-view-confirmed")
    self.assertEqual(touch.taps[-2][:2], (739, 414))
    self.assertEqual(touch.taps[-1][:2], (739, 414))

  def test_cancel_prepared_user_protocol_homes(self):
    touch = _TestGhostTouch()
    touch.queue_snapshot(
      STATE_MAIN_MENU, text="Main Menu", text_norm="main menu", image_path="home"
    )

    result = touch.cancel_prepared_user_protocol(home_after=True)

    self.assertTrue(result.cancelled)
    self.assertEqual(result.final_state.image_path, "home")

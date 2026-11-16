"""HID feature reports and capture replay without a connected HID device."""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from pylabrobot.io.capture import CaptureReader, start_capture, stop_capture
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.hid import HID, HIDValidator


class HIDTests(unittest.IsolatedAsyncioTestCase):
  async def test_feature_report_uses_worker_and_capture_can_replay(self) -> None:
    io = HID("test", 1, 2)
    io._executor = ThreadPoolExecutor(max_workers=1)
    device = MagicMock()
    io.device = device
    threads = []

    def send(data: bytes) -> int:
      """Record the execution thread and accept the full report."""
      threads.append(threading.get_ident())
      return len(data)

    device.send_feature_report.side_effect = send
    device.read.return_value = b"\x01\xff"
    device.write.return_value = 3
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "hid.json"
      start_capture(path)
      try:
        self.assertEqual(await io.send_feature_report(b"\x01\x00"), 3)
        self.assertEqual(await io.send_feature_report(b"abc", report_id=b"\x02"), 4)
        await io.write(b"\x03\x00")
        self.assertEqual(await io.read(64, 100), b"\x01\xff")
      finally:
        stop_capture()
        await io.stop()
      self.assertNotEqual(threads[0], threading.get_ident())
      self.assertEqual(len(set(threads)), 1)
      device.send_feature_report.assert_any_call(b"\x00\x01\x00")
      device.send_feature_report.assert_any_call(b"\x02abc")
      reader = CaptureReader(str(path))
      validator = HIDValidator(reader, "test", vid=1, pid=2)
      self.assertEqual(await validator.send_feature_report(b"\x01\x00"), 3)
      self.assertEqual(await validator.send_feature_report(b"abc", report_id=b"\x02"), 4)
      await validator.write(b"\x03\x00")
      self.assertEqual(await validator.read(64, 100), b"\x01\xff")
      reader.done()

  async def test_feature_report_requires_setup_and_valid_report_id(self) -> None:
    io = HID("test", 1, 2)
    with self.assertRaises(RuntimeError):
      await io.send_feature_report(b"payload")
    with self.assertRaises(ValueError):
      await io.send_feature_report(b"payload", report_id=b"too long")

  async def test_short_feature_write_is_not_recorded_as_success(self) -> None:
    io = HID("test", 1, 2)
    io._executor = ThreadPoolExecutor(max_workers=1)
    io.device = MagicMock()
    io.device.send_feature_report.return_value = 1
    try:
      with patch("pylabrobot.io.hid.capturer.record") as record:
        with self.assertRaisesRegex(OSError, "Short HID"):
          await io.send_feature_report(b"\x01\x00")
        record.assert_not_called()
    finally:
      await io.stop()

  async def test_validator_rejects_wrong_device_action_or_payload(self) -> None:
    for change in (
      {"device_id": "other"},
      {"action": "write"},
      {"module": "usb"},
      {"data": "000200"},
    ):
      with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "capture.json"
        command = {
          "module": "hid",
          "device_id": "1:2:None",
          "action": "send_feature_report",
          "data": "000100",
        }
        command.update(change)
        path.write_text(json.dumps({"commands": [command]}))
        validator = HIDValidator(CaptureReader(str(path)), "test", vid=1, pid=2)
        with self.assertRaises(ValidationError):
          await validator.send_feature_report(b"\x01\x00")

  async def test_validator_lifecycle_and_short_input_report(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      path.write_text(
        json.dumps(
          {
            "commands": [
              {"module": "hid", "device_id": "1:2:None", "action": action, "data": data}
              for action, data in [("open", ""), ("read", "01ff"), ("close", "")]
            ]
          }
        )
      )
      reader = CaptureReader(str(path))
      validator = HIDValidator(reader, "test", vid=1, pid=2)
      await validator.setup()
      self.assertEqual(await validator.read(64, 100), b"\x01\xff")
      await validator.stop()
      reader.done()

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pylabrobot.io import capture as capture_module
from pylabrobot.io.capture import CaptureReader, capturer
from pylabrobot.io.serial import Serial, SerialValidator


def _reset_capture_validation_state() -> None:
  """Reset the capture state after each serial test."""
  capture_module._capture_or_validation_active = False


class _FakePySerial:
  """Provide deterministic serial data without a physical device."""

  def __init__(self, read_data: bytes, line_data: bytes) -> None:
    self._read_data = bytearray(read_data)
    self._line_data = line_data
    self.written = bytearray()
    self.is_open = True

  def write(self, data: bytes) -> int:
    self.written.extend(data)
    return len(data)

  def read(self, size: int) -> bytes:
    data = bytes(self._read_data[:size])
    del self._read_data[:size]
    return data

  def readline(self) -> bytes:
    return self._line_data

  def close(self) -> None:
    self.is_open = False


class SerialCaptureEncodingTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.addCleanup(_reset_capture_validation_state)

  async def test_binary_capture_replays_through_validator(self) -> None:
    written = bytes(range(256)) + b"\\U\\x\\"
    read = bytes(reversed(range(256))) + b"\\"
    line = b"line\\U\xff\n"
    serial = Serial(human_readable_device_name="test", port="/dev/test")
    serial._executor = ThreadPoolExecutor(max_workers=1)
    device = _FakePySerial(read_data=read, line_data=line)
    serial._ser = device  # type: ignore[assignment]

    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      capturer.start(path)
      self.addCleanup(lambda: capturer.capture_active and capturer.stop())
      await serial.write(written)
      captured_read = await serial.read(len(read))
      captured_line = await serial.readline()
      capturer.stop()
      await serial.stop()

      self.assertEqual(bytes(device.written), written)
      self.assertEqual(captured_read, read)
      self.assertEqual(captured_line, line)

      reader = CaptureReader(str(path))
      validator = SerialValidator(reader, human_readable_device_name="test", port="/dev/test")
      reader.start()
      await validator.write(written)
      self.assertEqual(await validator.read(len(read)), read)
      self.assertEqual(await validator.readline(), line)
      reader.done()


if __name__ == "__main__":
  unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.micronic import MicronicCodeReader, SaneScanner, TwainScanner
from pylabrobot.micronic.code_reader.driver import (
  DecodeResult,
  MicronicDriver,
  MicronicError,
)
from pylabrobot.micronic.code_reader.rack_reading_backend import MicronicRackReadingBackend
from pylabrobot.resources.tube_rack import TubeRack


def _rack(num_items_x: int = 12, num_items_y: int = 8, num_items: int = 96) -> TubeRack:
  rack = MagicMock(spec=TubeRack)
  rack.num_items_x = num_items_x
  rack.num_items_y = num_items_y
  rack.num_items = num_items
  return rack


def _mock_scanner(image_extension: str = "bmp") -> MagicMock:
  scanner = MagicMock()
  scanner.image_extension = image_extension
  scanner.acquire = MagicMock(return_value={"source": "test"})
  return scanner


class TestScannerClasses(unittest.IsolatedAsyncioTestCase):
  def test_sane_scanner_invokes_scanimage(self):
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "rack.tiff"
      with (
        patch(
          "pylabrobot.micronic.code_reader.scanner.shutil.which",
          return_value="/usr/bin/scanimage",
        ),
        patch(
          "pylabrobot.micronic.code_reader.scanner._run_scan_command",
          return_value={"source": "sane"},
        ) as run_scan_command,
      ):
        scanner = SaneScanner(sane_device="avision:libusb:001:004")
        metadata = scanner.acquire(output_path, timeout_ms=1000)

      self.assertEqual(metadata["source"], "sane")
      self.assertEqual(scanner.image_extension, "tiff")
      run_scan_command.assert_called_once_with(
        [
          "/usr/bin/scanimage",
          "--device-name",
          "avision:libusb:001:004",
          "--format=tiff",
          "--output-file",
          str(output_path),
        ],
        output_path,
        1000,
        source="sane",
      )

  def test_sane_scanner_raises_when_scanimage_missing(self):
    with patch("pylabrobot.micronic.code_reader.scanner.shutil.which", return_value=None):
      with self.assertRaises(MicronicError):
        SaneScanner()

  def test_twain_scanner_resolves_path_from_env(self):
    with (
      patch.dict(os.environ, {"MICRONIC_TWAIN_SCANNER_PATH": "/opt/twain_scan"}, clear=False),
      patch("pylabrobot.micronic.code_reader.scanner.shutil.which", return_value=None),
    ):
      scanner = TwainScanner()
    self.assertEqual(scanner.twain_scanner_path, "/opt/twain_scan")

  def test_twain_scanner_raises_when_helper_missing(self):
    with (
      patch.dict(os.environ, {}, clear=True),
      patch("pylabrobot.micronic.code_reader.scanner.shutil.which", return_value=None),
    ):
      with self.assertRaises(MicronicError):
        TwainScanner()

  def test_twain_scanner_acquire_runs_helper(self):
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "rack.bmp"
      with patch(
        "pylabrobot.micronic.code_reader.scanner._run_scan_command",
        return_value={"source": "twain"},
      ) as run_scan_command:
        scanner = TwainScanner(twain_scanner_path="/opt/twain_scan", twain_source="AVA6PlusG")
        scanner.acquire(output_path, timeout_ms=1000)

      run_scan_command.assert_called_once_with(
        ["/opt/twain_scan", str(output_path), "AVA6PlusG", "1000"],
        output_path,
        1000,
        source="twain",
      )


class TestMicronicDriver(unittest.IsolatedAsyncioTestCase):
  async def test_scan_rack_populates_standard_rack_result(self):
    with tempfile.TemporaryDirectory() as image_dir:
      scanner = _mock_scanner()
      driver = MicronicDriver(
        scanner=scanner,
        serial_port="/dev/ttyUSB0",
        image_dir=image_dir,
        keep_images=True,
      )
      decoded = {
        "A1": DecodeResult(tube_id="1111111111", method="test"),
        "A2": DecodeResult(tube_id="2222222222", method="test"),
      }
      with (
        patch.object(driver.io, "setup", AsyncMock()),
        patch.object(driver.io, "stop", AsyncMock()),
        patch.object(
          driver, "scan_rack_id", AsyncMock(return_value="9500017722")
        ) as scan_rack_id_mock,
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 2}),
        ) as decode_image_mock,
      ):
        await driver.setup()
        result = await driver.scan_rack(_rack(num_items=2))

      self.assertEqual(result.rack_id, "9500017722")
      self.assertEqual(result.entries[0].position, "A1")
      self.assertEqual(result.entries[0].tube_id, "1111111111")
      self.assertEqual(result.entries[1].tube_id, "2222222222")
      self.assertEqual(driver.last_scan_metadata, {"source": "test"})
      self.assertEqual(driver.last_decode_metadata, {"decodedWells": 2})
      scanner.acquire.assert_called_once()
      scan_rack_id_mock.assert_awaited_once()
      decode_image_mock.assert_called_once()

  async def test_reader_can_scan_twice(self):
    with tempfile.TemporaryDirectory() as image_dir:
      scanner = _mock_scanner()
      reader = MicronicCodeReader(
        scanner=scanner,
        serial_port="/dev/ttyUSB0",
        image_dir=image_dir,
        keep_images=True,
      )
      decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}
      with (
        patch.object(reader.driver.io, "setup", AsyncMock()),
        patch.object(reader.driver.io, "stop", AsyncMock()),
        patch.object(reader.driver, "scan_rack_id", AsyncMock(return_value="9500017722")),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await reader.setup()
        first = await reader.rack_reading.scan_rack(
          rack=_rack(num_items=1), timeout=1.0, poll_interval=0.0
        )
        second = await reader.rack_reading.scan_rack(
          rack=_rack(num_items=1), timeout=1.0, poll_interval=0.0
        )

      self.assertEqual(first.rack_id, "9500017722")
      self.assertEqual(second.rack_id, "9500017722")
      self.assertEqual(scanner.acquire.call_count, 2)

  async def test_driver_scan_rack_id_uses_plr_serial(self):
    instances: list[object] = []

    class FakeSerial:
      def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.reads = iter([b"9", b"5", b"0", b"0", b"0", b"1", b"7", b"7", b"2", b"2", b"\r"])
        self.calls: list[str] = []
        instances.append(self)

      async def setup(self):
        self.calls.append("setup")

      async def reset_input_buffer(self):
        self.calls.append("reset_input_buffer")

      async def write(self, data: bytes):
        self.calls.append(f"write:{data!r}")

      async def read(self, num_bytes: int = 1) -> bytes:
        self.calls.append(f"read:{num_bytes}")
        return next(self.reads)

      async def stop(self):
        self.calls.append("stop")

    with patch("pylabrobot.micronic.code_reader.driver.Serial", FakeSerial):
      driver = MicronicDriver(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
      await driver.setup()
      try:
        rack_id = await driver.scan_rack_id()
      finally:
        await driver.stop()

    self.assertEqual(len(instances), 1)
    fake_serial = cast(FakeSerial, instances[0])
    self.assertEqual(rack_id, "9500017722")
    self.assertEqual(fake_serial.kwargs["port"], "/dev/ttyUSB0")
    self.assertEqual(fake_serial.kwargs["bytesize"], 7)
    self.assertEqual(fake_serial.kwargs["parity"], "E")
    self.assertIn("setup", fake_serial.calls)
    self.assertIn("reset_input_buffer", fake_serial.calls)
    self.assertIn("write:b'<t>\\r\\n'", fake_serial.calls)
    self.assertEqual(fake_serial.calls[-1], "stop")

  async def test_driver_rejects_mismatched_rack_shape(self):
    driver = MicronicDriver(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    with self.assertRaises(MicronicError):
      await driver.scan_rack(_rack(num_items_x=6, num_items_y=4))


class TestMicronicRackReadingBackend(unittest.IsolatedAsyncioTestCase):
  def _backend_with_mocked_driver(self) -> tuple[MicronicRackReadingBackend, MagicMock]:
    driver = MagicMock(spec=MicronicDriver)
    driver.scan_rack = AsyncMock()
    driver.scan_rack_id = AsyncMock()
    return MicronicRackReadingBackend(driver=driver), driver

  async def test_scan_rack_delegates_to_driver(self):
    backend, driver = self._backend_with_mocked_driver()
    expected = MagicMock()
    driver.scan_rack.return_value = expected
    rack = _rack()

    result = await backend.scan_rack(rack=rack, timeout=1.0, poll_interval=0.0)

    self.assertIs(result, expected)
    driver.scan_rack.assert_awaited_once_with(rack)

  async def test_scan_rack_times_out(self):
    backend, driver = self._backend_with_mocked_driver()

    async def slow(rack):
      del rack
      import asyncio

      await asyncio.sleep(1)
      return MagicMock()

    driver.scan_rack.side_effect = slow

    with self.assertRaises(TimeoutError):
      await backend.scan_rack(rack=_rack(), timeout=0.01, poll_interval=0.0)

  async def test_scan_rack_propagates_driver_micronic_error(self):
    backend, driver = self._backend_with_mocked_driver()
    driver.scan_rack.side_effect = MicronicError("rack shape mismatch")

    with self.assertRaises(MicronicError):
      await backend.scan_rack(
        rack=_rack(num_items_x=6, num_items_y=4), timeout=1.0, poll_interval=0.0
      )

  async def test_scan_rack_id_delegates_to_driver(self):
    backend, driver = self._backend_with_mocked_driver()
    driver.scan_rack_id.return_value = "9500017722"

    rack_id = await backend.scan_rack_id(timeout=5.0, poll_interval=0.5)

    self.assertEqual(rack_id, "9500017722")
    driver.scan_rack_id.assert_awaited_once_with(timeout=5.0, poll_interval=0.5)


class TestMicronicCodeReader(unittest.IsolatedAsyncioTestCase):
  async def test_device_exposes_rack_reading_only(self):
    reader = MicronicCodeReader(
      scanner=_mock_scanner(),
      serial_port="/dev/ttyUSB0",
      timeout=12.0,
      poll_interval=0.25,
    )
    with (
      patch.object(reader.driver.io, "setup", AsyncMock()),
      patch.object(reader.driver.io, "stop", AsyncMock()),
    ):
      await reader.setup()
      try:
        self.assertIn(reader.rack_reading, reader._capabilities)
        self.assertFalse(hasattr(reader, "barcode_scanning"))
        with patch.object(
          reader.rack_reading,
          "scan_rack",
          return_value=MagicMock(rack_id="9500017722"),
        ) as scan_rack:
          result = await reader.rack_reading.scan_rack(
            rack=_rack(),
            timeout=reader.default_timeout,
            poll_interval=reader.default_poll_interval,
          )
      finally:
        await reader.stop()

    self.assertEqual(result.rack_id, "9500017722")
    scan_rack.assert_called_once()

  async def test_frontend_uses_driver(self):
    reader = MicronicCodeReader(
      scanner=_mock_scanner(),
      serial_port="/dev/ttyUSB0",
    )
    self.assertIsInstance(reader.driver, MicronicDriver)
    self.assertFalse(hasattr(reader, "barcode_scanning"))


if __name__ == "__main__":
  unittest.main()

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.micronic import MicronicCodeReader, MicronicError, SaneScanner, TwainScanner
from pylabrobot.micronic.code_reader.driver import DecodeResult
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


def _run_inline(_executor, function, *args):
  future = asyncio.get_running_loop().create_future()
  try:
    future.set_result(function(*args))
  except Exception as exc:
    future.set_exception(exc)
  return future


class TestScannerClasses(unittest.TestCase):
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


class TestMicronicCodeReader(unittest.IsolatedAsyncioTestCase):
  def test_acquire_image_runs_scanner_and_tracks_metadata(self):
    with tempfile.TemporaryDirectory() as image_dir:
      scanner = _mock_scanner()
      reader = MicronicCodeReader(
        scanner=scanner,
        serial_port="/dev/ttyUSB0",
        image_dir=image_dir,
        scanner_timeout=1.25,
        keep_images=True,
      )
      image_path = reader._acquire_image()

      self.assertEqual(reader.last_image_path, image_path)
      self.assertEqual(reader.last_scan_metadata, {"source": "test"})
      scanner.acquire.assert_called_once_with(image_path, 1250)
      self.assertTrue(image_path.name.startswith("micronic_"))
      self.assertEqual(image_path.suffix, ".bmp")

  async def test_scan_rack_id_uses_plr_serial(self):
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
      reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
      await reader.setup()
      try:
        rack_id = await reader.scan_rack_id(timeout=1.0)
      finally:
        await reader.stop()

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

  async def test_scan_rack_populates_result(self):
    with tempfile.TemporaryDirectory() as image_dir:
      scanner = _mock_scanner()
      reader = MicronicCodeReader(
        scanner=scanner,
        serial_port="/dev/ttyUSB0",
        image_dir=image_dir,
        keep_images=True,
      )
      decoded = {
        "A1": DecodeResult(tube_id="1111111111", method="test"),
        "A2": DecodeResult(tube_id="2222222222", method="test"),
      }
      loop = asyncio.get_running_loop()
      with (
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")) as read_barcode,
        patch.object(loop, "run_in_executor", side_effect=_run_inline),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 2}),
        ) as decode_image_mock,
      ):
        result = await reader.scan_rack(_rack(num_items=2), timeout=1.0)

      self.assertEqual(result.rack_id, "9500017722")
      rack_barcode = result.rack_barcode
      assert rack_barcode is not None
      self.assertEqual(rack_barcode.data, "9500017722")
      self.assertEqual(rack_barcode.symbology, "Code 128 (Subset B and C)")
      self.assertEqual(result.entries[0].position, "A1")
      self.assertEqual(result.entries[0].tube_id, "1111111111")
      tube_barcode = result.entries[0].barcode
      assert tube_barcode is not None
      self.assertEqual(tube_barcode.data, "1111111111")
      self.assertEqual(tube_barcode.symbology, "DataMatrix")
      self.assertEqual(result.entries[1].tube_id, "2222222222")
      self.assertEqual(reader.last_scan_metadata, {"source": "test"})
      self.assertEqual(reader.last_decode_metadata, {"decodedWells": 2})
      scanner.acquire.assert_called_once()
      read_barcode.assert_awaited_once()
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
      loop = asyncio.get_running_loop()
      with (
        patch.object(reader.io, "setup", AsyncMock()),
        patch.object(reader.io, "stop", AsyncMock()),
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
        patch.object(loop, "run_in_executor", side_effect=_run_inline),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await reader.setup()
        try:
          first = await reader.scan_rack(rack=_rack(num_items=1), timeout=1.0)
          second = await reader.scan_rack(rack=_rack(num_items=1), timeout=1.0)
        finally:
          await reader.stop()

      self.assertEqual(first.rack_id, "9500017722")
      self.assertEqual(second.rack_id, "9500017722")
      self.assertEqual(scanner.acquire.call_count, 2)

  async def test_rejects_mismatched_rack_shape(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    with self.assertRaises(MicronicError):
      await reader.scan_rack(_rack(num_items_x=6, num_items_y=4), timeout=1.0)

  async def test_rejects_concurrent_scan(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    await reader._scan_lock.acquire()
    try:
      with self.assertRaises(MicronicError):
        await reader.scan_rack(_rack(), timeout=1.0)
    finally:
      reader._scan_lock.release()

  async def test_scan_rack_times_out(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")

    async def slow(rack):
      del rack
      await asyncio.sleep(1)
      return MagicMock()

    with patch.object(reader, "_scan_rack", slow):
      with self.assertRaises(TimeoutError):
        await reader.scan_rack(rack=_rack(), timeout=0.01)

  async def test_timeout_keeps_scan_lock_until_blocking_scan_finishes(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    loop = asyncio.get_running_loop()
    scan_future = loop.create_future()
    loop.call_later(0.05, scan_future.set_result, MagicMock())

    with (
      patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
      patch.object(loop, "run_in_executor", return_value=scan_future),
    ):
      with self.assertRaises(TimeoutError):
        await reader.scan_rack(rack=_rack(), timeout=0.01)
      with self.assertRaises(MicronicError):
        await reader.scan_rack(rack=_rack(), timeout=0.01)

      await asyncio.sleep(0.08)
      self.assertFalse(reader._scan_lock.locked())

  async def test_scan_rack_propagates_micronic_error(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    with self.assertRaises(MicronicError):
      await reader.scan_rack(
        rack=_rack(num_items_x=6, num_items_y=4),
        timeout=1.0,
      )

  async def test_scan_rack_id_reads_barcode(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    with patch.object(
      reader, "_read_barcode", AsyncMock(return_value="9500017722")
    ) as read_barcode:
      rack_id = await reader.scan_rack_id(timeout=5.0)

      self.assertEqual(rack_id, "9500017722")
      read_barcode.assert_awaited_once_with()


if __name__ == "__main__":
  unittest.main()

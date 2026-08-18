import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.io.command_line import CommandLineResult, CommandLineTransport
from pylabrobot.micronic import MicronicCodeReader, MicronicError, SaneScanner, TwainScanner
from pylabrobot.micronic.code_reader.driver import (
  DecodeResult,
  cluster_axis,
  decode_image,
  find_duplicate_ids,
  is_tube_id,
  iter_positions,
  rack_position,
)
from pylabrobot.resources.tube_rack import TubeRack


def _rack(num_items_x: int = 12, num_items_y: int = 8, num_items: int = 96) -> TubeRack:
  rack = MagicMock(spec=TubeRack)
  rack.num_items_x = num_items_x
  rack.num_items_y = num_items_y
  rack.num_items = num_items
  return rack


def _mock_scanner(image_extension: str = "bmp") -> MagicMock:
  """Return a scanner mock that succeeds without accessing hardware."""
  scanner = MagicMock()
  scanner.image_extension = image_extension
  scanner.setup = AsyncMock()
  scanner.stop = AsyncMock()
  scanner.acquire = AsyncMock(return_value={"source": "test"})
  return scanner


def _mock_command_line(
  executable: str,
  result: CommandLineResult = CommandLineResult(0, "", ""),
) -> MagicMock:
  """Return a configured command-line I/O mock."""
  command_line = MagicMock(spec=CommandLineTransport)
  command_line.executable = executable
  command_line.setup = AsyncMock()
  command_line.stop = AsyncMock()
  command_line.run = AsyncMock(return_value=result)
  return command_line


def _run_inline(_executor, function, *args):
  """Execute an executor callback inline and return its result as a future."""
  future = asyncio.get_running_loop().create_future()
  try:
    future.set_result(function(*args))
  except Exception as exc:
    future.set_exception(exc)
  return future


class TestScannerClasses(unittest.IsolatedAsyncioTestCase):
  """Tests for scanner command construction and error handling."""

  async def test_sane_scanner_invokes_scanimage(self):
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "rack.tiff"
      output_path.touch()
      command_line = _mock_command_line("/usr/bin/scanimage")
      scanner = SaneScanner(
        sane_device="avision:libusb:001:004",
        command_line=command_line,
      )
      await scanner.setup()
      metadata = await scanner.acquire(output_path, timeout=1.0)
      await scanner.stop()

      self.assertEqual(metadata["source"], "sane")
      self.assertEqual(scanner.image_extension, "tiff")
      command_line.setup.assert_awaited_once_with()
      command_line.stop.assert_awaited_once_with()
      command_line.run.assert_awaited_once_with(
        [
          "--device-name",
          "avision:libusb:001:004",
          "--format=tiff",
          "--output-file",
          str(output_path),
        ],
        timeout=16.0,
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

  async def test_twain_scanner_acquire_runs_helper(self):
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "rack.bmp"
      output_path.touch()
      command_line = _mock_command_line("/opt/twain_scan")
      scanner = TwainScanner(
        twain_source="AVA6PlusG",
        command_line=command_line,
      )
      await scanner.setup()
      await scanner.acquire(output_path, timeout=1.25)
      await scanner.stop()

      command_line.run.assert_awaited_once_with(
        [str(output_path), "AVA6PlusG", "1250"],
        timeout=16.25,
      )

  async def test_scanner_raises_on_helper_failure(self):
    command_line = _mock_command_line(
      "/opt/twain_scan",
      result=CommandLineResult(2, "", "scanner fault"),
    )
    scanner = TwainScanner(command_line=command_line)

    with self.assertRaisesRegex(MicronicError, "scanner fault"):
      await scanner.acquire(Path("rack.bmp"), timeout=1.0)

  async def test_scanner_raises_when_helper_creates_no_image(self):
    command_line = _mock_command_line("/opt/twain_scan")
    scanner = TwainScanner(command_line=command_line)

    with tempfile.TemporaryDirectory() as image_dir:
      with self.assertRaisesRegex(MicronicError, "did not create image"):
        await scanner.acquire(Path(image_dir) / "rack.bmp", timeout=1.0)

  async def test_scanner_wraps_helper_timeout(self):
    command_line = _mock_command_line("/opt/twain_scan")
    command_line.run = AsyncMock(side_effect=asyncio.TimeoutError)
    scanner = TwainScanner(command_line=command_line)

    with self.assertRaisesRegex(MicronicError, "timed out"):
      await scanner.acquire(Path("rack.bmp"), timeout=1.0)

  async def test_scanner_wraps_missing_helper(self):
    command_line = _mock_command_line("/missing/twain_scan")
    command_line.run = AsyncMock(side_effect=FileNotFoundError)
    scanner = TwainScanner(command_line=command_line)

    with self.assertRaisesRegex(MicronicError, "was not found"):
      await scanner.acquire(Path("rack.bmp"), timeout=1.0)


class TestMicronicCodeReader(unittest.IsolatedAsyncioTestCase):
  """Tests for the public Micronic code-reader operations."""

  def test_rejects_non_positive_device_timeouts(self):
    with self.assertRaisesRegex(ValueError, "scanner_timeout"):
      MicronicCodeReader(
        scanner=_mock_scanner(),
        serial_port="/dev/ttyUSB0",
        scanner_timeout=0,
      )
    with self.assertRaisesRegex(ValueError, "serial_timeout"):
      MicronicCodeReader(
        scanner=_mock_scanner(),
        serial_port="/dev/ttyUSB0",
        serial_timeout=0,
      )

  async def test_acquire_image_runs_scanner_and_tracks_metadata(self):
    with tempfile.TemporaryDirectory() as image_dir:
      scanner = _mock_scanner()
      reader = MicronicCodeReader(
        scanner=scanner,
        serial_port="/dev/ttyUSB0",
        image_dir=image_dir,
        scanner_timeout=1.25,
        keep_images=True,
      )
      image_path = await reader._acquire_image()

      self.assertEqual(reader.last_image_path, image_path)
      self.assertEqual(reader.last_scan_metadata, {"source": "test"})
      scanner.acquire.assert_awaited_once_with(image_path, 1.25)
      self.assertTrue(image_path.name.startswith("micronic_"))
      self.assertEqual(image_path.suffix, ".bmp")

  async def test_acquire_image_removes_partial_image_after_failure(self):
    async def fail_after_writing(output_path: Path, timeout: float) -> None:
      """Create a partial output image before reporting acquisition failure."""
      del timeout
      output_path.touch()
      raise MicronicError("acquisition failed")

    with tempfile.TemporaryDirectory() as image_dir:
      scanner = _mock_scanner()
      scanner.acquire = AsyncMock(side_effect=fail_after_writing)
      reader = MicronicCodeReader(
        scanner=scanner,
        serial_port="/dev/ttyUSB0",
        image_dir=image_dir,
      )

      with self.assertRaisesRegex(MicronicError, "acquisition failed"):
        await reader._acquire_image()

      self.assertEqual(list(Path(image_dir).iterdir()), [])

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
      scanner = _mock_scanner()
      reader = MicronicCodeReader(scanner=scanner, serial_port="/dev/ttyUSB0")
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
    scanner.setup.assert_awaited_once_with()
    scanner.stop.assert_awaited_once_with()

  async def test_setup_stops_scanner_when_serial_setup_fails(self):
    scanner = _mock_scanner()
    reader = MicronicCodeReader(scanner=scanner, serial_port="/dev/ttyUSB0")

    with patch.object(reader.io, "setup", AsyncMock(side_effect=OSError("serial failed"))):
      with self.assertRaisesRegex(OSError, "serial failed"):
        await reader.setup()

    scanner.setup.assert_awaited_once_with()
    scanner.stop.assert_awaited_once_with()

  async def test_stop_releases_scanner_when_serial_stop_fails(self):
    scanner = _mock_scanner()
    reader = MicronicCodeReader(scanner=scanner, serial_port="/dev/ttyUSB0")

    with patch.object(reader.io, "stop", AsyncMock(side_effect=OSError("serial failed"))):
      with self.assertRaisesRegex(OSError, "serial failed"):
        await reader.stop()

    scanner.stop.assert_awaited_once_with()

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
      scanner.acquire.assert_awaited_once()
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
      self.assertEqual(scanner.acquire.await_count, 2)

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

  async def test_scan_rack_id_returns_noread_for_unrecognized_response(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    with (
      patch.object(reader.io, "reset_input_buffer", AsyncMock()),
      patch.object(reader.io, "write", AsyncMock()),
      patch.object(reader.io, "read", AsyncMock(side_effect=[b"?", b"\r"])),
    ):
      rack_id = await reader.scan_rack_id(timeout=1.0)

    self.assertEqual(rack_id, "NOREAD")

  async def test_scan_rack_id_wraps_serial_error(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    with patch.object(
      reader.io,
      "reset_input_buffer",
      AsyncMock(side_effect=OSError("port disconnected")),
    ):
      with self.assertRaisesRegex(MicronicError, "port disconnected"):
        await reader.scan_rack_id(timeout=1.0)

  def test_decode_rack_rejects_missing_wells(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}

    with patch(
      "pylabrobot.micronic.code_reader.driver.decode_image",
      return_value=(decoded, {"decodedWells": 1}),
    ):
      with self.assertRaisesRegex(MicronicError, "expected at least 2"):
        reader._decode_rack_image(Path("rack.bmp"), "9500017722", expected_well_count=2)

  def test_decode_rack_represents_missing_rack_id(self):
    reader = MicronicCodeReader(scanner=_mock_scanner(), serial_port="/dev/ttyUSB0")
    decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}

    with patch(
      "pylabrobot.micronic.code_reader.driver.decode_image",
      return_value=(decoded, {"decodedWells": 1}),
    ):
      result = reader._decode_rack_image(Path("rack.bmp"), "NOREAD", expected_well_count=1)

    self.assertIsNone(result.rack_barcode)
    self.assertEqual(result.entries[0].status, "OK")
    self.assertEqual(result.entries[1].status, "NOREAD")


class TestDecodeHelpers(unittest.TestCase):
  """Tests for image-decoding validation and rack-coordinate parsing."""

  def test_tube_id_validation(self):
    self.assertTrue(is_tube_id("0123456789"))
    self.assertFalse(is_tube_id("123456789"))
    self.assertFalse(is_tube_id("12345A7890"))
    self.assertFalse(is_tube_id(1234567890))

  def test_rack_position_maps_scanner_orientation(self):
    self.assertEqual(rack_position(scan_row=0, scan_col=0), "H12")
    self.assertEqual(rack_position(scan_row=11, scan_col=7), "A1")

  def test_iter_positions_is_row_major(self):
    positions = list(iter_positions())
    self.assertEqual(len(positions), 96)
    self.assertEqual(positions[:2], ["A1", "A2"])
    self.assertEqual(positions[-2:], ["H11", "H12"])

  def test_cluster_axis_uses_detected_centers(self):
    self.assertEqual(cluster_axis([0.0, 2.0, 100.0, 102.0], 2, 10.0), [1.0, 101.0])

  def test_cluster_axis_interpolates_missing_centers(self):
    self.assertEqual(cluster_axis([0.0, 50.0, 100.0], 5, 10.0), [0.0, 25.0, 50.0, 75.0, 100.0])

  def test_cluster_axis_rejects_insufficient_data(self):
    with self.assertRaisesRegex(MicronicError, "Could not fit"):
      cluster_axis([1.0, 2.0], 8, 10.0)

  def test_duplicate_ids_are_sorted_and_unique(self):
    decoded = {
      "A1": DecodeResult("2222222222", "test"),
      "A2": DecodeResult("1111111111", "test"),
      "A3": DecodeResult("2222222222", "test"),
      "A4": DecodeResult("1111111111", "test"),
      "A5": DecodeResult("1111111111", "test"),
    }
    self.assertEqual(find_duplicate_ids(decoded), ["1111111111", "2222222222"])

  def test_decode_image_rejects_too_few_full_image_codes(self):
    image = MagicMock()
    image.size = (100, 100)
    image_context = MagicMock()
    image_context.__enter__.return_value.convert.return_value = image
    image_module = MagicMock()
    image_module.open.return_value = image_context
    zxingcpp = MagicMock()
    zxingcpp.read_barcodes.return_value = []

    with patch(
      "pylabrobot.micronic.code_reader.driver.import_decode_dependencies",
      return_value=(MagicMock(), MagicMock(), zxingcpp, image_module, MagicMock()),
    ):
      with self.assertRaisesRegex(MicronicError, "Only 0 DataMatrix"):
        decode_image(Path("rack.bmp"))


if __name__ == "__main__":
  unittest.main()

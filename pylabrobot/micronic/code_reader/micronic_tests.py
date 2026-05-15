import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.micronic import MicronicCodeReader
from pylabrobot.micronic.code_reader.driver import (
  DecodeResult,
  MicronicDriver,
  MicronicError,
  MicronicRackReaderState,
  choose_image_extension,
  read_rack_id,
  read_rack_id_plr_serial,
  run_scan,
)
from pylabrobot.micronic.code_reader.rack_reading_backend import MicronicRackReadingBackend
from pylabrobot.resources.tube_rack import TubeRack


def _rack(num_items_x: int = 12, num_items_y: int = 8, num_items: int = 96) -> TubeRack:
  rack = MagicMock(spec=TubeRack)
  rack.num_items_x = num_items_x
  rack.num_items_y = num_items_y
  rack.num_items = num_items
  return rack


async def wait_for_dataready(driver: MicronicDriver) -> None:
  for _ in range(100):
    if await driver.get_rack_reader_state() == MicronicRackReaderState.DATAREADY:
      return
    await asyncio.sleep(0.01)
  raise AssertionError("Micronic test scan did not reach dataready.")


class TestMicronicDriver(unittest.IsolatedAsyncioTestCase):
  def test_driver_does_not_default_to_packaged_twain_helper(self):
    driver = MicronicDriver()
    self.assertIsNone(driver.twain_scanner_path)
    self.assertIsNone(driver.scan_command)

  async def test_driver_scan_populates_standard_rack_result(self):
    with tempfile.TemporaryDirectory() as image_dir:
      driver = MicronicDriver(
        image_dir=image_dir,
        keep_images=True,
      )
      decoded = {
        "A1": DecodeResult(tube_id="1111111111", method="test"),
        "A2": DecodeResult(tube_id="2222222222", method="test"),
      }
      with (
        patch(
          "pylabrobot.micronic.code_reader.driver.run_scan",
          return_value={"source": "test"},
        ) as run_scan_mock,
        patch(
          "pylabrobot.micronic.code_reader.driver.read_rack_id",
          return_value="9500017722",
        ) as read_rack_id_mock,
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 2}),
        ) as decode_image_mock,
      ):
        await driver.setup()
        await driver.trigger_rack_scan(_rack(num_items=2))
        await wait_for_dataready(driver)
        result = await driver.get_scan_result()

      self.assertEqual(await driver.get_rack_reader_state(), MicronicRackReaderState.DATAREADY)
      self.assertEqual(result.rack_id, "9500017722")
      self.assertEqual(result.entries[0].position, "A1")
      self.assertEqual(result.entries[0].tube_id, "1111111111")
      self.assertEqual(result.entries[1].tube_id, "2222222222")
      self.assertEqual(driver.last_scan_metadata, {"source": "test"})
      self.assertEqual(driver.last_decode_metadata, {"decodedWells": 2})
      run_scan_mock.assert_called_once()
      read_rack_id_mock.assert_called_once()
      decode_image_mock.assert_called_once()

  async def test_reader_can_scan_twice_after_dataready(self):
    with tempfile.TemporaryDirectory() as image_dir:
      reader = MicronicCodeReader(
        driver=MicronicDriver(
          image_dir=image_dir,
          keep_images=True,
        )
      )
      decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}
      with (
        patch(
          "pylabrobot.micronic.code_reader.driver.run_scan",
          return_value={"source": "test"},
        ) as run_scan_mock,
        patch(
          "pylabrobot.micronic.code_reader.driver.read_rack_id",
          return_value="9500017722",
        ),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await reader.setup()
        first = await reader.rack_reading.scan_rack(
          rack=_rack(num_items=1), timeout=1.0, poll_interval=0.01
        )
        second = await reader.rack_reading.scan_rack(
          rack=_rack(num_items=1), timeout=1.0, poll_interval=0.01
        )

      self.assertEqual(first.rack_id, "9500017722")
      self.assertEqual(second.rack_id, "9500017722")
      self.assertEqual(run_scan_mock.call_count, 2)

  async def test_driver_get_rack_id_does_not_return_stale_result_while_scanning(self):
    with tempfile.TemporaryDirectory() as image_dir:
      driver = MicronicDriver(
        image_dir=image_dir,
        keep_images=True,
      )
      decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}

      def slow_scan(*args, **kwargs):
        del args, kwargs
        import time

        time.sleep(0.05)
        return {"source": "test"}

      with (
        patch(
          "pylabrobot.micronic.code_reader.driver.run_scan",
          return_value={"source": "test"},
        ),
        patch(
          "pylabrobot.micronic.code_reader.driver.read_rack_id",
          return_value="9500017722",
        ),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await driver.setup()
        await driver.trigger_rack_scan(_rack(num_items=1))
        await wait_for_dataready(driver)
        self.assertEqual(await driver.get_rack_id(), "9500017722")

      with (
        patch(
          "pylabrobot.micronic.code_reader.driver.run_scan",
          side_effect=slow_scan,
        ),
        patch(
          "pylabrobot.micronic.code_reader.driver.read_rack_id",
          return_value="9500017723",
        ),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await driver.trigger_rack_scan(_rack(num_items=1))
        with self.assertRaises(MicronicError):
          await driver.get_rack_id()
        await wait_for_dataready(driver)
        self.assertEqual(await driver.get_rack_id(), "9500017723")

  async def test_run_scan_uses_explicit_command(self):
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "rack.bmp"
      metadata = run_scan(
        output_path=output_path,
        timeout_ms=1000,
        scan_command=[
          sys.executable,
          "-c",
          "from pathlib import Path; Path(r'{output_path}').write_bytes(b'image')",
        ],
      )

      self.assertEqual(metadata["source"], "command")
      self.assertTrue(output_path.exists())

  async def test_run_scan_uses_sane_scanimage_when_requested(self):
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "micronic-test.tiff"
      with (
        patch(
          "pylabrobot.micronic.code_reader.driver.shutil.which",
          return_value="/usr/bin/scanimage",
        ),
        patch(
          "pylabrobot.micronic.code_reader.driver.run_scan_command",
          return_value={"source": "sane"},
        ) as run_scan_command,
      ):
        metadata = run_scan(
          output_path=output_path,
          timeout_ms=1000,
          scanner_backend="sane",
          sane_device="avision:libusb:001:004",
        )

      self.assertEqual(metadata["source"], "sane")
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

  async def test_run_scan_requires_configured_acquisition(self):
    with tempfile.TemporaryDirectory() as image_dir:
      with (
        patch("pylabrobot.micronic.code_reader.driver.shutil.which", return_value=None),
        self.assertRaises(MicronicError),
      ):
        run_scan(
          output_path=Path(image_dir) / "micronic-test.bmp",
          timeout_ms=1000,
          scanner_backend="twain",
        )

  async def test_choose_image_extension_prefers_sane_tiff_on_non_windows_auto(self):
    extension = choose_image_extension(
      image_extension=None,
      image_input=None,
      scanner_backend="auto",
      scan_command=None,
      twain_scanner_path=None,
      sane_device="avision:libusb:001:004",
    )
    self.assertEqual(extension, "tiff")

  async def test_read_rack_id_uses_configured_command(self):
    rack_id = read_rack_id(
      timeout_ms=1000,
      rack_id_command=[sys.executable, "-c", "print('rack 9500017722')"],
    )
    self.assertEqual(rack_id, "9500017722")

  async def test_read_rack_id_uses_plr_serial(self):
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
      rack_id = await read_rack_id_plr_serial(serial_port="COM4", timeout_ms=1000)

    self.assertEqual(len(instances), 1)
    fake_serial = cast(FakeSerial, instances[0])
    self.assertEqual(rack_id, "9500017722")
    self.assertEqual(fake_serial.kwargs["port"], "COM4")
    self.assertEqual(fake_serial.kwargs["bytesize"], 7)
    self.assertEqual(fake_serial.kwargs["parity"], "E")
    self.assertIn("reset_input_buffer", fake_serial.calls)
    self.assertIn("write:b'<t>\\r\\n'", fake_serial.calls)
    self.assertEqual(fake_serial.calls[-1], "stop")

  async def test_driver_scan_rack_id_uses_configured_command(self):
    driver = MicronicDriver(rack_id_command=[sys.executable, "-c", "print('rack 9500017722')"])
    self.assertEqual(await driver.scan_rack_id(timeout=1.0, poll_interval=0.1), "9500017722")

  async def test_driver_raises_when_scan_result_is_not_ready(self):
    driver = MicronicDriver()
    with self.assertRaises(MicronicError):
      await driver.get_scan_result()

  async def test_driver_rejects_mismatched_rack_shape(self):
    driver = MicronicDriver()
    with self.assertRaises(MicronicError):
      await driver.trigger_rack_scan(_rack(num_items_x=6, num_items_y=4))


class TestMicronicRackReadingBackend(unittest.IsolatedAsyncioTestCase):
  def _backend_with_mocked_driver(self) -> tuple[MicronicRackReadingBackend, MagicMock]:
    driver = MagicMock(spec=MicronicDriver)
    driver.trigger_rack_scan = AsyncMock()
    driver.get_rack_reader_state = AsyncMock()
    driver.get_scan_result = AsyncMock()
    driver.scan_rack_id = AsyncMock()
    return MicronicRackReadingBackend(driver=driver), driver

  async def test_scan_rack_triggers_polls_and_returns_result(self):
    backend, driver = self._backend_with_mocked_driver()
    driver.get_rack_reader_state.side_effect = [
      MicronicRackReaderState.IDLE,
      MicronicRackReaderState.DATAREADY,
    ]
    expected = MagicMock()
    driver.get_scan_result.return_value = expected
    rack = _rack()

    result = await backend.scan_rack(rack=rack, timeout=1.0, poll_interval=0.0)

    self.assertIs(result, expected)
    driver.trigger_rack_scan.assert_awaited_once_with(rack)
    self.assertEqual(driver.get_rack_reader_state.await_count, 2)
    driver.get_scan_result.assert_awaited_once()

  async def test_scan_rack_waits_for_fresh_dataready(self):
    backend, driver = self._backend_with_mocked_driver()
    driver.get_rack_reader_state.side_effect = [
      MicronicRackReaderState.DATAREADY,
      MicronicRackReaderState.SCANNING,
      MicronicRackReaderState.DATAREADY,
    ]
    driver.get_scan_result.return_value = MagicMock()

    await backend.scan_rack(rack=_rack(), timeout=1.0, poll_interval=0.0)

    self.assertEqual(driver.get_rack_reader_state.await_count, 3)
    driver.get_scan_result.assert_awaited_once()

  async def test_scan_rack_times_out_with_standard_timeout_error(self):
    backend, driver = self._backend_with_mocked_driver()
    driver.get_rack_reader_state.return_value = MicronicRackReaderState.SCANNING

    with self.assertRaises(TimeoutError):
      await backend.scan_rack(rack=_rack(), timeout=0.01, poll_interval=0.0)

  async def test_scan_rack_propagates_driver_micronic_error(self):
    backend, driver = self._backend_with_mocked_driver()
    driver.get_rack_reader_state.return_value = MicronicRackReaderState.IDLE
    driver.trigger_rack_scan.side_effect = MicronicError("rack shape mismatch")

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
      timeout=12.0,
      poll_interval=0.25,
      driver=MicronicDriver(rack_id_override="9500017722"),
    )
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
    reader = MicronicCodeReader(rack_id_override="9500017722")
    self.assertIsInstance(reader.driver, MicronicDriver)
    self.assertFalse(hasattr(reader, "barcode_scanning"))


if __name__ == "__main__":
  unittest.main()

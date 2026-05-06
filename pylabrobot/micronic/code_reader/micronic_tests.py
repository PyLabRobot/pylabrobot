import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pylabrobot.capabilities.rack_reading import RackReaderState
from pylabrobot.micronic import MicronicCodeReader, MicronicDirectCodeReader
from pylabrobot.micronic.code_reader.direct_driver import (
  DecodeResult,
  MicronicDirectDriver,
  MicronicDirectRackReaderError,
  choose_image_extension,
  read_rack_id,
  run_scan,
)
from pylabrobot.micronic.code_reader.rack_reading_backend import MicronicRackReadingBackend


async def wait_for_direct_dataready(driver: MicronicDirectDriver) -> None:
  for _ in range(100):
    if await driver.get_rack_reader_state() == RackReaderState.DATAREADY:
      return
    await asyncio.sleep(0.01)
  raise AssertionError("Direct Micronic test scan did not reach dataready.")


class TestMicronicDirectDriver(unittest.IsolatedAsyncioTestCase):
  def test_direct_driver_does_not_default_to_packaged_twain_helper(self):
    driver = MicronicDirectDriver()
    self.assertIsNone(driver.twain_scanner_path)
    self.assertIsNone(driver.scan_command)

  async def test_direct_driver_scan_populates_standard_rack_result(self):
    with tempfile.TemporaryDirectory() as image_dir:
      driver = MicronicDirectDriver(
        image_dir=image_dir,
        min_wells=2,
        keep_images=True,
      )
      decoded = {
        "A01": DecodeResult(tube_id="1111111111", method="test"),
        "A02": DecodeResult(tube_id="2222222222", method="test"),
      }
      with (
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.run_scan",
          return_value={"source": "test"},
        ) as run_scan_mock,
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.read_rack_id",
          return_value="9500017722",
        ) as read_rack_id_mock,
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.decode_image",
          return_value=(decoded, {"decodedWells": 2}),
        ) as decode_image_mock,
      ):
        await driver.setup()
        await driver.trigger_rack_scan()
        await wait_for_direct_dataready(driver)
        result = await driver.get_scan_result()

      self.assertEqual(await driver.get_rack_reader_state(), RackReaderState.DATAREADY)
      self.assertEqual(result.rack_id, "9500017722")
      self.assertEqual(result.entries[0].position, "A01")
      self.assertEqual(result.entries[0].tube_id, "1111111111")
      self.assertEqual(result.entries[1].tube_id, "2222222222")
      self.assertEqual(driver.last_scan_metadata, {"source": "test"})
      self.assertEqual(driver.last_decode_metadata, {"decodedWells": 2})
      run_scan_mock.assert_called_once()
      read_rack_id_mock.assert_called_once()
      decode_image_mock.assert_called_once()

  async def test_direct_reader_can_scan_twice_after_dataready(self):
    with tempfile.TemporaryDirectory() as image_dir:
      reader = MicronicCodeReader(
        driver=MicronicDirectDriver(
          image_dir=image_dir,
          min_wells=1,
          keep_images=True,
        )
      )
      decoded = {"A01": DecodeResult(tube_id="1111111111", method="test")}
      with (
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.run_scan",
          return_value={"source": "test"},
        ) as run_scan_mock,
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.read_rack_id",
          return_value="9500017722",
        ),
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await reader.setup()
        first = await reader.rack_reading.scan_rack(timeout=1.0, poll_interval=0.01)
        second = await reader.rack_reading.scan_rack(timeout=1.0, poll_interval=0.01)

      self.assertEqual(first.rack_id, "9500017722")
      self.assertEqual(second.rack_id, "9500017722")
      self.assertEqual(run_scan_mock.call_count, 2)

  async def test_direct_driver_get_rack_id_does_not_return_stale_result_while_scanning(self):
    with tempfile.TemporaryDirectory() as image_dir:
      driver = MicronicDirectDriver(
        image_dir=image_dir,
        min_wells=1,
        keep_images=True,
      )
      decoded = {"A01": DecodeResult(tube_id="1111111111", method="test")}

      def slow_scan(*args, **kwargs):
        del args, kwargs
        import time

        time.sleep(0.05)
        return {"source": "test"}

      with (
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.run_scan",
          return_value={"source": "test"},
        ),
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.read_rack_id",
          return_value="9500017722",
        ),
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await driver.setup()
        await driver.trigger_rack_scan()
        await wait_for_direct_dataready(driver)
        self.assertEqual(await driver.get_rack_id(), "9500017722")

      with (
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.run_scan",
          side_effect=slow_scan,
        ),
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.read_rack_id",
          return_value="9500017723",
        ),
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        await driver.trigger_rack_scan()
        with self.assertRaises(MicronicDirectRackReaderError):
          await driver.get_rack_id()
        await wait_for_direct_dataready(driver)
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
          "pylabrobot.micronic.code_reader.direct_driver.shutil.which",
          return_value="/usr/bin/scanimage",
        ),
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.run_scan_command",
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
        patch("pylabrobot.micronic.code_reader.direct_driver.shutil.which", return_value=None),
        self.assertRaises(MicronicDirectRackReaderError),
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

  async def test_direct_driver_scan_rack_id_uses_configured_command(self):
    driver = MicronicDirectDriver(
      rack_id_command=[sys.executable, "-c", "print('rack 9500017722')"]
    )
    self.assertEqual(await driver.scan_rack_id(timeout=1.0, poll_interval=0.1), "9500017722")

  async def test_direct_driver_raises_when_scan_result_is_not_ready(self):
    driver = MicronicDirectDriver()
    with self.assertRaises(MicronicDirectRackReaderError):
      await driver.get_scan_result()

  async def test_direct_driver_rejects_unknown_layout(self):
    driver = MicronicDirectDriver()
    with self.assertRaises(MicronicDirectRackReaderError):
      await driver.set_current_layout("384")

  async def test_generic_backend_delegates_to_direct_driver(self):
    driver = MicronicDirectDriver(rack_id_override="9500017722")
    backend = MicronicRackReadingBackend(driver=driver)
    with patch.object(driver, "scan_rack_id", return_value="9500017722") as scan_rack_id:
      rack_id = await backend.scan_rack_id(timeout=5.0, poll_interval=0.5)
    self.assertEqual(rack_id, "9500017722")
    scan_rack_id.assert_called_once_with(timeout=5.0, poll_interval=0.5)


class TestMicronicCodeReader(unittest.IsolatedAsyncioTestCase):
  async def test_device_exposes_rack_reading_only(self):
    reader = MicronicCodeReader(
      timeout=12.0,
      poll_interval=0.25,
      driver=MicronicDirectDriver(rack_id_override="9500017722"),
    )
    with patch.object(
      reader.driver,
      "get_rack_reader_state",
      return_value=RackReaderState.IDLE,
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
          timeout=reader.default_timeout,
          poll_interval=reader.default_poll_interval,
        )
    finally:
      await reader.stop()

    self.assertEqual(result.rack_id, "9500017722")
    scan_rack.assert_called_once_with(timeout=12.0, poll_interval=0.25)

  async def test_direct_frontend_uses_direct_driver(self):
    reader = MicronicDirectCodeReader(rack_id_override="9500017722")
    self.assertIsInstance(reader.driver, MicronicDirectDriver)
    self.assertFalse(hasattr(reader, "barcode_scanning"))


if __name__ == "__main__":
  unittest.main()

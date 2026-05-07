import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from pylabrobot.capabilities.rack_reading import RackReaderState
from pylabrobot.micronic import MicronicCodeReader
from pylabrobot.micronic.code_reader.driver import (
  DecodeResult,
  MicronicDriver,
  MicronicError,
  choose_image_extension,
  read_rack_id,
  read_rack_id_plr_serial,
  run_scan,
)
from pylabrobot.micronic.code_reader.rack_reading_backend import MicronicRackReadingBackend


async def wait_for_dataready(driver: MicronicDriver) -> None:
  for _ in range(100):
    if await driver.get_rack_reader_state() == RackReaderState.DATAREADY:
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
        min_wells=2,
        keep_images=True,
      )
      decoded = {
        "A01": DecodeResult(tube_id="1111111111", method="test"),
        "A02": DecodeResult(tube_id="2222222222", method="test"),
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
        await driver.trigger_rack_scan()
        await wait_for_dataready(driver)
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

  async def test_reader_can_scan_twice_after_dataready(self):
    with tempfile.TemporaryDirectory() as image_dir:
      reader = MicronicCodeReader(
        driver=MicronicDriver(
          image_dir=image_dir,
          min_wells=1,
          keep_images=True,
        )
      )
      decoded = {"A01": DecodeResult(tube_id="1111111111", method="test")}
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
        first = await reader.rack_reading.scan_rack(timeout=1.0, poll_interval=0.01)
        second = await reader.rack_reading.scan_rack(timeout=1.0, poll_interval=0.01)

      self.assertEqual(first.rack_id, "9500017722")
      self.assertEqual(second.rack_id, "9500017722")
      self.assertEqual(run_scan_mock.call_count, 2)

  async def test_driver_get_rack_id_does_not_return_stale_result_while_scanning(self):
    with tempfile.TemporaryDirectory() as image_dir:
      driver = MicronicDriver(
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
        await driver.trigger_rack_scan()
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
        await driver.trigger_rack_scan()
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

  async def test_driver_rejects_unknown_layout(self):
    driver = MicronicDriver()
    with self.assertRaises(MicronicError):
      await driver.set_current_layout("384")

  async def test_backend_delegates_to_driver(self):
    driver = MicronicDriver(rack_id_override="9500017722")
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
      driver=MicronicDriver(rack_id_override="9500017722"),
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

  async def test_frontend_uses_driver(self):
    reader = MicronicCodeReader(rack_id_override="9500017722")
    self.assertIsInstance(reader.driver, MicronicDriver)
    self.assertFalse(hasattr(reader, "barcode_scanning"))


if __name__ == "__main__":
  unittest.main()

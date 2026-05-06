import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib import error

from pylabrobot.capabilities.barcode_scanning.backend import BarcodeScannerError
from pylabrobot.capabilities.rack_reading import RackReaderState
from pylabrobot.micronic import MicronicCodeReader, MicronicDirectCodeReader
from pylabrobot.micronic.code_reader.barcode_scanning_backend import (
  MicronicBarcodeScannerError,
  MicronicIOMonitorBarcodeScannerBackend,
)
from pylabrobot.micronic.code_reader.direct_driver import (
  DecodeResult,
  MicronicDirectDriver,
  MicronicDirectRackReaderError,
  choose_image_extension,
  powershell_single_quote,
  read_rack_id,
  run_scan,
)
from pylabrobot.micronic.code_reader.driver import (
  MicronicError,
  MicronicIOMonitorDriver,
  MicronicIOMonitorState,
)
from pylabrobot.micronic.code_reader.rack_reading_backend import (
  MicronicIOMonitorRackReadingBackend,
  MicronicRackReadingBackend,
)
from pylabrobot.resources.barcode import Barcode


async def wait_for_direct_dataready(driver: MicronicDirectDriver) -> None:
  for _ in range(100):
    if await driver.get_rack_reader_state() == RackReaderState.DATAREADY:
      return
    await asyncio.sleep(0.01)
  raise AssertionError("Direct Micronic test scan did not reach dataready.")


class TestMicronicIOMonitorDriver(unittest.IsolatedAsyncioTestCase):
  async def test_request_sync_retries_connection_reset(self):
    driver = MicronicIOMonitorDriver()
    response = MagicMock()
    response.read.return_value = b'{"state":"idle"}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
      "pylabrobot.micronic.code_reader.driver.request.urlopen",
      side_effect=[ConnectionResetError(104, "reset"), response],
    ):
      body = driver._request_sync("GET", "/state")

    self.assertEqual(body, b'{"state":"idle"}')

  async def test_http_error_maps_to_backend_error(self):
    driver = MicronicIOMonitorDriver()
    err = driver._as_micronic_error(
      json.dumps({"ErrorCode": 4, "ErrorMsg": "invalid state"}).encode("utf-8"),
      fallback="fallback",
    )
    self.assertIsInstance(err, MicronicError)
    self.assertIn("invalid state", str(err))

  async def test_request_sync_retries_retryable_urlerror(self):
    driver = MicronicIOMonitorDriver()
    response = MagicMock()
    response.read.return_value = b'{"state":"idle"}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
      "pylabrobot.micronic.code_reader.driver.request.urlopen",
      side_effect=[error.URLError(ConnectionResetError(104, "reset")), response],
    ):
      body = driver._request_sync("GET", "/state")

    self.assertEqual(body, b'{"state":"idle"}')

  async def test_get_iomonitor_state_parses_payload(self):
    driver = MicronicIOMonitorDriver()
    with patch.object(driver, "request_json", return_value={"state": "dataready"}):
      state = await driver.get_iomonitor_state()
    self.assertEqual(state, MicronicIOMonitorState.DATAREADY)

  async def test_get_iomonitor_state_rejects_unknown(self):
    driver = MicronicIOMonitorDriver()
    with patch.object(driver, "request_json", return_value={"state": "weird"}):
      with self.assertRaises(MicronicError):
        await driver.get_iomonitor_state()

  async def test_wait_for_fresh_data_ready_requires_state_change_when_starting_ready(self):
    driver = MicronicIOMonitorDriver()
    with patch.object(
      driver,
      "request_json",
      side_effect=[
        {"state": "dataready"},
        {"state": "scanning"},
        {"state": "dataready"},
      ],
    ) as request_json:
      await driver.wait_for_fresh_data_ready(
        initial_state=MicronicIOMonitorState.DATAREADY,
        timeout=1.0,
        poll_interval=0.0,
      )
    self.assertEqual(request_json.call_count, 3)

  async def test_get_single_tube_barcode_prefers_scanresult(self):
    driver = MicronicIOMonitorDriver()
    with patch.object(
      driver,
      "request_json",
      side_effect=[{"TubeID": ["5007377910"]}],
    ):
      barcode = await driver.get_single_tube_barcode()
    self.assertEqual(barcode, "5007377910")

  async def test_get_single_tube_barcode_falls_back_to_rackid(self):
    driver = MicronicIOMonitorDriver()
    with patch.object(
      driver,
      "request_json",
      side_effect=[{"unexpected": "shape"}, {"RackID": "5007377910"}],
    ):
      barcode = await driver.get_single_tube_barcode()
    self.assertEqual(barcode, "5007377910")


class TestMicronicIOMonitorRackReadingBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    super().setUp()
    self.driver = MicronicIOMonitorDriver()
    self.backend = MicronicIOMonitorRackReadingBackend(driver=self.driver)

  async def test_on_setup_checks_state(self):
    with patch.object(self.backend, "get_state", return_value=RackReaderState.IDLE) as get_state:
      await self.backend._on_setup()
    get_state.assert_called_once_with()

  async def test_get_state(self):
    with patch.object(
      self.driver,
      "request_json",
      return_value={"state": "dataready"},
    ):
      state = await self.backend.get_state()
    self.assertEqual(state, RackReaderState.DATAREADY)

  async def test_trigger_rack_scan(self):
    with patch.object(self.driver, "request", return_value=b"") as request_bytes:
      await self.backend.trigger_rack_scan()
    request_bytes.assert_called_once_with(
      "POST",
      "/scanbox",
      data=b"",
      headers=None,
      expect_json=False,
    )

  async def test_scan_rack_id_uses_rackid_endpoint(self):
    with patch.object(
      self.driver,
      "request_json",
      return_value={"RackID": "5500135415"},
    ) as request_json:
      rack_id = await self.backend.scan_rack_id(timeout=10.0, poll_interval=0.5)

    request_json.assert_called_once_with("GET", "/rackid", data=None, headers=None)
    self.assertEqual(rack_id, "5500135415")

  async def test_get_scan_result(self):
    payload = {
      "RackID": "3000756455",
      "Date": "20260315",
      "Time": "114804",
      "Position": ["A01", "A02"],
      "TubeID": ["5007377910", "5007377911"],
      "Status": ["Code OK", "Code OK"],
      "FreeText": ["", ""],
    }
    with patch.object(self.driver, "request_json", return_value=payload):
      result = await self.backend.get_scan_result()

    self.assertEqual(result.rack_id, "3000756455")
    self.assertEqual(result.entries[0].position, "A01")
    self.assertEqual(result.entries[1].tube_id, "5007377911")

  async def test_get_layouts_dict_payload(self):
    with patch.object(self.driver, "request_json", return_value={"Layout": ["8x12", "6x8"]}):
      layouts = await self.backend.get_layouts()
    self.assertEqual([layout.name for layout in layouts], ["8x12", "6x8"])

  async def test_set_current_layout(self):
    with patch.object(self.driver, "request", return_value=b"") as request_bytes:
      await self.backend.set_current_layout("96")
    request_bytes.assert_called_once_with(
      "PUT",
      "/currentlayout",
      data=b'{"Layout": "96"}',
      headers={"Content-Type": "application/json; charset=utf-8"},
      expect_json=False,
    )


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
        ) as run_scan,
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.read_rack_id",
          return_value="9500017722",
        ) as read_rack_id,
        patch(
          "pylabrobot.micronic.code_reader.direct_driver.decode_image",
          return_value=(decoded, {"decodedWells": 2}),
        ) as decode_image,
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
      run_scan.assert_called_once()
      read_rack_id.assert_called_once()
      decode_image.assert_called_once()

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
        ) as run_scan,
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
      self.assertEqual(run_scan.call_count, 2)

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

  def test_powershell_single_quote_escapes_serial_port(self):
    self.assertEqual(powershell_single_quote("COM4"), "'COM4'")
    self.assertEqual(powershell_single_quote("COM'4"), "'COM''4'")

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


class TestMicronicIOMonitorBarcodeScannerBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    super().setUp()
    self.driver = MicronicIOMonitorDriver()
    self.backend = MicronicIOMonitorBarcodeScannerBackend(
      driver=self.driver, timeout=1.0, poll_interval=0.0
    )

  async def test_scan_barcode_reads_single_tube_code(self):
    with (
      patch.object(self.driver, "request", return_value=b"") as request_bytes,
      patch.object(
        self.driver,
        "request_json",
        side_effect=[
          {"state": "idle"},
          {"state": "scanning"},
          {"state": "dataready"},
          {"TubeID": ["5007377910"]},
        ],
      ),
    ):
      barcode = await self.backend.scan_barcode()

    request_bytes.assert_called_once_with(
      "POST",
      "/scantube",
      data=b"",
      headers=None,
      expect_json=False,
    )
    self.assertEqual(barcode, Barcode("5007377910", "Data Matrix", "bottom"))

  async def test_scan_barcode_falls_back_to_rackid_payload(self):
    with (
      patch.object(self.driver, "request", return_value=b"") as request_bytes,
      patch.object(
        self.driver,
        "request_json",
        side_effect=[
          {"state": "idle"},
          {"state": "dataready"},
          {"unexpected": "shape"},
          {"RackID": "5007377910"},
        ],
      ),
    ):
      barcode = await self.backend.scan_barcode()

    request_bytes.assert_called_once_with(
      "POST",
      "/scantube",
      data=b"",
      headers=None,
      expect_json=False,
    )
    self.assertEqual(barcode.data, "5007377910")

  async def test_scan_barcode_waits_for_new_dataready_cycle(self):
    with (
      patch.object(self.driver, "request", return_value=b"") as request_bytes,
      patch.object(
        self.driver,
        "request_json",
        side_effect=[
          {"state": "dataready"},
          {"state": "dataready"},
          {"state": "scanning"},
          {"state": "dataready"},
          {"TubeID": ["5007377910"]},
        ],
      ) as request_json,
    ):
      barcode = await self.backend.scan_barcode()

    request_bytes.assert_called_once_with(
      "POST",
      "/scantube",
      data=b"",
      headers=None,
      expect_json=False,
    )
    self.assertEqual(barcode.data, "5007377910")
    self.assertEqual(request_json.call_count, 5)

  async def test_scan_barcode_raises_on_unknown_payload(self):
    with (
      patch.object(self.driver, "request", return_value=b""),
      patch.object(
        self.driver,
        "request_json",
        side_effect=[
          {"state": "idle"},
          {"state": "dataready"},
          {"unexpected": "shape"},
          {"still": "bad"},
        ],
      ),
    ):
      with self.assertRaises(MicronicBarcodeScannerError):
        await self.backend.scan_barcode()

  async def test_backend_error_is_a_barcode_scanner_error(self):
    with patch.object(
      self.driver,
      "request_json",
      side_effect=MicronicError("network failure"),
    ):
      with self.assertRaises(BarcodeScannerError):
        await self.backend.scan_barcode()


class TestMicronicCodeReader(unittest.IsolatedAsyncioTestCase):
  async def test_device_exposes_rack_and_barcode_capabilities(self):
    reader = MicronicCodeReader(timeout=12.0, poll_interval=0.25)
    with patch.object(
      reader.driver,
      "get_iomonitor_state",
      return_value=MicronicIOMonitorState.IDLE,
    ):
      await reader.setup()
    try:
      self.assertIn(reader.rack_reading, reader._capabilities)
      self.assertIn(reader.barcode_scanning, reader._capabilities)
      self.assertFalse(hasattr(reader, "rack_reader"))
      with patch.object(
        reader.rack_reading,
        "scan_rack",
        return_value=MagicMock(rack_id="5500135415"),
      ) as scan_rack:
        result = await reader.rack_reading.scan_rack(
          timeout=reader.default_timeout,
          poll_interval=reader.default_poll_interval,
        )
      with patch.object(
        reader.barcode_scanning,
        "scan",
        return_value=Barcode(
          data="5007377910", symbology="Data Matrix", position_on_resource="bottom"
        ),
      ) as scan_barcode:
        barcode = await reader.barcode_scanning.scan()
    finally:
      await reader.stop()

    self.assertEqual(result.rack_id, "5500135415")
    self.assertEqual(barcode.data, "5007377910")
    scan_rack.assert_called_once_with(timeout=12.0, poll_interval=0.25)
    scan_barcode.assert_called_once_with()

  async def test_device_exposes_rack_id_only_scan_on_rack_reading(self):
    reader = MicronicCodeReader(timeout=12.0, poll_interval=0.25)
    with patch.object(
      reader.driver,
      "get_iomonitor_state",
      return_value=MicronicIOMonitorState.IDLE,
    ):
      await reader.setup()
    try:
      with patch.object(
        reader.rack_reading,
        "scan_rack_id",
        return_value="5500135415",
      ) as scan_rack_id:
        rack_id = await reader.rack_reading.scan_rack_id(
          timeout=reader.default_timeout,
          poll_interval=reader.default_poll_interval,
        )
    finally:
      await reader.stop()

    self.assertEqual(rack_id, "5500135415")
    scan_rack_id.assert_called_once_with(timeout=12.0, poll_interval=0.25)

  async def test_device_accepts_direct_driver_without_barcode_capability(self):
    with tempfile.TemporaryDirectory() as image_dir:
      reader = MicronicCodeReader(
        timeout=12.0,
        poll_interval=0.25,
        driver=MicronicDirectDriver(image_dir=image_dir),
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
      finally:
        await reader.stop()

  async def test_direct_frontend_uses_direct_driver(self):
    reader = MicronicDirectCodeReader(rack_id_override="9500017722")
    self.assertIsInstance(reader.driver, MicronicDirectDriver)
    self.assertFalse(hasattr(reader, "barcode_scanning"))


if __name__ == "__main__":
  unittest.main()

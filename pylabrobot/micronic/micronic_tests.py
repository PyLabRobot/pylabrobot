import json
import unittest
from urllib import error
from unittest.mock import MagicMock, patch

from pylabrobot.capabilities.barcode_scanning.backend import BarcodeScannerError
from pylabrobot.capabilities.rack_reading import RackReaderState
from pylabrobot.resources.barcode import Barcode
from pylabrobot.micronic import MicronicCodeReader
from pylabrobot.micronic.barcode_scanning_backend import (
  MicronicBarcodeScannerBackend,
  MicronicBarcodeScannerError,
)
from pylabrobot.micronic.http_driver import MicronicError, MicronicHTTPDriver
from pylabrobot.micronic.rack_reading_backend import MicronicRackReadingBackend


class TestMicronicHTTPDriver(unittest.IsolatedAsyncioTestCase):
  async def test_request_sync_retries_connection_reset(self):
    driver = MicronicHTTPDriver()
    response = MagicMock()
    response.read.return_value = b'{"state":"idle"}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
      "pylabrobot.micronic.http_driver.request.urlopen",
      side_effect=[ConnectionResetError(104, "reset"), response],
    ):
      body = driver._request_sync("GET", "/state")

    self.assertEqual(body, b'{"state":"idle"}')

  async def test_http_error_maps_to_backend_error(self):
    driver = MicronicHTTPDriver()
    err = driver._as_micronic_error(
      json.dumps({"ErrorCode": 4, "ErrorMsg": "invalid state"}).encode("utf-8"),
      fallback="fallback",
    )
    self.assertIsInstance(err, MicronicError)
    self.assertIn("invalid state", str(err))

  async def test_request_sync_retries_retryable_urlerror(self):
    driver = MicronicHTTPDriver()
    response = MagicMock()
    response.read.return_value = b'{"state":"idle"}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
      "pylabrobot.micronic.http_driver.request.urlopen",
      side_effect=[error.URLError(ConnectionResetError(104, "reset")), response],
    ):
      body = driver._request_sync("GET", "/state")

    self.assertEqual(body, b'{"state":"idle"}')


class TestMicronicRackReadingBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    super().setUp()
    self.driver = MicronicHTTPDriver()
    self.backend = MicronicRackReadingBackend(driver=self.driver)

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


class TestMicronicBarcodeScannerBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    super().setUp()
    self.driver = MicronicHTTPDriver()
    self.backend = MicronicBarcodeScannerBackend(driver=self.driver, timeout=1.0, poll_interval=0.0)

  async def test_scan_barcode_reads_single_tube_code(self):
    with patch.object(self.driver, "request", return_value=b"") as request_bytes, patch.object(
      self.driver,
      "request_json",
      side_effect=[
        {"state": "idle"},
        {"state": "scanning"},
        {"state": "dataready"},
        {"TubeID": ["5007377910"]},
      ],
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
    with patch.object(self.driver, "request", return_value=b"") as request_bytes, patch.object(
      self.driver,
      "request_json",
      side_effect=[
        {"state": "idle"},
        {"state": "dataready"},
        {"unexpected": "shape"},
        {"RackID": "5007377910"},
      ],
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
    with patch.object(self.driver, "request", return_value=b"") as request_bytes, patch.object(
      self.driver,
      "request_json",
      side_effect=[
        {"state": "dataready"},
        {"state": "dataready"},
        {"state": "scanning"},
        {"state": "dataready"},
        {"TubeID": ["5007377910"]},
      ],
    ) as request_json:
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
    with patch.object(self.driver, "request", return_value=b""), patch.object(
      self.driver,
      "request_json",
      side_effect=[
        {"state": "idle"},
        {"state": "dataready"},
        {"unexpected": "shape"},
        {"still": "bad"},
      ],
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
      reader.rack_reading.backend,
      "get_state",
      return_value=RackReaderState.IDLE,
    ), patch.object(
      reader.barcode_scanning.backend,
      "_get_state",
      return_value=RackReaderState.IDLE,
    ):
      await reader.setup()
    try:
      self.assertIs(reader.rack_reader, reader.rack_reading)
      self.assertIn(reader.barcode_scanning, reader._capabilities)
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
        return_value=Barcode(data="5007377910", symbology="Data Matrix", position_on_resource="bottom"),
      ) as scan_barcode:
        barcode = await reader.barcode_scanning.scan()
    finally:
      await reader.stop()

    self.assertEqual(result.rack_id, "5500135415")
    self.assertEqual(barcode.data, "5007377910")
    scan_rack.assert_called_once_with(timeout=12.0, poll_interval=0.25)
    scan_barcode.assert_called_once_with()


if __name__ == "__main__":
  unittest.main()

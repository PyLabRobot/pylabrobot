import json
import unittest
from unittest.mock import MagicMock, patch

from pylabrobot.capabilities.rack_reading import RackReaderState
from pylabrobot.micronic import MicronicCodeReader
from pylabrobot.micronic.http_driver import MicronicHTTPDriver, MicronicRackReaderError
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
    self.assertIsInstance(err, MicronicRackReaderError)
    self.assertIn("invalid state", str(err))


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
    request_bytes.assert_called_once_with("POST", "/scanbox", data=b"", expect_json=False)

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


class TestMicronicCodeReader(unittest.IsolatedAsyncioTestCase):
  async def test_device_exposes_rack_reading_capability(self):
    reader = MicronicCodeReader(timeout=12.0, poll_interval=0.25)
    with patch.object(
      reader.rack_reading.backend,
      "get_state",
      return_value=RackReaderState.IDLE,
    ):
      await reader.setup()
    try:
      self.assertIs(reader.rack_reader, reader.rack_reading)
      with patch.object(
        reader.rack_reading,
        "scan_rack",
        return_value=MagicMock(rack_id="5500135415"),
      ) as scan_rack:
        result = await reader.rack_reading.scan_rack(
          timeout=reader.default_timeout,
          poll_interval=reader.default_poll_interval,
        )
    finally:
      await reader.stop()

    self.assertEqual(result.rack_id, "5500135415")
    scan_rack.assert_called_once_with(timeout=12.0, poll_interval=0.25)


if __name__ == "__main__":
  unittest.main()

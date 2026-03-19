import json
import unittest
from unittest.mock import MagicMock, patch

from pylabrobot.rack_reading.micronic.http_backend import (
  MicronicHTTPBackend,
  MicronicRackReaderError,
)
from pylabrobot.rack_reading.standard import RackReaderState


class TestMicronicHTTPBackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    super().setUp()
    self.backend = MicronicHTTPBackend()

  async def test_setup_checks_state(self):
    with patch.object(
      self.backend,
      "_request_json",
      return_value={"state": "idle"},
    ) as request_json:
      await self.backend.setup()

    request_json.assert_called_once_with("GET", "/state")

  async def test_get_state(self):
    with patch.object(
      self.backend,
      "_request_json",
      return_value={"state": "dataready"},
    ):
      state = await self.backend.get_state()

    self.assertEqual(state, RackReaderState.DATAREADY)

  async def test_scan_box(self):
    with patch.object(self.backend, "_request", return_value=b"") as request_bytes:
      await self.backend.scan_box()

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
    with patch.object(self.backend, "_request_json", return_value=payload):
      result = await self.backend.get_scan_result()

    self.assertEqual(result.rack_id, "3000756455")
    self.assertEqual(result.entries[0].position, "A01")
    self.assertEqual(result.entries[1].tube_id, "5007377911")

  async def test_get_rack_id(self):
    with patch.object(self.backend, "_request_json", return_value={"RackID": "3000756455"}):
      rack_id = await self.backend.get_rack_id()

    self.assertEqual(rack_id, "3000756455")

  async def test_get_layouts_list_payload(self):
    with patch.object(self.backend, "_request_json", return_value=["96", "48"]):
      layouts = await self.backend.get_layouts()

    self.assertEqual([layout.name for layout in layouts], ["96", "48"])

  async def test_get_layouts_capitalized_key_payload(self):
    with patch.object(self.backend, "_request_json", return_value={"Layout": ["8x12", "6x8"]}):
      layouts = await self.backend.get_layouts()

    self.assertEqual([layout.name for layout in layouts], ["8x12", "6x8"])

  async def test_get_current_layout_dict_payload(self):
    with patch.object(self.backend, "_request_json", return_value={"layout": "96"}):
      layout = await self.backend.get_current_layout()

    self.assertEqual(layout, "96")

  async def test_get_current_layout_capitalized_key_payload(self):
    with patch.object(self.backend, "_request_json", return_value={"Layout": "8x12"}):
      layout = await self.backend.get_current_layout()

    self.assertEqual(layout, "8x12")

  async def test_set_current_layout(self):
    with patch.object(self.backend, "_request", return_value=b"") as request_bytes:
      await self.backend.set_current_layout("96")

    request_bytes.assert_called_once_with(
      "PUT",
      "/currentlayout",
      data=b'{"Layout": "96"}',
      headers={"Content-Type": "application/json; charset=utf-8"},
      expect_json=False,
    )

  async def test_http_error_payload_maps_to_backend_error(self):
    err = self.backend._as_micronic_error(
      json.dumps({"ErrorCode": 4, "ErrorMsg": "invalid state"}).encode("utf-8"),
      fallback="fallback",
    )

    self.assertIsInstance(err, MicronicRackReaderError)
    self.assertIn("invalid state", str(err))

  async def test_parse_scan_result_requires_positions(self):
    with self.assertRaises(MicronicRackReaderError):
      self.backend._parse_scan_result({"RackID": "1", "Date": "20260315", "Time": "114804"})

  async def test_request_sync_retries_connection_reset(self):
    response = MagicMock()
    response.read.return_value = b'{"state":"idle"}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
      "pylabrobot.rack_reading.micronic.http_backend.request.urlopen",
      side_effect=[ConnectionResetError(104, "reset"), response],
    ):
      body = self.backend._request_sync("GET", "/state")

    self.assertEqual(body, b'{"state":"idle"}')

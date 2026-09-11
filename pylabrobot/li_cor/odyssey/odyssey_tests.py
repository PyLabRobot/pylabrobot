"""Odyssey wire-protocol and lifecycle tests; no real instrument is contacted."""

import io
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs

from pylabrobot.io.http import HTTP, HTTPResponse
from pylabrobot.li_cor import OdysseyChatterbox, OdysseyClassic
from pylabrobot.li_cor.odyssey import (
  OdysseyError,
  OdysseyImageError,
  OdysseyScanError,
  OdysseyStatusError,
)
from pylabrobot.li_cor.odyssey.chatterbox import _tiff
from pylabrobot.li_cor.odyssey.tagging import build_identity_description, tag_tiff_with_identity

try:
  from PIL import Image  # type: ignore[import-not-found]
except ImportError:
  Image = None


class OdysseyProtocolTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.io = MagicMock(spec=HTTP)
    self.io.base_url = "http://odyssey.test"
    self.io.setup = AsyncMock()
    self.io.stop = AsyncMock()
    self.io.request_raw = AsyncMock(return_value=self.response("<p>Scanner Status: Idle</p>"))
    self.device = OdysseyClassic(io=self.io)
    await self.device.setup()
    self.io.request_raw.reset_mock()

  @staticmethod
  def response(body: str, status: int = 200, **headers):
    return HTTPResponse(status, body.encode(), headers)

  async def asyncTearDown(self):
    await self.device.stop()

  async def configure(self, **kwargs):
    self.io.request_raw.return_value = self.response("")
    with patch("pylabrobot.li_cor.odyssey.odyssey.asyncio.sleep", new_callable=AsyncMock):
      await self.device.configure_scan(**kwargs)

  async def test_configuration_encodes_units_and_runs_all_initialization_steps(self):
    await self.configure(
      name="membrane",
      width=125,
      height=75,
      origin_x=10,
      origin_y=20,
      resolution=0.042,
      channel_800=False,
      comment="a & b",
    )
    calls = self.io.request_raw.call_args_list
    method, path, body = calls[0].args
    self.assertEqual((method, path), ("POST", "/scanapp/scan/nonjava/configure.pl"))
    form = parse_qs(body.decode(), keep_blank_values=True)
    expected = {
      "channel": ["x"],
      "resolution": ["42"],
      "x0": ["1"],
      "y0": ["2"],
      "width": ["12.5"],
      "height": ["7.5"],
      "x1": ["13.5"],
      "y1": ["9.5"],
      "comment": ["a & b"],
      "chan700": ["chan700"],
    }
    for key, value in expected.items():
      self.assertEqual(form[key], value)
    self.assertNotIn("chan800", form)
    self.assertEqual(len(calls), 8)
    for call, step in zip(calls[1:], range(7, 0, -1)):
      self.assertIn("initializing.pl", call.args[1])
      self.assertEqual(parse_qs(call.args[1].split("?", 1)[1])["timeout"], [str(step)])

  async def test_configuration_follows_redirect_before_countdown(self):
    self.io.request_raw.side_effect = [
      HTTPResponse(302, b"", {"location": "/prepare"}),
      self.response(""),
      *[self.response("") for _ in range(6)],
      HTTPResponse(302, b"", {"location": "/console"}),
      self.response(""),
    ]
    with patch("pylabrobot.li_cor.odyssey.odyssey.asyncio.sleep", new_callable=AsyncMock):
      await self.device.configure_scan()
    calls = self.io.request_raw.call_args_list
    self.assertEqual(calls[1].args[1], "/prepare")
    self.assertEqual(calls[-1].args[1], "/console")

  async def test_configuration_rejects_firmware_error_without_retrying(self):
    self.io.request_raw.return_value = self.response('<Error shorterror="Busy">In use</Error>')
    with self.assertRaisesRegex(OdysseyScanError, "Busy"):
      await self.device.configure_scan()
    self.io.request_raw.assert_awaited_once()
    with self.assertRaises(OdysseyScanError):
      await self.device.start_scan()

  async def test_invalid_configuration_sends_nothing(self):
    for invalid in (
      {"width": 251},
      {"focus": float("nan")},
      {"resolution": 169},
      {"channel_700": False, "channel_800": False},
      {"name": "../scan"},
    ):
      with self.subTest(invalid=invalid), self.assertRaises(ValueError):
        await self.device.configure_scan(**invalid)
    self.io.request_raw.assert_not_awaited()

  async def test_scan_commands_keep_wire_actions_and_do_not_retry(self):
    await self.configure()
    self.io.request_raw.reset_mock()
    for method, action in [
      (self.device.start_scan, "start"),
      (self.device.pause_scan, "pause"),
      (self.device.stop_scan, "stop"),
      (self.device.cancel_scan, "cancel"),
    ]:
      await method()
      self.assertEqual(
        self.io.request_raw.call_args.args[:2],
        ("GET", f"/scanapp/scan/nonjava/command.pl?action={action}"),
      )
    self.assertEqual(self.io.request_raw.await_count, 4)
    self.io.request_raw.side_effect = OSError("connection lost")
    with self.assertRaises(OSError):
      await self.device.stop_scan()
    self.assertEqual(self.io.request_raw.await_count, 5)

  async def test_http_error_does_not_report_success(self):
    self.io.request_raw.return_value = self.response("unauthorized", 401)
    with self.assertRaisesRegex(OdysseyError, "401"):
      await self.device.request_status()
    self.io.request_raw.return_value = self.response("server failure", 500)
    with self.assertRaisesRegex(OdysseyError, "500"):
      await self.device.stop_scan()

  async def test_status_parses_table_markup_and_percent(self):
    self.io.request_raw.return_value = self.response(
      "<tr><td>Scanner Status:</td><td>escanning</td></tr>"
      "<tr><td>Current User:</td><td>operator</td></tr>"
      "<tr><td>Percent Complete:</td><td>12.5%</td></tr>"
      "<tr><td>Time Remaining:</td><td>2 minutes</td></tr>"
      "<tr><td>Lid Status:</td><td>Closed</td></tr>"
    )
    status = await self.device.request_status()
    self.assertEqual(status.state, "Scanning")
    self.assertEqual(status.progress, 12.5)
    self.assertFalse(status.lid_open)
    self.assertEqual(status.current_user, "operator")

  async def test_unrecognized_status_is_not_idle(self):
    for html in ("<html>login page</html>", "Scanner Status: Mystery"):
      self.io.request_raw.return_value = self.response(html)
      with self.assertRaises(OdysseyStatusError):
        await self.device.request_status()

  async def test_wait_observes_scan_and_returns_idle(self):
    self.io.request_raw.side_effect = [
      self.response(f"Scanner Status: {state}") for state in ("Idle", "Scanning", "Idle")
    ]
    reading = await self.device.wait_until_done(poll_interval=0.001)
    self.assertEqual(reading.state, "Idle")
    self.assertEqual(self.io.request_raw.await_count, 3)

  async def test_wait_times_out_on_unchanging_idle(self):
    with self.assertRaises(TimeoutError):
      await self.device.wait_until_done(timeout=0.01, poll_interval=0.001)

  async def test_wait_raises_on_failed_scan(self):
    self.io.request_raw.return_value = self.response("Scanner Status: Failed")
    with self.assertRaises(OdysseyScanError):
      await self.device.wait_until_done()

  async def test_download_encodes_xml_and_preserves_channel_files(self):
    raw = _tiff()
    self.io.request_raw.return_value = HTTPResponse(200, raw, {"content-length": str(len(raw))})
    result = await self.device.download("group & one", "name with space")
    self.assertEqual(result, {700: raw, 800: raw})
    path = self.io.request_raw.call_args_list[0].args[1]
    self.assertTrue(path.startswith("/scan/image/name%20with%20space-700.tif?"))
    xml = parse_qs(path.split("?", 1)[1])["xml"][0]
    self.assertIn("<scangroup>group &amp; one</scangroup>", xml)
    self.assertIn("<channel>700</channel>", xml)

  async def test_download_rejects_truncated_tiff_and_html(self):
    for response in (
      HTTPResponse(200, _tiff(), {"content-length": "9999"}),
      self.response("<html>login</html>"),
    ):
      self.io.request_raw.return_value = response
      with self.assertRaises(OdysseyImageError):
        await self.device.download_channel("odyssey", "test", 700)

  async def test_stop_closes_connection_without_sending_scan_command(self):
    await self.device.stop()
    self.io.request_raw.assert_not_awaited()
    self.assertFalse(self.device.connected)
    with self.assertRaises(RuntimeError):
      await self.device.list_groups()

  async def test_setup_failure_closes_transport(self):
    await self.device.stop()
    self.io.request_raw.return_value = self.response("unauthorized", 401)
    self.io.stop.reset_mock()
    with self.assertRaises(OdysseyError):
      await self.device.setup()
    self.assertFalse(self.device.connected)
    self.io.stop.assert_awaited_once()

  def test_credentials_stay_out_of_serialized_settings(self):
    device = OdysseyClassic(host="scanner", username="user", password="secret")
    self.assertEqual(device.serialize(), {"host": "scanner", "port": 80, "timeout": 60})
    self.assertEqual(device.io.headers["Authorization"], "Basic dXNlcjpzZWNyZXQ=")
    self.assertEqual(device.io.headers["Connection"], "close")


class OdysseyChatterboxTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.io = OdysseyChatterbox()
    self.device = OdysseyClassic(io=self.io)
    await self.device.setup()

  async def asyncTearDown(self):
    await self.device.stop()

  async def configure(self, **kwargs):
    with patch("pylabrobot.li_cor.odyssey.odyssey.asyncio.sleep", new_callable=AsyncMock):
      await self.device.configure_scan(**kwargs)

  async def test_full_scan_and_separate_downloads(self):
    await self.configure(name="membrane", group="lab")
    result = await self.device.scan(poll_interval=0.001)
    self.assertEqual((result.state, result.progress), ("Idle", 100))
    self.assertEqual(await self.device.list_scans("lab"), ["membrane"])
    self.assertNotIn("membrane", await self.device.list_scans("odyssey"))
    images = await self.device.download("lab", "membrane")
    self.assertEqual(set(images), {700, 800})
    self.assertTrue(images[700].startswith(b"II*\x00"))

  async def test_pause_resume_and_partial_stop(self):
    await self.configure(name="partial", channel_800=False)
    await self.device.start_scan()
    before = await self.device.request_status()
    await self.device.pause_scan()
    paused = await self.device.request_status()
    self.assertEqual(paused.progress, before.progress)
    self.assertEqual(paused.state, "Paused")
    await self.device.start_scan()
    result = await self.device.stop_and_save(poll_interval=0.001)
    self.assertEqual(result.channels_available, [700])
    self.assertTrue(result.partial)

  async def test_cancel_discards_output(self):
    await self.configure(name="cancelled")
    await self.device.start_scan()
    await self.device.cancel_scan()
    self.assertNotIn("cancelled", await self.device.list_scans("odyssey"))
    self.assertEqual((await self.device.request_status()).state, "Idle")

  async def test_no_network_for_chatterbox(self):
    with patch("urllib.request.build_opener", side_effect=AssertionError("network attempted")):
      await self.configure(name="offline")
      await self.device.scan(poll_interval=0.001)
      self.assertTrue(await self.device.download_channel("odyssey", "offline", 700))


class TaggingTests(unittest.TestCase):
  def test_description_accepts_plain_identity_dict(self):
    identity = {"pid": "https://example.org/instruments/1"}
    result = json.loads(build_identity_description(identity, scan_name="scan", channel=700))
    self.assertEqual(result, {**identity, "scan_name": "scan", "channel": 700})
    self.assertEqual(identity, {"pid": "https://example.org/instruments/1"})

  def test_empty_identity_and_bad_image_preserve_original_bytes(self):
    self.assertEqual(tag_tiff_with_identity(b"bad image"), b"bad image")
    self.assertEqual(tag_tiff_with_identity(b"bad image", {"pid": "unit"}), b"bad image")

  @unittest.skipIf(Image is None, "Pillow is not installed")
  def test_tiff_tagging_preserves_pixels(self):
    raw = _tiff()
    tagged = tag_tiff_with_identity(raw, {"pid": "unit"}, channel=700)
    with Image.open(io.BytesIO(raw)) as original, Image.open(io.BytesIO(tagged)) as image:
      self.assertEqual(image.tobytes(), original.tobytes())
      self.assertEqual(json.loads(image.tag_v2[270]), {"pid": "unit", "channel": 700})
      self.assertEqual(image.tag_v2[305], "PyLabRobot Odyssey")

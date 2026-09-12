"""Odyssey wire-protocol and lifecycle tests; no real instrument is contacted."""

import asyncio
import io
import json
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs

import anyio
import pytest

from pylabrobot.io.http import HTTP, HTTPResponse
from pylabrobot.li_cor import OdysseyChatterbox, OdysseyClassic
from pylabrobot.li_cor.odyssey import (
  OdysseyError,
  OdysseyImageError,
  OdysseyScanError,
  OdysseyStatusError,
  protocol,
)
from pylabrobot.li_cor.odyssey.chatterbox import _tiff
from pylabrobot.li_cor.odyssey.tagging import build_identity_description, tag_tiff_with_identity
from pylabrobot.testing.concurrency import AnyioTestBase

try:
  from PIL import Image  # type: ignore[import-not-found]

  HAS_PILLOW = True
except ImportError:
  HAS_PILLOW = False


class TestOdysseyLifespan(AnyioTestBase):
  async def test_owned_transport_closes_after_normal_exit_and_reopens(self):
    class OfflineHTTP(HTTP):
      async def request_raw(self, *args, **kwargs):
        return HTTPResponse(200, b"Scanner Status: Idle", {})

    with patch("pylabrobot.li_cor.odyssey.odyssey.HTTP", OfflineHTTP):
      device = OdysseyClassic(host="odyssey.test", username="user", password="secret")
    for _ in range(2):
      async with device:
        assert device.io.is_active
        assert device.connected
      assert not device.io.is_active
      assert not device.connected

  async def test_owned_cleanup_failure_keeps_body_error_as_context(self):
    class FailingCloseHTTP(HTTP):
      async def request_raw(self, *args, **kwargs):
        return HTTPResponse(200, b"Scanner Status: Idle", {})

      async def _close(self):
        await super()._close()
        raise OSError("close failed")

    with patch("pylabrobot.li_cor.odyssey.odyssey.HTTP", FailingCloseHTTP):
      device = OdysseyClassic(host="odyssey.test", username="user", password="secret")
    with pytest.raises(OSError, match="close failed") as exc:
      async with device:
        raise ValueError("body failed")
    assert isinstance(exc.value.__context__, ValueError)
    assert not device.connected
    assert not device.io.is_active

  async def test_configuration_cancellation_finishes_initialization(self):
    class CancelAfterConfigure(OdysseyChatterbox):
      async def request_raw(self, method, path, body=None, *, headers=None, allow_redirects=True):
        response = await super().request_raw(
          method, path, body, headers=headers, allow_redirects=allow_redirects
        )
        if method == "POST" and path == protocol._CONFIGURE_URL_PATH:
          scope.cancel()
          await anyio.lowlevel.checkpoint()
        return response

    transport = CancelAfterConfigure()
    device = OdysseyClassic(io=transport)
    async with transport, device:
      with patch("pylabrobot.li_cor.odyssey.odyssey.anyio.sleep", new_callable=AsyncMock):
        with anyio.CancelScope() as scope:
          await device.configure_scan(name="partial")
      assert scope.cancel_called
      assert transport.state == "Configured"
      assert device._current_scan == ("odyssey", "partial")

  async def test_command_cancellation_after_dispatch_reconciles_response(self):
    class CancelAfterDispatch(OdysseyChatterbox):
      async def request_raw(self, method, path, body=None, *, headers=None, allow_redirects=True):
        response = await super().request_raw(
          method, path, body, headers=headers, allow_redirects=allow_redirects
        )
        if "action=cancel" in path:
          scope.cancel()
          await anyio.lowlevel.checkpoint()
        return response

    transport = CancelAfterDispatch()
    device = OdysseyClassic(io=transport)
    async with transport, device:
      device._current_scan = ("odyssey", "test_scan")
      device._channels = (700, 800)
      with anyio.CancelScope() as scope:
        await device.cancel_scan()
      assert scope.cancel_called
      assert transport.state == "Idle"
      assert device._current_scan is None
      assert device._channels == ()

  async def test_chatterbox_rejects_cancelled_request_before_mutation(self):
    transport = OdysseyChatterbox()
    async with transport:
      with anyio.CancelScope() as scope:
        scope.cancel()
        await transport.request_raw("GET", f"{protocol._COMMAND_URL_PATH}?action=stop")
      assert transport.state == "Idle"

  async def test_chatterbox_borrowed_and_reusable(self):
    transport = OdysseyChatterbox()
    device = OdysseyClassic(io=transport)
    with pytest.raises(RuntimeError):
      async with device:
        pass
    async with transport:
      for _ in range(2):
        async with device:
          assert device.connected
          assert (await device.request_status()).state == "Idle"
        assert transport.is_active
        assert not device.connected
    assert not transport.is_active

  async def test_owned_transport_closes_after_status_failure(self):
    class BrokenHTTP(HTTP):
      async def request_raw(self, *args, **kwargs):
        return HTTPResponse(401, b"unauthorized", {})

    with patch("pylabrobot.li_cor.odyssey.odyssey.HTTP", BrokenHTTP):
      device = OdysseyClassic(host="odyssey.test", username="user", password="secret")
    transport = device.io
    with pytest.raises(OdysseyError):
      async with device:
        pass
    assert not transport.is_active
    assert not device.connected

  async def test_body_failure_does_not_close_borrowed_transport(self):
    transport = OdysseyChatterbox()
    async with transport:
      with pytest.raises(ValueError):
        async with OdysseyClassic(io=transport):
          raise ValueError("body")
      assert (await transport.request_raw("GET", protocol._STATUS_URL_PATH)).status == 200


class OdysseyProtocolTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.io = MagicMock(spec=HTTP)
    self.io.base_url = "http://odyssey.test"
    self.io.is_active = True
    self.io.request_raw = AsyncMock(return_value=self.response("<p>Scanner Status: Idle</p>"))
    self.device = OdysseyClassic(io=self.io)
    await self.device.__aenter__()
    self.io.request_raw.reset_mock()

  @staticmethod
  def response(body: str, status: int = 200, **headers):
    return HTTPResponse(status, body.encode(), headers)

  async def asyncTearDown(self):
    if self.device.is_active:
      await self.device.__aexit__(None, None, None)

  async def configure(self, **kwargs):
    self.io.request_raw.return_value = self.response("")
    with patch("pylabrobot.li_cor.odyssey.odyssey.anyio.sleep", new_callable=AsyncMock):
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
    with patch("pylabrobot.li_cor.odyssey.odyssey.anyio.sleep", new_callable=AsyncMock):
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

  async def test_scan_waits_past_stale_idle_for_fresh_acquisition(self):
    await self.configure()
    self.io.request_raw.reset_mock()
    self.io.request_raw.side_effect = [self.response("")] + [
      self.response(f"Scanner Status: {state}") for state in ("Idle", "Idle", "Scanning", "Idle")
    ]
    states = []
    result = await self.device.scan(
      poll_interval=0.001, on_progress=lambda reading: states.append(reading.state)
    )
    self.assertEqual(result.state, "Idle")
    self.assertEqual(states, ["Idle", "Idle", "Scanning", "Idle"])

  async def test_wait_deadline_cancels_slow_status_request(self):
    cancelled = asyncio.Event()

    async def delayed_response(*args, **kwargs):
      try:
        await asyncio.sleep(1)
        return self.response("Scanner Status: Idle")
      except asyncio.CancelledError:
        cancelled.set()
        raise

    self.io.request_raw.side_effect = delayed_response
    with self.assertRaises(TimeoutError):
      await self.device.wait_until_done(timeout=0.01, require_fresh=False)
    self.assertTrue(cancelled.is_set())

  async def test_wait_rejects_terminal_response_delivered_after_deadline(self):
    async def delayed_response(*args, **kwargs):
      try:
        await asyncio.sleep(1)
      except asyncio.CancelledError:
        return self.response("Scanner Status: Idle")

    self.io.request_raw.side_effect = delayed_response
    with self.assertRaises(TimeoutError):
      await self.device.wait_until_done(timeout=0.01, require_fresh=False)

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
    await self.device.__aexit__(None, None, None)
    self.io.request_raw.assert_not_awaited()
    self.assertFalse(self.device.connected)
    with self.assertRaises(RuntimeError):
      await self.device.list_groups()

  async def test_status_failure_does_not_close_borrowed_transport(self):
    await self.device.__aexit__(None, None, None)
    self.io.request_raw.return_value = self.response("unauthorized", 401)
    with self.assertRaises(OdysseyError):
      await self.device.__aenter__()
    self.assertFalse(self.device.connected)
    self.assertTrue(self.io.is_active)

  def test_credentials_stay_out_of_serialized_settings(self):
    device = OdysseyClassic(host="scanner", username="user", password="secret")
    self.assertEqual(device.serialize(), {"host": "scanner", "port": 80, "timeout": 60})
    self.assertEqual(device.io.headers["Authorization"], "Basic dXNlcjpzZWNyZXQ=")
    self.assertEqual(device.io.headers["Connection"], "close")


class OdysseyChatterboxTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.io = OdysseyChatterbox()
    self.device = OdysseyClassic(io=self.io)
    await self.io.__aenter__()
    await self.device.__aenter__()

  async def asyncTearDown(self):
    await self.device.__aexit__(None, None, None)
    await self.io.__aexit__(None, None, None)

  async def configure(self, **kwargs):
    with patch("pylabrobot.li_cor.odyssey.odyssey.anyio.sleep", new_callable=AsyncMock):
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

  async def test_rejected_reconfiguration_preserves_active_scan_for_stop_and_save(self):
    await self.configure(name="first", channel_800=False)
    await self.device.start_scan()
    with self.assertRaises(OdysseyScanError):
      await self.configure(name="second", channel_700=False, channel_800=True)

    result = await self.device.stop_and_save(poll_interval=0.001)

    self.assertTrue(result.partial)
    self.assertEqual(result.channels_available, [700])
    self.assertIn("first", await self.device.list_scans("odyssey"))
    self.assertNotIn("second", await self.device.list_scans("odyssey"))
    self.assertTrue(await self.device.download_channel("odyssey", "first", 700))

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

  @unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
  def test_tiff_tagging_preserves_pixels(self):
    raw = _tiff()
    tagged = tag_tiff_with_identity(raw, {"pid": "unit"}, channel=700)
    with Image.open(io.BytesIO(raw)) as original, Image.open(io.BytesIO(tagged)) as image:
      self.assertEqual(image.tobytes(), original.tobytes())
      self.assertEqual(json.loads(image.tag_v2[270]), {"pid": "unit", "channel": 700})
      self.assertEqual(image.tag_v2[305], "PyLabRobot Odyssey")

  @unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
  def test_tiff_tagging_preserves_all_pages_tags_and_compressed_image_bytes(self):
    with Image.new("I;16", (3, 2), 42) as first, Image.new("I;16", (3, 2), 999) as second:
      buffer = io.BytesIO()
      first.save(
        buffer,
        format="TIFF",
        save_all=True,
        append_images=[second],
        compression="tiff_deflate",
        tiffinfo={65000: "vendor metadata", 270: "old description"},
      )
    raw = buffer.getvalue()
    tagged = tag_tiff_with_identity(raw, {"pid": "instrument"}, channel=800)
    self.assertEqual(tagged[8 : len(raw)], raw[8:])
    with Image.open(io.BytesIO(raw)) as original, Image.open(io.BytesIO(tagged)) as result:
      self.assertEqual(result.n_frames, 2)
      for index in range(original.n_frames):
        original.seek(index)
        result.seek(index)
        self.assertEqual(result.tobytes(), original.tobytes())
        for tag, value in original.tag_v2.items():
          if tag not in (270, 305):
            self.assertEqual(result.tag_v2[tag], value)
        self.assertEqual(json.loads(result.tag_v2[270]), {"pid": "instrument", "channel": 800})

  @unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
  def test_big_endian_tiff_and_short_software_tag_are_preserved(self):
    with Image.new("I;16B", (2, 1), 513) as image:
      buffer = io.BytesIO()
      image.save(buffer, format="TIFF", tiffinfo={65000: "custom"})
    raw = buffer.getvalue()
    self.assertEqual(raw[:4], b"MM\x00*")
    tagged = tag_tiff_with_identity(raw, {"name": "instrument"}, software_tag="PLR")
    with Image.open(io.BytesIO(raw)) as original, Image.open(io.BytesIO(tagged)) as result:
      self.assertEqual(result.tobytes(), original.tobytes())
      self.assertEqual(result.tag_v2[65000], "custom")
      self.assertEqual(result.tag_v2[305], "PLR")

  def test_bigtiff_and_malformed_directories_return_original_bytes(self):
    cyclic = bytearray(_tiff())
    count = struct.unpack_from("<H", cyclic, 8)[0]
    struct.pack_into("<I", cyclic, 8 + 2 + count * 12, 8)
    for raw in (b"II+\x00\x08\x00\x00\x00", b"II*\x00", _tiff()[:20], bytes(cyclic)):
      with self.subTest(raw=raw):
        self.assertEqual(tag_tiff_with_identity(raw, {"pid": "instrument"}), raw)

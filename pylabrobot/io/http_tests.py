import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from pylabrobot.io.http import HTTP, HTTPError


class _Response:
  def __init__(self, body: bytes):
    self.body = body

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    return False

  def read(self) -> bytes:
    return self.body


class HTTPTests(unittest.IsolatedAsyncioTestCase):
  async def test_request_sends_and_decodes_json_off_event_loop(self) -> None:
    transport = HTTP(
      human_readable_device_name="test device",
      base_url="http://device.local:1234",
      headers={"X-API-Version": "3"},
    )
    await transport.setup()
    with patch(
      "urllib.request.urlopen", return_value=_Response(b'{"data":{"id":"run"}}')
    ) as urlopen:
      response = await transport.request("post", "/runs", {"value": 1})
    await transport.stop()

    self.assertEqual(response, {"data": {"id": "run"}})
    request = urlopen.call_args.args[0]
    self.assertEqual(request.full_url, "http://device.local:1234/runs")
    self.assertEqual(request.method, "POST")
    self.assertEqual(json.loads(request.data), {"value": 1})
    self.assertEqual(request.headers["X-api-version"], "3")
    self.assertEqual(request.headers["Content-type"], "application/json")

  async def test_http_error_includes_response_body(self) -> None:
    transport = HTTP(
      human_readable_device_name="test device",
      base_url="http://device.local:1234",
    )
    await transport.setup()
    error = urllib.error.HTTPError(
      url="http://device.local:1234/runs",
      code=400,
      msg="Bad Request",
      hdrs=Message(),
      fp=io.BytesIO(b'{"message":"bad run"}'),
    )
    with patch("urllib.request.urlopen", side_effect=error):
      with self.assertRaisesRegex(HTTPError, "bad run"):
        await transport.request("POST", "/runs")
    await transport.stop()


if __name__ == "__main__":
  unittest.main()


class _RawResponse(_Response):
  def __init__(self, body: bytes, code: int = 200, headers=None):
    super().__init__(body)
    self.code = code
    self.headers = headers or {}


class HTTPRawTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.transport = HTTP(
      "raw test", "http://device.local:80", headers={"Authorization": "Basic secret"}
    )
    await self.transport.setup()

  async def asyncTearDown(self):
    await self.transport.stop()

  async def test_raw_post_preserves_form_status_and_headers(self):
    with patch("urllib.request.build_opener") as build:
      build.return_value.open.return_value = _RawResponse(b"redirect", 302, {"Location": "/next"})
      response = await self.transport.request_raw(
        "POST",
        "/configure",
        b"scan=a+name&channel=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=False,
      )
    request = build.return_value.open.call_args.args[0]
    self.assertEqual(request.data, b"scan=a+name&channel=x")
    self.assertEqual(request.headers["Authorization"], "Basic secret")
    self.assertEqual(request.headers["Content-type"], "application/x-www-form-urlencoded")
    self.assertEqual(response.status, 302)
    self.assertEqual(response.headers, {"location": "/next"})
    self.assertFalse(build.call_args.args[0].allow_redirects)

  async def test_raw_response_retains_non_utf8_bytes(self):
    data = b"II*\x00\xff\xfe"
    with patch("urllib.request.build_opener") as build:
      build.return_value.open.return_value = _RawResponse(data)
      response = await self.transport.request_raw("GET", "/scan.tif")
    self.assertEqual(response.body, data)

  async def test_raw_request_returns_http_error_body_for_device_parser(self):
    error = urllib.error.HTTPError(
      "http://device.local/status", 401, "Denied", Message(), io.BytesIO(b"denied")
    )
    with patch("urllib.request.build_opener") as build:
      build.return_value.open.side_effect = error
      response = await self.transport.request_raw("GET", "/status")
    self.assertEqual((response.status, response.body), (401, b"denied"))

  async def test_raw_request_does_not_retry_connection_failure(self):
    with patch("urllib.request.build_opener") as build:
      build.return_value.open.side_effect = OSError("lost connection")
      with self.assertRaises(OSError):
        await self.transport.request_raw("POST", "/configure")
    build.return_value.open.assert_called_once()

  async def test_cross_origin_request_is_rejected_before_sending_credentials(self):
    with patch("urllib.request.build_opener") as build:
      with self.assertRaises(ValueError):
        await self.transport.request_raw("GET", "http://another-device/path")
    build.assert_not_called()

  async def test_same_origin_absolute_url_accepts_implicit_default_port(self):
    with patch("urllib.request.build_opener") as build:
      build.return_value.open.return_value = _RawResponse(b"ok")
      await self.transport.request_raw("GET", "http://device.local/path")
    self.assertEqual(build.return_value.open.call_args.args[0].full_url, "http://device.local/path")

  async def test_redirect_policy_blocks_cross_origin_and_allows_relative_redirect(self):
    with patch("urllib.request.build_opener") as build:
      build.return_value.open.return_value = _RawResponse(b"ok")
      await self.transport.request_raw("GET", "/status")
    handler = build.call_args.args[0]
    request = urllib.request.Request(
      "http://device.local:80/status", headers={"Authorization": "Basic secret"}
    )
    with self.assertRaises(ValueError):
      handler.redirect_request(request, None, 302, "Found", {}, "http://another-device/path")
    redirected = handler.redirect_request(
      request, None, 302, "Found", {}, "http://device.local/path"
    )
    self.assertEqual(redirected.full_url, "http://device.local/path")

  async def test_raw_request_requires_setup(self):
    await self.transport.stop()
    with self.assertRaisesRegex(RuntimeError, "setup"):
      await self.transport.request_raw("GET", "/status")

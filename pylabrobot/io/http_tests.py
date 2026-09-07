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

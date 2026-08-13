"""Tests for the HTTP io: what it records, and that a replay reproduces the run."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List

import httpx

import pylabrobot
from pylabrobot.io.capture import CaptureReader
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.http import HTTP, HTTPValidator
from pylabrobot.testing.http_server import serving as _serving

BASE_URL = "http://robot.test:31950"


def _ok(body: Dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
  return lambda request: httpx.Response(200, json=body)


class HTTPCaptureTests(unittest.IsolatedAsyncioTestCase):
  """A live run through HTTP lands in the capture file, verb by verb."""

  def setUp(self):
    self._dir = tempfile.TemporaryDirectory()
    self.capture_file = Path(self._dir.name) / "capture.json"

  def tearDown(self):
    self._dir.cleanup()

  def _recorded(self) -> List[Dict[str, Any]]:
    with open(self.capture_file, "r") as f:
      return list(json.load(f)["commands"])

  async def test_get_records_path_and_response(self):
    io = HTTP(base_url=BASE_URL)
    with _serving(_ok({"api_version": "8.8.0"})):
      await io.setup()
      pylabrobot.start_capture(self.capture_file)
      self.assertEqual(await io.get("/health"), {"api_version": "8.8.0"})
      pylabrobot.stop_capture()

    (command,) = self._recorded()
    self.assertEqual(command["module"], "http")
    self.assertEqual(command["device_id"], BASE_URL)
    self.assertEqual(command["action"], "get")
    self.assertEqual(command["path"], "/health")
    self.assertIsNone(command["request"])
    self.assertEqual(command["response"], {"api_version": "8.8.0"})
    self.assertEqual(command["status"], 200)

  async def test_post_records_the_request_body(self):
    io = HTTP(base_url=BASE_URL)
    sent = {"data": {"commandType": "home", "params": {}}}
    with _serving(_ok({"data": {"id": "cmd-1", "status": "succeeded"}})):
      await io.setup()
      pylabrobot.start_capture(self.capture_file)
      await io.post("/runs/r1/commands", json=sent)
      pylabrobot.stop_capture()

    (command,) = self._recorded()
    self.assertEqual(command["action"], "post")
    self.assertEqual(command["request"], sent)

  async def test_a_refusal_is_recorded_before_it_raises(self):
    """The error shapes are the point: a 4xx must reach the capture file."""
    io = HTTP(base_url=BASE_URL)
    refusal = {"errors": [{"errorType": "LocationIsOccupiedError"}]}
    with _serving(lambda request: httpx.Response(409, json=refusal)):
      await io.setup()
      pylabrobot.start_capture(self.capture_file)
      with self.assertRaises(httpx.HTTPStatusError):
        await io.post("/runs/r1/commands", json={"data": {}})
      pylabrobot.stop_capture()

    (command,) = self._recorded()
    self.assertEqual(command["status"], 409)
    self.assertEqual(command["response"], refusal)

  async def test_a_non_json_body_is_recorded_as_text(self):
    io = HTTP(base_url=BASE_URL)
    with _serving(lambda request: httpx.Response(200, text="not json")):
      await io.setup()
      pylabrobot.start_capture(self.capture_file)
      self.assertEqual(await io.get("/health"), "not json")
      pylabrobot.stop_capture()

    (command,) = self._recorded()
    self.assertEqual(command["response"], "not json")

  async def test_use_before_setup_is_refused(self):
    io = HTTP(base_url=BASE_URL)
    with self.assertRaisesRegex(RuntimeError, "not set up"):
      await io.get("/health")


class HTTPReplayTests(unittest.IsolatedAsyncioTestCase):
  """A recording replays the same run with nothing on the network."""

  def setUp(self):
    self._dir = tempfile.TemporaryDirectory()
    self.capture_file = Path(self._dir.name) / "capture.json"

  def tearDown(self):
    self._dir.cleanup()

  async def _record_two_exchanges(self):
    def handler(request: httpx.Request) -> httpx.Response:
      if request.url.path == "/health":
        return httpx.Response(200, json={"api_version": "8.8.0"})
      return httpx.Response(200, json={"data": {"id": "run-1"}})

    io = HTTP(base_url=BASE_URL)
    with _serving(handler):
      await io.setup()
      pylabrobot.start_capture(self.capture_file)
      await io.get("/health")
      await io.post("/runs", json={"data": {}})
      pylabrobot.stop_capture()

  def _validator(self) -> HTTPValidator:
    return HTTPValidator(CaptureReader(path=str(self.capture_file)), base_url=BASE_URL)

  async def test_replay_returns_the_recorded_responses_in_order(self):
    await self._record_two_exchanges()
    replay = self._validator()
    await replay.setup()

    self.assertEqual(await replay.get("/health"), {"api_version": "8.8.0"})
    self.assertEqual(await replay.post("/runs", json={"data": {}}), {"data": {"id": "run-1"}})
    replay.cr.done()

  async def test_a_different_path_fails_validation(self):
    await self._record_two_exchanges()
    replay = self._validator()
    await replay.setup()

    with self.assertRaisesRegex(ValidationError, "Expected http get /instruments"):
      await replay.get("/instruments")

  async def test_a_changed_request_body_fails_validation(self):
    await self._record_two_exchanges()
    replay = self._validator()
    await replay.setup()

    await replay.get("/health")
    with self.assertRaisesRegex(ValidationError, "Request body mismatch"):
      await replay.post("/runs", json={"data": {"changed": True}})

  async def test_stopping_short_of_the_recording_fails(self):
    """The no-cassette guard, in reverse: a dropped command must not pass."""
    await self._record_two_exchanges()
    replay = self._validator()
    await replay.setup()

    await replay.get("/health")
    with self.assertRaisesRegex(ValidationError, "not fully read"):
      replay.cr.done()

  async def test_a_recorded_refusal_replays_as_the_same_error(self):
    io = HTTP(base_url=BASE_URL)
    refusal = {"errors": [{"errorType": "LocationIsOccupiedError"}]}
    with _serving(lambda request: httpx.Response(409, json=refusal)):
      await io.setup()
      pylabrobot.start_capture(self.capture_file)
      with self.assertRaises(httpx.HTTPStatusError):
        await io.post("/runs/r1/commands", json={"data": {}})
      pylabrobot.stop_capture()

    replay = self._validator()
    await replay.setup()
    with self.assertRaises(httpx.HTTPStatusError) as caught:
      await replay.post("/runs/r1/commands", json={"data": {}})
    self.assertEqual(caught.exception.response.status_code, 409)
    self.assertEqual(caught.exception.response.json(), refusal)


if __name__ == "__main__":
  unittest.main()

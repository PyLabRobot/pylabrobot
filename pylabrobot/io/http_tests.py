"""Offline HTTP exchange, capture, and lifespan regressions."""

import base64
import io
import json
import threading
import urllib.error
import urllib.request
from email.message import Message
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.io.capture import CaptureReader, start_capture, stop_capture
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.http import (
  HTTP,
  HTTPCommand,
  HTTPError,
  HTTPRawCommand,
  HTTPResponse,
  HTTPValidator,
  _RedirectHandler,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class _Response:
  def __init__(self, body: bytes, code: int = 200, headers=None):
    self.body = body
    self.code = self.status = code
    self.headers = headers or {}

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    return False

  def read(self) -> bytes:
    return self.body


async def _wait_for(event: threading.Event) -> None:
  with anyio.fail_after(2):
    while not event.is_set():
      await anyio.sleep(0)


class TestHTTP(AnyioTestBase):
  async def test_json_rejects_foreign_origin_before_dispatch(self) -> None:
    transport = HTTP("test", "http://device.local", headers={"Authorization": "Basic secret"})
    with (
      mock.patch("urllib.request.urlopen") as urlopen,
      mock.patch("urllib.request.build_opener") as build_opener,
    ):
      async with transport:
        with pytest.raises(ValueError, match="origin"):
          await transport.request("GET", "http://other-device.local/status")
      urlopen.assert_not_called()
      build_opener.assert_not_called()

  async def test_json_redirect_rejects_foreign_origin(self) -> None:
    transport = HTTP("test", "http://device.local", headers={"Authorization": "Basic secret"})
    with mock.patch("urllib.request.build_opener") as build_opener:

      def open_request(request, *args, **kwargs):
        handler = build_opener.call_args.args[0]
        handler.redirect_request(
          request, None, 302, "Found", {}, "http://other-device.local/status"
        )
        raise AssertionError("redirect was followed")

      build_opener.return_value.open.side_effect = open_request
      async with transport:
        with pytest.raises(ValueError, match="origin"):
          await transport.request("GET", "/status")

  async def test_raw_capture_replays_into_odyssey_without_network(self, tmp_path) -> None:
    from pylabrobot.li_cor import OdysseyClassic

    path = tmp_path / "raw-capture.json"
    transport = HTTP("odyssey", "http://device.local")
    with mock.patch.object(
      transport, "_make_raw_request", return_value=HTTPResponse(200, b"Scanner Status: Idle", {})
    ):
      async with transport:
        start_capture(path)
        try:
          device = OdysseyClassic(io=transport)
          async with device:
            assert (await device.request_status()).state == "Idle"
        finally:
          stop_capture()

    reader = CaptureReader(str(path))
    validator = HTTPValidator(reader, "odyssey", "http://device.local")
    reader.start()
    try:
      with mock.patch("urllib.request.build_opener", side_effect=AssertionError("network used")):
        async with validator:
          replay_device = OdysseyClassic(io=validator)
          async with replay_device:
            assert (await replay_device.request_status()).state == "Idle"
    finally:
      reader.done()

  async def test_odyssey_redirect_location_survives_raw_capture_replay(self, tmp_path) -> None:
    from pylabrobot.li_cor import OdysseyClassic

    path = tmp_path / "redirect.json"
    transport = HTTP("odyssey", "http://device.local")

    def exchange(method, request_path, body, headers, allow_redirects):
      if request_path == "/scanapp/util/status/":
        return HTTPResponse(200, b"Scanner Status: Idle", {})
      if method == "POST" and request_path == "/scanapp/scan/nonjava/configure.pl":
        return HTTPResponse(302, b"", {"location": "/prepare"})
      return HTTPResponse(200, b"", {})

    with mock.patch.object(transport, "_make_raw_request", side_effect=exchange):
      async with transport:
        start_capture(path)
        try:
          device = OdysseyClassic(io=transport)
          async with device:
            with mock.patch(
              "pylabrobot.li_cor.odyssey.odyssey.anyio.sleep", new_callable=mock.AsyncMock
            ):
              await device.configure_scan()
        finally:
          stop_capture()

    reader = CaptureReader(str(path))
    validator = HTTPValidator(reader, "odyssey", "http://device.local")
    reader.start()
    try:
      with mock.patch("urllib.request.build_opener", side_effect=AssertionError("network used")):
        async with validator:
          device = OdysseyClassic(io=validator)
          async with device:
            with mock.patch(
              "pylabrobot.li_cor.odyssey.odyssey.anyio.sleep", new_callable=mock.AsyncMock
            ):
              await device.configure_scan()
            assert device._current_scan == ("odyssey", "scan")
    finally:
      reader.done()

  async def test_odyssey_truncated_tiff_detected_on_raw_capture_replay(self, tmp_path) -> None:
    from pylabrobot.li_cor import OdysseyClassic
    from pylabrobot.li_cor.odyssey import OdysseyImageError

    path = tmp_path / "image.json"
    transport = HTTP("odyssey", "http://device.local")

    def exchange(method, request_path, body, headers, allow_redirects):
      if request_path == "/scanapp/util/status/":
        return HTTPResponse(200, b"Scanner Status: Idle", {})
      return HTTPResponse(200, b"II*\x00", {"content-length": "12"})

    with mock.patch.object(transport, "_make_raw_request", side_effect=exchange):
      async with transport:
        start_capture(path)
        try:
          device = OdysseyClassic(io=transport)
          async with device:
            with pytest.raises(OdysseyImageError, match="Truncated TIFF"):
              await device.download_channel("odyssey", "scan", 700)
        finally:
          stop_capture()

    reader = CaptureReader(str(path))
    validator = HTTPValidator(reader, "odyssey", "http://device.local")
    reader.start()
    try:
      with mock.patch("urllib.request.build_opener", side_effect=AssertionError("network used")):
        async with validator:
          device = OdysseyClassic(io=validator)
          async with device:
            with pytest.raises(OdysseyImageError, match="Truncated TIFF"):
              await device.download_channel("odyssey", "scan", 700)
    finally:
      reader.done()

  async def test_raw_validator_replays_binary_refusal_and_detects_mismatches(self) -> None:
    command = {
      "module": "http",
      "device_id": "http://device.local",
      "action": "POST",
      "path": "/image",
      "request_base64": "AP8=",
      "status": 401,
      "response_base64": "/gA=",
    }
    reader = mock.Mock()
    reader.next_command.side_effect = lambda: command.copy()
    validator = HTTPValidator(reader, "test", "http://device.local")
    async with validator:
      response = await validator.request_raw("post", "/image", b"\x00\xff")
      assert (response.status, response.body, response.headers) == (401, b"\xfe\x00", {})
      for method, path, body in (
        ("GET", "/image", b"\x00\xff"),
        ("POST", "/other", b"\x00\xff"),
        ("POST", "/image", b"\x00"),
      ):
        with pytest.raises(ValidationError):
          await validator.request_raw(method, path, body)

  async def test_json_exchange_and_refusal_capture(self) -> None:
    transport = HTTP("test", "http://device.local:1234", headers={"X-API-Version": "3"})
    commands: list[HTTPCommand] = []
    with mock.patch("pylabrobot.io.http.capturer.record", commands.append):
      with pytest.raises(RuntimeError):
        await transport.request("GET", "/runs")
      with mock.patch("urllib.request.build_opener") as opener:
        opener.return_value.open.return_value = _Response(b'{"z":2,"a":1}')
        async with transport:
          assert isinstance(transport, HasLifespan)
          assert await transport.request("post", "/runs", {"value": 1}) == {"z": 2, "a": 1}
          request = opener.return_value.open.call_args.args[0]
          assert request.full_url == "http://device.local:1234/runs"
          assert request.method == "POST"
          assert json.loads(request.data) == {"value": 1}
          assert request.headers["X-api-version"] == "3"
          assert request.headers["Content-type"] == "application/json"
          assert (commands[0].action, commands[0].response) == ("POST", '{"a": 1, "z": 2}')
          error = urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", Message(), io.BytesIO(b'{"message":"bad run"}')
          )
          opener.return_value.open.side_effect = error
          with pytest.raises(HTTPError, match="bad run"):
            await transport.request("POST", "/runs")
          assert (commands[1].status, commands[1].response) == (400, '{"message":"bad run"}')
      with pytest.raises(RuntimeError):
        await transport.request("GET", "/runs")

  async def test_raw_response_capture_and_origin_policy(self) -> None:
    transport = HTTP("raw", "http://device.local:80", headers={"Authorization": "Basic secret"})
    commands: list[HTTPRawCommand] = []
    with (
      mock.patch("urllib.request.build_opener") as opener,
      mock.patch("pylabrobot.io.http.capturer") as capture,
    ):
      capture.capture_active = True
      capture.record.side_effect = commands.append
      opener.return_value.open.return_value = _Response(
        b"\xff\xfe", 302, {"Location": "/next", "Authorization": "secret"}
      )
      async with transport:
        response = await transport.request_raw(
          "post",
          "/configure",
          b"request",
          headers={"Content-Type": "text/plain"},
          allow_redirects=False,
        )
        assert (response.status, response.body, response.headers) == (
          302,
          b"\xff\xfe",
          {"location": "/next", "authorization": "secret"},
        )
        request = opener.return_value.open.call_args.args[0]
        assert request.headers["Authorization"] == "Basic secret"
        assert request.headers["Content-type"] == "text/plain"
        assert not opener.call_args.args[0].allow_redirects
        assert commands[0].request_base64 is not None
        assert base64.b64decode(commands[0].request_base64) == b"request"
        assert base64.b64decode(commands[0].response_base64) == b"\xff\xfe"
        assert commands[0].response_headers == {"location": "/next"}
        with pytest.raises(ValueError, match="origin"):
          await transport.request_raw("GET", "http://other-device/path")
        assert opener.return_value.open.call_count == 1
        handler = opener.call_args.args[0]
        assert handler.redirect_request(request, None, 302, "Found", {}, "/next") is None
        handler = _RedirectHandler(True)
        with pytest.raises(ValueError, match="origin"):
          handler.redirect_request(request, None, 302, "Found", {}, "http://other-device/path")
        assert (
          handler.redirect_request(
            request, None, 302, "Found", {}, "http://device.local/next"
          ).full_url
          == "http://device.local/next"
        )

  async def test_raw_refusal_and_connection_failure_not_retried(self) -> None:
    transport = HTTP("raw", "http://device.local")
    error = urllib.error.HTTPError(
      "http://device.local/status", 401, "Denied", Message(), io.BytesIO(b"denied")
    )
    with mock.patch("urllib.request.build_opener") as opener:
      async with transport:
        opener.return_value.open.side_effect = error
        response = await transport.request_raw("GET", "/status")
        assert (response.status, response.body) == (401, b"denied")
        opener.return_value.open.side_effect = OSError("lost connection")
        with pytest.raises(OSError, match="lost connection"):
          await transport.request_raw("POST", "/configure")
      assert opener.return_value.open.call_count == 2

  async def test_cancelled_worker_finishes_capture_before_exit_and_reentry(self) -> None:
    transport = HTTP("raw", "http://device.local")
    started = threading.Event()
    release = threading.Event()
    worker_finished = threading.Event()
    caller_done = anyio.Event()
    exit_done = anyio.Event()
    scopes = []
    commands: list[HTTPRawCommand] = []

    def blocking_request(*args):
      started.set()
      assert release.wait(2)
      worker_finished.set()
      return _Response(b"binary").body

    def exchange(*args):
      return_response = blocking_request(*args)
      return HTTPResponse(200, return_response, {})

    async def use_transport() -> None:
      async with transport:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await transport.request_raw("POST", "/scan", b"body")
          finally:
            caller_done.set()
      exit_done.set()

    with (
      mock.patch.object(transport, "_make_raw_request", side_effect=exchange),
      mock.patch("pylabrobot.io.http.capturer") as capture,
    ):
      capture.capture_active = True
      capture.record.side_effect = commands.append
      async with anyio.create_task_group() as group:
        group.start_soon(use_transport)
        await _wait_for(started)
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not caller_done.is_set()
        assert not exit_done.is_set()
        release.set()
        await exit_done.wait()
      assert worker_finished.is_set()
      assert len(commands) == 1
      assert base64.b64decode(commands[0].response_base64) == b"binary"
      with pytest.raises(RuntimeError):
        await transport.request_raw("GET", "/late")
      async with transport:
        assert (await transport.request_raw("GET", "/again")).body == b"binary"

  async def test_mixed_requests_serialize_complete_exchange(self) -> None:
    transport = HTTP("test", "http://device.local")
    first_started = threading.Event()
    release = threading.Event()
    calls = []

    def raw(*args):
      first_started.set()
      assert release.wait(2)
      calls.append("raw worker")
      return HTTPResponse(200, b"ok", {})

    def json_request(*args):
      calls.append("json worker")
      return 200, "{}"

    with (
      mock.patch.object(transport, "_make_raw_request", side_effect=raw),
      mock.patch.object(transport, "_make_request", side_effect=json_request),
      mock.patch("pylabrobot.io.http.capturer") as capture,
    ):
      capture.capture_active = True
      capture.record.side_effect = lambda command: calls.append(command.action + " capture")
      async with transport:
        async with anyio.create_task_group() as group:
          group.start_soon(transport.request_raw, "POST", "/first")
          await _wait_for(first_started)
          group.start_soon(transport.request, "GET", "/second")
          await anyio.sleep(0)
          assert calls == []
          release.set()
      assert calls == ["raw worker", "POST capture", "json worker", "GET capture"]

  async def test_validator_owns_no_network(self) -> None:
    validator = HTTPValidator(mock.Mock(), "device", "http://device.local")
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
      async with validator:
        assert validator.is_active
    assert not validator.is_active

  async def test_exit_waits_for_external_cancelled_request_and_rejects_queue(self) -> None:
    transport = HTTP("test", "http://device.local")
    started = threading.Event()
    release = threading.Event()
    caller_done = anyio.Event()
    exit_done = anyio.Event()
    leave = anyio.Event()
    scopes = []
    calls = []

    def blocked(*args):
      started.set()
      assert release.wait(2)
      calls.append("worker finished")
      return 200, "{}"

    async def owner() -> None:
      async with transport:
        await leave.wait()
      calls.append("exit finished")
      exit_done.set()

    async def caller() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await transport.request("GET", "/first")
        finally:
          caller_done.set()

    async def queued() -> None:
      with pytest.raises(RuntimeError, match="not active"):
        await transport.request("GET", "/second")

    with mock.patch.object(transport, "_make_request", side_effect=blocked):
      async with anyio.create_task_group() as group:
        group.start_soon(owner)
        with anyio.fail_after(2):
          while not transport.is_active:
            await anyio.sleep(0)
        group.start_soon(caller)
        await _wait_for(started)
        scopes[0].cancel()
        leave.set()
        with anyio.fail_after(2):
          while transport._active:
            await anyio.sleep(0)
        assert not caller_done.is_set()
        assert not exit_done.is_set()
        group.start_soon(queued)
        release.set()
        await caller_done.wait()
        await exit_done.wait()
    assert calls == ["worker finished", "exit finished"]

  async def test_body_and_worker_failures_release_lifespan(self) -> None:
    transport = HTTP("test", "http://device.local")
    with mock.patch.object(transport, "_make_request", side_effect=OSError("offline")):
      with pytest.raises(OSError, match="offline"):
        async with transport:
          await transport.request("GET", "/status")
    assert not transport.is_active
    with pytest.raises(ValueError, match="body"):
      async with transport:
        raise ValueError("body")
    assert not transport.is_active

  async def test_binary_encoding_runs_off_loop_only_when_capture_is_active(self) -> None:
    transport = HTTP("test", "http://device.local")
    encoded_on = []
    caller_thread = threading.get_ident()
    original_encode = transport._encode_raw_capture

    def encode(*args):
      encoded_on.append(threading.get_ident())
      return original_encode(*args)

    with (
      mock.patch.object(
        transport, "_make_raw_request", return_value=HTTPResponse(200, b"\xff", {})
      ),
      mock.patch.object(transport, "_encode_raw_capture", side_effect=encode),
      mock.patch("pylabrobot.io.http.capturer") as capture,
    ):
      async with transport:
        capture.capture_active = False
        await transport.request_raw("GET", "/binary")
        assert encoded_on == []
        capture.capture_active = True
        await transport.request_raw("GET", "/binary")
    assert len(encoded_on) == 1 and encoded_on[0] != caller_thread

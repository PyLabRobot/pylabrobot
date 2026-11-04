"""Exercise HTTP framing on a serial stream without connecting to hardware."""

import http.client
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.capture import CaptureReader, start_capture, stop_capture
from pylabrobot.io.http import HTTPError, HTTPValidator
from pylabrobot.io.http_serial import HTTPSerial
from pylabrobot.opentrons.flex import Flex
from pylabrobot.testing import AnyioTestBase


class SerialPeer:
  """A serial bridge that emits fragmented responses after complete requests."""

  def __init__(self, responses):
    self.responses = list(responses)
    self.incoming = bytearray()
    self.pending = bytearray()
    self.requests = []
    self.closed = False
    self.timeout = 0
    self.write_timeout = 0

  @property
  def in_waiting(self):
    return min(3, len(self.incoming))

  def write(self, data):
    self.pending.extend(data)
    head, sep, body = bytes(self.pending).partition(b"\r\n\r\n")
    if sep:
      headers = dict(line.split(b": ", 1) for line in head.split(b"\r\n")[1:])
      if len(body) == int(headers.get(b"Content-Length", 0)):
        self.requests.append(bytes(self.pending))
        self.pending.clear()
        self.incoming.extend(self.responses.pop(0))
    return len(data)

  def read(self, size):
    data = bytes(self.incoming[:size])
    del self.incoming[:size]
    return data

  def close(self):
    self.closed = True


def response(body=b'{"ok": true}', status="200 OK"):
  """Construct a length-delimited HTTP response."""
  return f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body


class TestHTTPSerial(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self._stack = stack

  async def open_transport(self, responses):
    """Open a transport against the in-memory serial peer."""
    peer = SerialPeer(responses)
    factory = Mock(return_value=peer)
    self._stack.enter_context(
      patch("pylabrobot.io.http_serial.serial", SimpleNamespace(Serial=factory))
    )
    transport = HTTPSerial("Flex", "/dev/test", headers={"Opentrons-Version": "3"})
    await self._stack.enter_async_context(transport.lifespan())
    factory.assert_called_once_with(
      port="/dev/test", baudrate=1152000, timeout=30.0, write_timeout=30.0
    )
    return transport, peer

  async def test_fragmented_post_and_sequential_requests(self):
    peer = SerialPeer([response(), response(b'{"id": "run"}')])
    factory = Mock(return_value=peer)
    with patch("pylabrobot.io.http_serial.serial", SimpleNamespace(Serial=factory)):
      transport = HTTPSerial("Flex", "/dev/test", headers={"Opentrons-Version": "3"})
      async with transport:
        assert await transport.request("GET", "/health") == {"ok": True}
        data = {"name": "µL rack"}
        assert await transport.request("POST", "/runs", data) == {"id": "run"}
        header, body = peer.requests[1].split(b"\r\n\r\n")
        assert header.startswith(b"POST /runs HTTP/1.1\r\n")
        assert b"Opentrons-Version: 3" in header
        assert f"Content-Length: {len(body)}".encode() in header
        assert json.loads(body) == data
        assert not peer.closed
      assert peer.closed

  async def test_chunked_body_with_trailer(self):
    wire = (
      b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
      b'6\r\n{"ok":\r\n6\r\n true}\r\n0\r\nX-End: yes\r\n\r\n'
    )
    transport, _ = await self.open_transport([wire])
    assert await transport.request("GET", "/health") == {"ok": True}

  async def test_empty_response(self):
    transport, _ = await self.open_transport([b"HTTP/1.1 204 No Content\r\n\r\n"])
    assert await transport.request("DELETE", "/runs/id") == {}

  async def test_raw_binary_response_retains_status_and_headers(self):
    transport, peer = await self.open_transport([response(b"\xff\x00", "409 Conflict")])
    result = await transport.request_raw(
      "POST", "/binary", b"\x00\xff", headers={"Content-Type": "application/octet-stream"}
    )
    assert result.status == 409
    assert result.body == b"\xff\x00"
    assert result.headers["content-length"] == "2"
    assert peer.requests[0].endswith(b"\r\n\r\n\x00\xff")

  async def test_redirects_are_not_sent_to_another_destination(self):
    redirect = b"HTTP/1.1 302 Found\r\nLocation: http://other/\r\nContent-Length: 0\r\n\r\n"
    transport, peer = await self.open_transport([redirect, redirect])
    with pytest.raises(http.client.HTTPException, match="does not follow redirects"):
      await transport.request_raw("GET", "/health")
    result = await transport.request_raw("GET", "/health", allow_redirects=False)
    assert result.status == 302
    assert len(peer.requests) == 2
    with pytest.raises(ValueError, match="connected device"):
      await transport.request_raw("GET", "http://other/health")
    assert len(peer.requests) == 2

  async def test_error_response_recorded_before_raising(self):
    transport, peer = await self.open_transport([response(b'{"error":"busy"}', "409 Conflict")])
    with patch("pylabrobot.io.http.capturer.record") as record:
      with pytest.raises(HTTPError, match="busy"):
        await transport.request("POST", "/runs", {})
    assert record.call_args.args[0].status == 409
    assert record.call_args.args[0].device_id == transport.base_url
    assert not peer.closed

  async def test_timeout_closes_port_and_does_not_retry(self):
    transport, peer = await self.open_transport([b"HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n{"])
    with pytest.raises(TimeoutError):
      await transport.request("POST", "/runs", {})
    assert peer.closed
    with pytest.raises(RuntimeError, match="reconnect"):
      await transport.request("POST", "/runs", {})
    assert len(peer.requests) == 1

  async def test_unframed_response_is_rejected(self):
    transport, peer = await self.open_transport([b"HTTP/1.1 200 OK\r\n\r\n{}"])
    with pytest.raises(http.client.HTTPException, match="framing"):
      await transport.request("GET", "/health")
    assert peer.closed

  async def test_capture_replays_without_a_serial_port(self):
    transport, _ = await self.open_transport([response()])
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      start_capture(path)
      try:
        original = await transport.request("GET", "/health")
      finally:
        stop_capture()
      reader = CaptureReader(str(path))
      replay = HTTPValidator(reader, "Flex", base_url=transport.base_url)
      reader.start()
      try:
        assert await replay.request("GET", "/health") == original
      finally:
        reader.done()

  async def test_malformed_status_closes_port(self):
    transport, peer = await self.open_transport([b"not HTTP\r\n\r\n"])
    with pytest.raises(http.client.BadStatusLine):
      await transport.request("GET", "/health")
    assert peer.closed

  async def test_setup_failure_deactivates_transport(self):
    transport = HTTPSerial("Flex", "/dev/test")
    with patch(
      "pylabrobot.io.http_serial.serial", SimpleNamespace(Serial=Mock(side_effect=OSError("busy")))
    ):
      with pytest.raises(OSError, match="busy"):
        async with transport:
          pass
    assert not transport._active

  async def test_optional_dependency_error(self):
    transport = HTTPSerial("Flex", "/dev/test")
    with patch("pylabrobot.io.http_serial.serial", None):
      with pytest.raises(RuntimeError, match="pyserial"):
        async with transport:
          pass
    assert not transport._active

  async def test_flex_connect_over_serial_reads_health_and_deck_configuration(self):
    body = json.dumps(
      {"name": "Flex", "robot_model": "OT-3 Standard", "api_version": "9.1.2"}
    ).encode()
    deck_configuration = (
      Path(__file__).parents[1] / "testing/test_data/opentrons_flex_deck_configuration.json"
    ).read_bytes()
    peer = SerialPeer([response(body), response(deck_configuration)])
    flex = Flex(serial_port="/dev/test")
    with patch("pylabrobot.io.http_serial.serial", SimpleNamespace(Serial=Mock(return_value=peer))):
      async with flex.lifespan(query_only=True):
        assert flex.software_version == "9.1.2"
        assert flex.deck.get_slot(flex.deck.get_trash_area()) == "D3"
        assert [request.split(b"\r\n", 1)[0] for request in peer.requests[:2]] == [
          b"GET /health HTTP/1.1",
          b"GET /deck_configuration HTTP/1.1",
        ]

  def test_flex_rejects_ambiguous_connection(self):
    with pytest.raises(ValueError):
      Flex()
    with pytest.raises(ValueError):
      Flex(host="robot", serial_port="/dev/test")
    with pytest.raises(ValueError):
      Flex(serial_port="")


if __name__ == "__main__":
  unittest.main()

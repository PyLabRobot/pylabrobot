import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union
from unittest.mock import AsyncMock, patch

import websockets.exceptions

from pylabrobot.io.capture import CaptureReader, start_capture, stop_capture
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.websocket import WebSocket, WebSocketValidator


class _FakeConnection:
  """A connection that answers with what it was given, then ends or waits."""

  def __init__(
    self,
    messages: Sequence[Union[str, bytes]] = (),
    ends_with: Optional[BaseException] = None,
  ):
    """
    Args:
      messages: what `recv` answers, in order.
      ends_with: what `recv` raises once they run out. None waits instead, as a live connection
        with nothing to say does.
    """
    self._messages = list(messages)
    self._ends_with = ends_with
    self.drained = asyncio.Event()
    self.sent: List[Union[str, bytes]] = []
    self.closed = False

  async def recv(self) -> Union[str, bytes]:
    """Answer with the next message, then end or wait."""
    if self._messages:
      return self._messages.pop(0)
    self.drained.set()
    if self._ends_with is not None:
      raise self._ends_with
    await asyncio.Event().wait()
    raise AssertionError("a connection that never ends should not have stopped waiting")

  async def send(self, data: Union[str, bytes]) -> None:
    """Record what was sent, and as which frame type."""
    self.sent.append(data)

  async def close(self) -> None:
    """Record that the link was closed."""
    self.closed = True


def _closed_ok() -> websockets.exceptions.ConnectionClosedOK:
  """The exception a server-side close raises on the client."""
  return websockets.exceptions.ConnectionClosedOK(None, None)


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
  """The read path, the write path, and what happens when the link ends."""

  async def _connected(self, connection: _FakeConnection, **kwargs) -> WebSocket:
    """Build a link on a fake connection and set it up.

    Args:
      connection: what `connect` is to answer with.
      kwargs: passed to `WebSocket`.

    Returns:
      The link, set up, with its drain task running.
    """
    io = WebSocket("test device", "ws://device.invalid/ws", **kwargs)
    self.addAsyncCleanup(io.stop)
    with patch("websockets.asyncio.client.connect", new=AsyncMock(return_value=connection)):
      await io.setup()
    return io

  async def test_reads_messages_in_arrival_order(self) -> None:
    """Text comes back UTF-8 encoded and binary as it stands, oldest first."""
    connection = _FakeConnection(["first", b"second", "third"])
    io = await self._connected(connection)
    await connection.drained.wait()

    self.assertEqual(await io.read(), b"first")
    self.assertEqual(await io.read(), b"second")
    self.assertEqual(await io.read(), b"third")

  async def test_read_before_setup_names_the_device(self) -> None:
    """Reading a link that was never opened says which one, and to set it up."""
    io = WebSocket("test device", "ws://device.invalid/ws")
    with self.assertRaises(RuntimeError) as caught:
      await io.read()
    self.assertIn("test device", str(caught.exception))
    self.assertIn("setup()", str(caught.exception))

  async def test_read_times_out_without_taking_the_link_down(self) -> None:
    """A read that waits too long raises, and the link is still usable afterwards."""
    connection = _FakeConnection()
    io = await self._connected(connection)
    await connection.drained.wait()

    with self.assertRaises(TimeoutError):
      await io.read(timeout=0.01)
    self.assertFalse(connection.closed)

  async def test_ended_stream_raises_instead_of_waiting(self) -> None:
    """A read past the end of a closed stream raises rather than waiting for a message that
    cannot arrive - what the end-of-stream marker exists to prevent."""
    connection = _FakeConnection(["last"], ends_with=_closed_ok())
    io = await self._connected(connection)
    await connection.drained.wait()

    self.assertEqual(await io.read(), b"last")
    for _ in range(2):
      # Every read after the end, not just the first: the marker goes back on the queue.
      with self.assertRaises(ConnectionError):
        await io.read(timeout=0.01)

  async def test_end_of_stream_carries_what_ended_it(self) -> None:
    """A stream that ended badly says so through the raised error's cause."""
    ended_with = websockets.exceptions.ConnectionClosedError(None, None)
    connection = _FakeConnection(ends_with=ended_with)
    io = await self._connected(connection)
    await connection.drained.wait()

    with self.assertRaises(ConnectionError) as caught:
      await io.read(timeout=0.01)
    self.assertIs(caught.exception.__cause__, ended_with)

  async def test_full_queue_drops_oldest_and_counts_it(self) -> None:
    """A caller that has not read keeps the newest messages, and is told how many went."""
    connection = _FakeConnection(["1", "2", "3", "4"])
    io = await self._connected(connection, max_queued_messages=2)
    await connection.drained.wait()

    self.assertEqual(io.dropped_messages, 2)
    self.assertEqual(await io.read(), b"3")
    self.assertEqual(await io.read(), b"4")

  async def test_write_sends_and_reports_a_closed_link(self) -> None:
    """A message goes as one frame; a link that closed under it raises ConnectionError."""
    connection = _FakeConnection()
    io = await self._connected(connection)
    await io.write(b"ping")
    self.assertEqual(connection.sent, [b"ping"])

    with patch.object(
      connection, "send", side_effect=websockets.exceptions.ConnectionClosedError(None, None)
    ):
      with self.assertRaises(ConnectionError):
        await io.write(b"ping")

  async def test_write_before_setup_names_the_device(self) -> None:
    """Writing to a link that was never opened says which one, and to set it up."""
    io = WebSocket("test device", "ws://device.invalid/ws")
    with self.assertRaises(RuntimeError) as caught:
      await io.write(b"ping")
    self.assertIn("test device", str(caught.exception))

  async def test_reconnect_after_a_drop_reads_the_new_connection(self) -> None:
    """The reason `reconnect` exists: a link that dropped comes back and reads normally.

    The previous connection's end-of-stream marker must not survive into the new queue, or the
    first read on a live link raises for a connection that is no longer there.
    """
    dropped = _FakeConnection(
      ["before"], ends_with=websockets.exceptions.ConnectionClosedError(None, None)
    )
    io = await self._connected(dropped)
    await dropped.drained.wait()

    second = _FakeConnection(["after1", "after2"])
    with patch("websockets.asyncio.client.connect", new=AsyncMock(return_value=second)):
      await io.reconnect()
    await second.drained.wait()

    self.assertTrue(dropped.closed)
    self.assertEqual(await io.read(), b"after1")
    self.assertEqual(await io.read(), b"after2")

  async def test_reconnect_discards_what_the_old_connection_left(self) -> None:
    """Unread messages go with the connection that delivered them, rather than arriving after a
    reconnect with nothing marking where they stopped describing the device."""
    first = _FakeConnection(["stale"])
    io = await self._connected(first)
    await first.drained.wait()

    second = _FakeConnection(["fresh"])
    with patch("websockets.asyncio.client.connect", new=AsyncMock(return_value=second)):
      await io.reconnect()
    await second.drained.wait()

    self.assertEqual(await io.read(), b"fresh")

  async def test_second_setup_closes_the_first_connection(self) -> None:
    """Setting up twice must not leave the first link open, draining into the same queue."""
    first = _FakeConnection(["from first"])
    io = await self._connected(first)
    await first.drained.wait()

    second = _FakeConnection(["from second"])
    with patch("websockets.asyncio.client.connect", new=AsyncMock(return_value=second)):
      await io.setup()
    await second.drained.wait()

    self.assertTrue(first.closed)
    self.assertEqual(await io.read(), b"from second")

  async def test_stop_closes_the_connection(self) -> None:
    """Stopping closes the link and stops draining it."""
    connection = _FakeConnection()
    io = await self._connected(connection)
    await io.stop()
    self.assertTrue(connection.closed)

  async def test_stop_wakes_a_waiting_reader(self) -> None:
    """A read already waiting when the link is closed is told so, rather than waiting out its
    whole timeout on a link that is gone - which is what the marker exists to prevent."""
    connection = _FakeConnection()
    io = await self._connected(connection)
    reader = asyncio.create_task(io.read(timeout=30))
    await asyncio.sleep(0)

    await io.stop()
    # A whole read_timeout would be 30s; anything that resolves promptly did not wait it out.
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(reader, timeout=1)

  async def test_read_after_stop_reports_a_closed_link(self) -> None:
    """A link that was set up and has since closed is shut, not un-opened: telling the caller to
    call setup() would describe a different problem."""
    connection = _FakeConnection(["only"])
    io = await self._connected(connection)
    await connection.drained.wait()
    self.assertEqual(await io.read(), b"only")
    await io.stop()

    with self.assertRaises(ConnectionError):
      await io.read(timeout=0.01)

  async def test_reader_waiting_across_a_reconnect_does_not_poison_the_new_queue(self) -> None:
    """A read already waiting when `reconnect` swaps the queue must put its marker back where it
    came from. Into the new queue, it would end the new connection's stream before it delivered
    anything - and, being re-queued on every read, once a lap forever."""
    first = _FakeConnection()
    io = await self._connected(first)
    reader = asyncio.create_task(io.read(timeout=30))
    await asyncio.sleep(0)

    second = _FakeConnection(["fresh1", "fresh2"])
    with patch("websockets.asyncio.client.connect", new=AsyncMock(return_value=second)):
      await io.reconnect()
    await second.drained.wait()

    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(reader, timeout=1)
    self.assertEqual(await io.read(), b"fresh1")
    self.assertEqual(await io.read(), b"fresh2")

  async def test_stop_after_the_device_ended_the_stream_costs_no_message(self) -> None:
    """The stream is marked once per connection. A second marker would push the oldest message
    out of a full queue - exactly when a reader has fallen behind and can least afford it."""
    connection = _FakeConnection(["unread"], ends_with=_closed_ok())
    io = await self._connected(connection, max_queued_messages=2)
    await connection.drained.wait()

    await io.stop()

    self.assertEqual(await io.read(), b"unread")
    self.assertEqual(io.dropped_messages, 0)

  async def test_write_after_stop_reports_a_closed_link(self) -> None:
    """As a read on the same link does: shut is not the same as never opened."""
    connection = _FakeConnection()
    io = await self._connected(connection)
    await io.stop()

    with self.assertRaises(ConnectionError):
      await io.write(b"ping")

  async def test_a_failed_reconnect_leaves_the_link_reporting_closed(self) -> None:
    """A connect that raises must not leave a fresh, empty, unmarked queue behind: every read
    would then wait out its whole timeout on a link that is not there."""
    dropped = _FakeConnection(ends_with=_closed_ok())
    io = await self._connected(dropped)
    await dropped.drained.wait()

    failed_with = OSError("no route to host")
    with patch("websockets.asyncio.client.connect", new=AsyncMock(side_effect=failed_with)):
      with self.assertRaises(OSError):
        await io.reconnect()

    for _ in range(2):
      with self.assertRaises(ConnectionError) as caught:
        await io.read(timeout=0.3)
      # Why the reconnect failed, not why the old link ended: the reader knows the second.
      self.assertIs(caught.exception.__cause__, failed_with)

  async def test_a_failed_first_setup_still_reads_as_never_set_up(self) -> None:
    """Nothing was ever open, so the answer is to set it up, not that it closed."""
    io = WebSocket("test device", "ws://device.invalid/ws")
    with patch(
      "websockets.asyncio.client.connect", new=AsyncMock(side_effect=OSError("no route to host"))
    ):
      with self.assertRaises(OSError):
        await io.setup()

    with self.assertRaises(RuntimeError):
      await io.read(timeout=0.3)

  async def test_write_can_send_a_text_frame(self) -> None:
    """A JSON API wants text; the two are different frame types on the wire."""
    connection = _FakeConnection()
    io = await self._connected(connection)
    await io.write(b'{"hello":true}', text=True)
    await io.write(b"\xff\xfe")
    self.assertEqual(connection.sent, ['{"hello":true}', b"\xff\xfe"])

  async def test_a_text_frame_of_bytes_that_are_not_text_is_refused(self) -> None:
    """A text frame carries text, and bytes that do not decode wanted a binary one."""
    connection = _FakeConnection()
    io = await self._connected(connection)
    with self.assertRaises(UnicodeDecodeError):
      await io.write(b"\xff\xfe", text=True)
    self.assertEqual(connection.sent, [])

  async def test_exported_from_the_io_package(self) -> None:
    """Every other transport is reachable from `pylabrobot.io`; so is this one."""
    from pylabrobot.io import WebSocket as exported

    self.assertIs(exported, WebSocket)


class WebSocketCaptureTests(unittest.IsolatedAsyncioTestCase):
  """What a capture file records, and in what form."""

  async def test_capture_keeps_text_readable_and_encodes_binary(self) -> None:
    """A text frame is recorded as itself; a binary one base64, flagged so it reads back."""
    connection = _FakeConnection(['{"event":"tip_pickup"}', b"\xff\xfe"])
    io = WebSocket("test device", "ws://device.invalid/ws")
    with patch("websockets.asyncio.client.connect", new=AsyncMock(return_value=connection)):
      await io.setup()
    await connection.drained.wait()

    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      start_capture(path)
      try:
        await io.read()
        await io.read()
        await io.write(b"ping")
      finally:
        stop_capture()
      recorded = json.loads(path.read_text())["commands"]

    await io.stop()

    self.assertEqual(
      [(c["action"], c["data"], c["binary"]) for c in recorded],
      [
        ("recv", '{"event":"tip_pickup"}', False),
        ("recv", "//4=", True),
        ("send", "cGluZw==", True),
      ],
    )
    self.assertEqual({c["device_id"] for c in recorded}, {"ws://device.invalid/ws"})

  async def test_cannot_be_built_while_capturing(self) -> None:
    """A link built mid-capture would record only part of a session, so it is refused."""
    with tempfile.TemporaryDirectory() as directory:
      start_capture(Path(directory) / "capture.json")
      try:
        with self.assertRaises(RuntimeError):
          WebSocket("test device", "ws://device.invalid/ws")
      finally:
        stop_capture()


class WebSocketCredentialTests(unittest.TestCase):
  """What the link refuses to carry, and what it refuses to write down."""

  def test_credential_header_over_plain_ws_is_refused(self) -> None:
    """An unencrypted link would put the credential on the wire in clear text."""
    for header in ("Authorization", "authorization", "Cookie", "X-API-Key"):
      with self.subTest(header=header):
        with self.assertRaises(ValueError) as caught:
          WebSocket("test device", "ws://device.invalid/ws", headers={header: "Bearer t"})
        self.assertIn(header, str(caught.exception))
        self.assertNotIn("Bearer t", str(caught.exception))

  def test_credential_header_over_wss_is_allowed(self) -> None:
    """An encrypted link is what a credential is for."""
    WebSocket("test device", "wss://device.invalid/ws", headers={"Authorization": "Bearer t"})

  def test_ordinary_header_over_plain_ws_is_allowed(self) -> None:
    """Only headers that carry a credential are refused."""
    WebSocket("test device", "ws://device.invalid/ws", headers={"User-Agent": "plr"})

  def test_serialize_omits_headers(self) -> None:
    """Serialized state is written to disk and passed around; a credential must not ride along."""
    io = WebSocket(
      "test device",
      "wss://device.invalid/ws",
      headers={"Authorization": "Bearer secret-token"},
      max_queued_messages=7,
    )
    serialized = io.serialize()
    self.assertNotIn("headers", serialized)
    self.assertNotIn("secret-token", json.dumps(serialized))
    self.assertEqual(serialized["url"], "wss://device.invalid/ws")
    self.assertEqual(serialized["max_queued_messages"], 7)

  def test_rejects_impossible_limits(self) -> None:
    """A non-positive read timeout or queue size describes a link that cannot work."""
    impossible: Sequence[Dict[str, Any]] = (
      {"read_timeout": 0},
      {"max_queued_messages": 0},
    )
    for kwargs in impossible:
      with self.subTest(**kwargs):
        with self.assertRaises(ValueError):
          WebSocket("test device", "ws://device.invalid/ws", **kwargs)


class WebSocketValidatorTests(unittest.IsolatedAsyncioTestCase):
  """Replaying a capture back through the validator."""

  def _reader(self, commands: List[Dict[str, Any]]) -> CaptureReader:
    """Build a CaptureReader over the given commands.

    Args:
      commands: what the capture is to hold.

    Returns:
      A reader positioned at the first of them.
    """
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      path.write_text(json.dumps({"version": "test", "commands": commands}))
      return CaptureReader(str(path))

  async def test_replays_recorded_reads(self) -> None:
    """Text and binary come back exactly as `read` returned them when recorded."""
    validator = WebSocketValidator(
      cr=self._reader(
        [
          {
            "module": "websocket",
            "device_id": "ws://device.invalid/ws",
            "action": "recv",
            "data": '{"event":"tip_pickup"}',
            "binary": False,
          },
          {
            "module": "websocket",
            "device_id": "ws://device.invalid/ws",
            "action": "recv",
            "data": "//4=",
            "binary": True,
          },
        ]
      ),
      human_readable_device_name="test device",
      url="ws://device.invalid/ws",
    )
    await validator.setup()
    self.assertEqual(await validator.read(), b'{"event":"tip_pickup"}')
    self.assertEqual(await validator.read(), b"\xff\xfe")

  async def test_a_write_that_does_not_match_is_a_validation_error(self) -> None:
    """Sending something the recorded run did not is what validation is for catching."""
    validator = WebSocketValidator(
      cr=self._reader(
        [
          {
            "module": "websocket",
            "device_id": "ws://device.invalid/ws",
            "action": "send",
            "data": "cGluZw==",
            "binary": True,
          }
        ]
      ),
      human_readable_device_name="test device",
      url="ws://device.invalid/ws",
    )
    with self.assertRaises(ValidationError):
      await validator.write(b"pong")

  async def test_a_command_from_another_transport_is_a_validation_error(self) -> None:
    """A capture that diverged onto another transport holds a command of a different shape.
    Building this one out of it would raise TypeError for a missing field, which says nothing
    about what actually went wrong."""
    validator = WebSocketValidator(
      cr=self._reader(
        [{"module": "socket", "device_id": "1.2.3.4:80", "action": "read", "data": "ff"}]
      ),
      human_readable_device_name="test device",
      url="ws://device.invalid/ws",
    )
    with self.assertRaises(ValidationError):
      await validator.read()

  async def test_the_wrong_kind_of_command_is_a_validation_error(self) -> None:
    """A read where the capture recorded a write means the run diverged."""
    validator = WebSocketValidator(
      cr=self._reader(
        [
          {
            "module": "websocket",
            "device_id": "ws://device.invalid/ws",
            "action": "send",
            "data": "cGluZw==",
            "binary": True,
          }
        ]
      ),
      human_readable_device_name="test device",
      url="ws://device.invalid/ws",
    )
    with self.assertRaises(ValidationError):
      await validator.read()


class WebSocketUrlCredentialTests(unittest.TestCase):
  """A credential carried in the url, which is logged, serialized and captured."""

  def test_a_token_in_the_query_string_is_redacted(self) -> None:
    """Keeping a token out of the headers is no use if the url puts it back."""
    io = WebSocket(
      "test device",
      "wss://device.invalid/ws?channels=1,2&token=secret-token&api_key=another",
    )
    serialized = json.dumps(io.serialize())
    self.assertNotIn("secret-token", serialized)
    self.assertNotIn("another", serialized)
    # What endpoint it was survives, so the redacted url still identifies the link.
    self.assertIn("channels=1%2C2", serialized)

  def test_userinfo_is_redacted(self) -> None:
    """A credential in the authority is a credential in the url."""
    io = WebSocket("test device", "wss://user:hunter2@device.invalid/ws")
    self.assertNotIn("hunter2", json.dumps(io.serialize()))
    self.assertIn("device.invalid", json.dumps(io.serialize()))

  def test_redacted_url_names_the_link_in_errors(self) -> None:
    """The refusal says which link it is about, without saying how to reach it as you."""
    with self.assertRaises(ValueError) as caught:
      WebSocket(
        "test device",
        "ws://device.invalid/ws?token=secret-token",
        headers={"Authorization": "Bearer t"},
      )
    self.assertNotIn("secret-token", str(caught.exception))
    self.assertNotIn("Bearer t", str(caught.exception))
    self.assertIn("device.invalid", str(caught.exception))


class WebSocketUrlInTheClearTests(unittest.TestCase):
  """A credential in the url is the same disclosure as one in a header."""

  def test_a_token_in_the_query_string_over_plain_ws_is_refused(self) -> None:
    """Moving a token out of the headers and into the url must not get past the refusal."""
    with self.assertRaises(ValueError) as caught:
      WebSocket("test device", "ws://device.invalid/ws?token=secret-token")
    self.assertIn("token", str(caught.exception))
    self.assertNotIn("secret-token", str(caught.exception))

  def test_userinfo_over_plain_ws_is_refused(self) -> None:
    """A password in the authority goes on the wire like any other credential."""
    with self.assertRaises(ValueError) as caught:
      WebSocket("test device", "ws://user:hunter2@device.invalid/ws")
    self.assertNotIn("hunter2", str(caught.exception))

  def test_the_same_url_over_wss_is_allowed(self) -> None:
    """Encrypted is what a credential in a url needs, not that it be absent."""
    WebSocket("test device", "wss://user:hunter2@device.invalid/ws?token=secret-token")

  def test_an_ordinary_query_over_plain_ws_is_allowed(self) -> None:
    """Only the parameters that carry a credential are refused."""
    WebSocket("test device", "ws://device.invalid/ws?channels=1,2&sensors=pressure")

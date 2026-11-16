"""UDP datagram boundaries, peer filtering, timeout, reconnect, and capture tests."""

import json
import socket
import tempfile
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io import UDP, UDPValidator, start_capture, stop_capture
from pylabrobot.io.capture import CaptureReader
from pylabrobot.io.errors import ValidationError
from pylabrobot.serializer import deserialize, serialize
from pylabrobot.testing.concurrency import AnyioTestBase


class TestUDP(AnyioTestBase):
  """Exercise the UDP transport over local sockets without lab hardware."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Bind a local peer and connect a UDP transport to it."""
    self.peer = await anyio.create_udp_socket(local_host="127.0.0.1")
    await stack.enter_async_context(self.peer)
    peer_port = self.peer.extra(anyio.abc.SocketAttribute.local_port)
    self.io = UDP("test", "127.0.0.1", peer_port)
    await stack.enter_async_context(self.io.lifespan())
    await self.io.write(b"hello")
    data, address = await self.peer.receive()
    self.address = (str(address[0]), int(address[1]))
    assert data == b"hello"

  async def test_datagrams_and_capture(self) -> None:
    """Separate, empty, and large packets remain distinct in reads, captures, and replay."""
    packets = [b"first", b"", b"x" * 4096, b"last"]
    peer_port = self.peer.extra(anyio.abc.SocketAttribute.local_port)
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      start_capture(path)
      try:
        with pytest.raises(RuntimeError, match="capture or validation is active"):
          UDP("blocked", "127.0.0.1", peer_port)
        for packet in packets:
          await self.peer.sendto(packet, *self.address)
          assert await self.io.read() == packet
        await self.io.write(b"sent")
        data, _ = await self.peer.receive()
        assert data == b"sent"
      finally:
        stop_capture()
      commands = json.loads(path.read_text())["commands"]
      assert [c["module"] for c in commands] == ["udp"] * 5
      assert [bytes.fromhex(c["data"]) for c in commands] == packets + [b"sent"]

      validator = UDPValidator(CaptureReader(str(path)), "test", "127.0.0.1", peer_port)
      async with validator:
        for packet in packets:
          assert await validator.read() == packet
        await validator.write(b"sent")
      validator.cr.done()

      bad_validator = UDPValidator(CaptureReader(str(path)), "test", "127.0.0.1", peer_port)
      async with bad_validator:
        with pytest.raises(ValidationError):
          await bad_validator.write(b"unexpected")

  async def test_other_peer_is_filtered(self) -> None:
    """The kernel rejects a datagram from a different source port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
      stranger.sendto(b"wrong peer", self.address)
    with pytest.raises(TimeoutError):
      with anyio.fail_after(0.05):
        await self.io.read()
    await self.peer.sendto(b"right peer", *self.address)
    assert await self.io.read() == b"right peer"

  async def test_timeout_cancel_and_reconnect(self) -> None:
    """Timed out and cancelled reads do not consume later packets; reconnect replaces the port."""
    with pytest.raises(TimeoutError):
      with anyio.fail_after(0.05):
        await self.io.read()

    with pytest.warns(DeprecationWarning, match=r"UDP\.read\(timeout=\.\.\.\)"):
      with pytest.raises(TimeoutError):
        await self.io.read(timeout=0.02)

    started = anyio.Event()

    async def reader_task(scope: anyio.CancelScope) -> None:
      with scope:
        started.set()
        await self.io.read()

    cancel_scope = anyio.CancelScope()
    async with anyio.create_task_group() as tg:
      tg.start_soon(reader_task, cancel_scope)
      await started.wait()
      await anyio.sleep(0.01)
      cancel_scope.cancel()
    assert cancel_scope.cancelled_caught

    await self.peer.sendto(b"after cancel", *self.address)
    assert await self.io.read() == b"after cancel"

    await self.io.reconnect()
    await self.io.write(b"reconnected")
    data, new_address = await self.peer.receive()
    assert data == b"reconnected"
    assert (str(new_address[0]), int(new_address[1])) != self.address
    self.address = (str(new_address[0]), int(new_address[1]))

    closed = UDP("closed", "127.0.0.1", 9)
    with pytest.raises(RuntimeError, match="enter its lifespan"):
      await closed.read()
    with pytest.raises(RuntimeError, match="enter its lifespan"):
      await closed.write(b"closed")
    with pytest.raises(RuntimeError, match="enter its lifespan"):
      await closed.reconnect()

  async def test_reconnect_unblocks_concurrent_reader_and_failed_reconnect_invalidates(
    self,
  ) -> None:
    """Reconnecting closes the old socket without deadlocking a concurrent read."""
    read_started = anyio.Event()
    read_errors: list[BaseException] = []

    async def blocked_reader() -> None:
      read_started.set()
      try:
        await self.io.read()
      except (
        OSError,
        anyio.BrokenResourceError,
        anyio.ClosedResourceError,
        ConnectionError,
      ) as exc:
        read_errors.append(exc)

    with anyio.fail_after(5.0):
      async with anyio.create_task_group() as tg:
        tg.start_soon(blocked_reader)
        await read_started.wait()
        await anyio.sleep(0.01)
        await self.io.reconnect()

    assert len(read_errors) == 1
    await self.io.write(b"after concurrent reconnect")
    data, _ = await self.peer.receive()
    assert data == b"after concurrent reconnect"

    with patch("pylabrobot.io.udp.anyio.create_connected_udp_socket", side_effect=OSError("boom")):
      with pytest.raises(OSError, match="boom"):
        await self.io.reconnect()
    with pytest.raises(ConnectionError, match="connection lost"):
      await self.io.read()
    with pytest.raises(ConnectionError, match="connection lost"):
      await self.io.write(b"rejected")

  async def test_disconnect_preserves_connection_lost_latch(self) -> None:
    """Disconnecting after a transport fault keeps ConnectionError until reconnect."""
    self.io._mark_connection_lost()
    await self.io._disconnect()
    with pytest.raises(ConnectionError, match="connection lost"):
      await self.io.read()
    with pytest.raises(ConnectionError, match="connection lost"):
      await self.io.write(b"rejected")

  async def test_invalid_datagram_does_not_send(self) -> None:
    """Empty writes and oversized packets are rejected before socket IO."""
    for data in (b"", b"x" * 65508):
      with pytest.raises(ValueError):
        await self.io.write(data)
    with pytest.raises(TimeoutError):
      with anyio.fail_after(0.02):
        await self.peer.receive()

  def test_serialize_roundtrip_emits_no_deprecation_warning(self) -> None:
    """Default UDP serialization round-trips cleanly without triggering read_timeout warnings."""
    with warnings.catch_warnings(record=True) as captured:
      warnings.simplefilter("always", DeprecationWarning)
      restored = deserialize(serialize(self.io))
    assert isinstance(restored, UDP)
    assert restored._host == "127.0.0.1"
    assert restored._read_timeout is None
    assert not [w for w in captured if issubclass(w.category, DeprecationWarning)]

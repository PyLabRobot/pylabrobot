"""UDP datagram boundaries, peer filtering, timeout, and capture tests."""

import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path

from pylabrobot.io import UDP, start_capture, stop_capture


class UDPTests(unittest.IsolatedAsyncioTestCase):
  """Exercise the transport over local sockets without lab hardware."""

  async def asyncSetUp(self) -> None:
    """Bind a local peer and connect a transport to it."""
    self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.peer.bind(("127.0.0.1", 0))
    self.peer.setblocking(False)
    self.io = UDP("test", "127.0.0.1", self.peer.getsockname()[1], read_timeout=0.05)
    await self.io.setup()
    await self.io.write(b"hello")
    self.peer.settimeout(1)
    data, self.address = await asyncio.to_thread(self.peer.recvfrom, 65535)
    self.peer.setblocking(False)
    self.assertEqual(data, b"hello")

  async def asyncTearDown(self) -> None:
    """Release both endpoints."""
    await self.io.stop()
    self.peer.close()

  async def test_datagrams_and_capture(self) -> None:
    """Separate, empty, and large packets remain distinct in reads and captures."""
    packets = [b"first", b"", b"x" * 4096, b"last"]
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      start_capture(path)
      try:
        for packet in packets:
          self.peer.sendto(packet, self.address)
          self.assertEqual(await self.io.read(), packet)
        await self.io.write(b"sent")
        data = await asyncio.get_running_loop().sock_recv(self.peer, 65535)
        self.assertEqual(data, b"sent")
      finally:
        stop_capture()
      commands = json.loads(path.read_text())["commands"]
      self.assertEqual([c["module"] for c in commands], ["udp"] * 5)
      self.assertEqual([bytes.fromhex(c["data"]) for c in commands], packets + [b"sent"])

  async def test_other_peer_is_filtered(self) -> None:
    """The kernel rejects a datagram from a different source port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
      stranger.sendto(b"wrong peer", self.address)
    with self.assertRaises(asyncio.TimeoutError):
      await self.io.read()
    self.peer.sendto(b"right peer", self.address)
    self.assertEqual(await self.io.read(), b"right peer")

  async def test_timeout_cancel_and_reconnect(self) -> None:
    """Timed out and cancelled reads do not consume later packets."""
    with self.assertRaises(asyncio.TimeoutError):
      await self.io.read()
    pending = asyncio.create_task(self.io.read(timeout=10))
    await asyncio.sleep(0.01)
    pending.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await pending
    self.peer.sendto(b"after cancel", self.address)
    self.assertEqual(await self.io.read(), b"after cancel")
    await self.io.stop()
    await self.io.stop()
    with self.assertRaises(RuntimeError):
      await self.io.read()
    with self.assertRaises(RuntimeError):
      await self.io.write(b"closed")
    await self.io.setup()
    await self.io.write(b"reconnected")
    self.assertEqual(await asyncio.get_running_loop().sock_recv(self.peer, 65535), b"reconnected")

  async def test_invalid_datagram_does_not_send(self) -> None:
    """Empty writes and oversized packets are rejected before socket IO."""
    for data in (b"", b"x" * 65508):
      with self.assertRaises(ValueError):
        await self.io.write(data)
    with self.assertRaises(asyncio.TimeoutError):
      await asyncio.wait_for(asyncio.get_running_loop().sock_recv(self.peer, 65535), 0.02)

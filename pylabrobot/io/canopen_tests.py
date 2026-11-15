"""CANopen transport tests use mocked adapters; no CAN interface is opened."""

import asyncio
import threading
import unittest
from unittest.mock import MagicMock, patch

from pylabrobot.io.canopen import CANopen, canopen


@unittest.skipIf(canopen is None, "optional canopen dependency is not installed")
class CANopenTests(unittest.IsolatedAsyncioTestCase):
  """Verify worker lifetime, cancellation, subscriptions and NMT framing."""

  async def test_cancelled_sdo_keeps_channel_until_worker_finishes(self) -> None:
    """Cancellation cannot let a second SDO overtake an unfinished transfer."""
    network = MagicMock()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def upload(index: int, subindex: int) -> bytes:
      """Block the first transaction while a competing transaction queues."""
      calls.append(index)
      if index == 1:
        entered.set()
        if not release.wait(2):
          raise TimeoutError("test worker was not released")
      return b"\x05"

    network.add_node.return_value.sdo.upload.side_effect = upload
    io = CANopen("virtual")
    with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
      await io.setup([1])
    first = asyncio.create_task(io.upload(1, 1, 0))
    await asyncio.to_thread(entered.wait, 2)
    first.cancel()
    second = asyncio.create_task(io.upload(1, 2, 0))
    await asyncio.sleep(0.01)
    self.assertEqual(calls, [1])
    first.cancel()
    release.set()
    with self.assertRaises(asyncio.CancelledError):
      await first
    self.assertEqual(await second, b"\x05")
    self.assertEqual(calls, [1, 2])
    await io.stop()
    network.disconnect.assert_called_once()

  async def test_nmt_request_masks_toggle_and_discards_stale_callbacks(self) -> None:
    """Node guarding uses RTR and queued callbacks from a closed session are ignored."""
    network = MagicMock()
    callbacks = {}

    def subscribe(cob_id, callback) -> None:
      """Retain the library-thread callback."""
      callbacks[cob_id] = callback

    def send(message) -> None:
      """Supply a node guard response from the worker thread."""
      if message.is_remote_frame:
        callbacks[message.arbitration_id](message.arbitration_id, b"\x85", 0.0)

    network.subscribe.side_effect = subscribe
    network.bus.send.side_effect = send
    io = CANopen("virtual")
    with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
      await io.setup([1])
    self.assertEqual(await io.request_nmt_state(1), 5)
    frame = network.bus.send.call_args.args[0]
    self.assertEqual(frame.arbitration_id, 0x701)
    self.assertEqual(frame.dlc, 1)
    self.assertTrue(frame.is_remote_frame)
    self.assertEqual(frame.data, b"")
    seen = []
    io.subscribe(0x181, lambda cob, data, timestamp: seen.append(data))
    callback = callbacks[0x181]
    await io.stop()
    callback(0x181, b"late", 0.0)
    await asyncio.sleep(0)
    self.assertEqual(seen, [])

  async def test_heartbeat_fallback_restores_period(self) -> None:
    """Gold-style heartbeat readback restores the exact period on every exit path."""
    for outcome in ("success", "timeout", "cancel", "write_error"):
      with self.subTest(outcome=outcome):
        network = MagicMock()
        callbacks = {}
        period = b"\x34\x12"
        changed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def subscribe(cob_id, callback) -> None:
          """Retain the receive callback."""
          callbacks[cob_id] = callback

        def download(index, subindex, data) -> None:
          """Emulate a period write, including errors after applying it."""
          nonlocal period
          self.assertEqual((index, subindex), (0x1017, 0))
          period = data
          if data == b"\x64\x00":
            loop.call_soon_threadsafe(changed.set)
            if outcome == "success":
              callbacks[0x701](0x701, b"\x05", 0.0)
            elif outcome == "write_error":
              raise OSError("lost acknowledgement")

        network.subscribe.side_effect = subscribe
        network.add_node.return_value.sdo.upload.side_effect = lambda *_: period
        network.add_node.return_value.sdo.download.side_effect = download
        io = CANopen("virtual", timeout=0.02)
        with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
          await io.setup([1])
        request = asyncio.create_task(io.request_nmt_state(1))
        if outcome == "cancel":
          await changed.wait()
          request.cancel()
          with self.assertRaises(asyncio.CancelledError):
            await request
        elif outcome == "timeout":
          with self.assertRaises(asyncio.TimeoutError):
            await request
        elif outcome == "write_error":
          with self.assertRaisesRegex(OSError, "lost acknowledgement"):
            await request
        else:
          self.assertEqual(await request, 5)
          self.assertEqual(
            await asyncio.gather(io.request_nmt_state(1), io.request_nmt_state(1)), [5, 5]
          )
        self.assertEqual(period, b"\x34\x12")
        self.assertEqual(io._callbacks[0x701], [])
        await io.stop()

  async def test_partial_connect_failure_disconnects(self) -> None:
    """A partially allocated adapter is released when connect raises."""
    network = MagicMock()
    network.connect.side_effect = OSError("adapter error")
    io = CANopen("virtual")
    with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
      with self.assertRaisesRegex(OSError, "adapter error"):
        await io.setup([1])
    network.disconnect.assert_called_once()
    self.assertFalse(io.connected)

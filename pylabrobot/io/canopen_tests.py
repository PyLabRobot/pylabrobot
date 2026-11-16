"""CANopen transport tests use mocked adapters; no CAN interface is opened."""

import threading
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

import anyio
import pytest

from pylabrobot.concurrency import non_cancelling_task_group
from pylabrobot.io.canopen import CANopen, canopen
from pylabrobot.testing.concurrency import AnyioTestBase


@pytest.mark.skipif(canopen is None, reason="optional canopen dependency is not installed")
class TestCANopen(AnyioTestBase):
  """Verify worker lifetime, cancellation, subscriptions and NMT framing."""

  async def test_cancelled_sdo_keeps_channel_until_worker_finishes(self) -> None:
    """Cancellation cannot let a second SDO overtake an unfinished transfer."""
    network = MagicMock()
    entered, release = threading.Event(), threading.Event()
    calls: List[int] = []

    def upload(index: int, subindex: int) -> bytes:
      """Block the first transaction while a competing transaction queues."""
      del subindex
      calls.append(index)
      if index == 1:
        entered.set()
        if not release.wait(2):
          raise TimeoutError("test worker was not released")
      return b"\x05"

    network.add_node.return_value.sdo.upload.side_effect = upload
    io = CANopen("virtual")
    with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
      async with io.lifespan(node_ids=[1]):
        first_scope = anyio.CancelScope()
        first_done = anyio.Event()
        second_result: Optional[bytes] = None

        async def run_first() -> None:
          with first_scope:
            await io.upload(1, 1, 0)
          assert first_scope.cancelled_caught
          first_done.set()

        async def run_second() -> None:
          nonlocal second_result
          second_result = await io.upload(1, 2, 0)

        async with anyio.create_task_group() as tg:
          tg.start_soon(run_first)
          assert await anyio.to_thread.run_sync(entered.wait, 2.0)
          first_scope.cancel()
          tg.start_soon(run_second)
          await anyio.sleep(0.01)
          assert calls == [1]
          release.set()
          await first_done.wait()

        assert second_result == b"\x05"
        assert calls == [1, 2]

    network.disconnect.assert_called_once()

  async def test_nmt_request_masks_toggle_and_discards_stale_callbacks(self) -> None:
    """Node guarding uses RTR and queued callbacks from a closed session are ignored."""
    network = MagicMock()
    callbacks: Dict[int, Callable[[int, bytes, float], None]] = {}

    def subscribe(cob_id: int, callback: Callable[[int, bytes, float], None]) -> None:
      """Retain the library-thread callback."""
      callbacks[cob_id] = callback

    def send(message: Any) -> None:
      """Supply a node guard response from the worker thread."""
      if message.is_remote_frame:
        callbacks[message.arbitration_id](message.arbitration_id, b"\x85", 0.0)

    network.subscribe.side_effect = subscribe
    network.bus.send.side_effect = send
    io = CANopen("virtual")
    seen: List[bytes] = []
    with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
      async with io.lifespan(node_ids=[1]):
        assert await io.request_nmt_state(1) == 5
        frame = network.bus.send.call_args.args[0]
        assert frame.arbitration_id == 0x701
        assert frame.dlc == 1
        assert frame.is_remote_frame
        assert frame.data == b""
        io.subscribe(0x181, lambda cob, data, timestamp: seen.append(data))
        callback = callbacks[0x181]

    callback(0x181, b"late", 0.0)
    await anyio.sleep(0)
    assert seen == []

  async def test_heartbeat_fallback_restores_period(self) -> None:
    """Gold-style heartbeat readback restores the exact period on every exit path."""
    for outcome in ("success", "timeout", "cancel", "write_error"):
      network = MagicMock()
      callbacks: Dict[int, Callable[[int, bytes, float], None]] = {}
      period = b"\x34\x12"
      changed = threading.Event()

      def subscribe(cob_id: int, callback: Callable[[int, bytes, float], None]) -> None:
        """Retain the receive callback."""
        callbacks[cob_id] = callback

      def download(index: int, subindex: int, data: bytes) -> None:
        """Emulate a period write, including errors after applying it."""
        nonlocal period
        assert (index, subindex) == (0x1017, 0)
        period = data
        if data == b"\x64\x00":
          changed.set()
          if outcome == "success":
            callbacks[0x701](0x701, b"\x05", 0.0)
          elif outcome == "write_error":
            raise OSError("lost acknowledgement")

      network.subscribe.side_effect = subscribe
      network.add_node.return_value.sdo.upload.side_effect = lambda *_: period
      network.add_node.return_value.sdo.download.side_effect = download
      io = CANopen("virtual", timeout=0.02)
      with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
        async with io.lifespan(node_ids=[1]):
          if outcome == "cancel":
            cancel_scope = anyio.CancelScope()

            async def cancel_when_written() -> None:
              assert await anyio.to_thread.run_sync(changed.wait, 2.0)
              cancel_scope.cancel()

            async with anyio.create_task_group() as tg:
              tg.start_soon(cancel_when_written)
              with cancel_scope:
                await io.request_nmt_state(1)
            assert cancel_scope.cancelled_caught
          elif outcome == "timeout":
            with pytest.raises(TimeoutError):
              await io.request_nmt_state(1)
          elif outcome == "write_error":
            with pytest.raises(OSError, match="lost acknowledgement"):
              await io.request_nmt_state(1)
          else:
            assert await io.request_nmt_state(1) == 5
            results: List[int] = []

            async def query_once() -> None:
              results.append(await io.request_nmt_state(1))

            async with non_cancelling_task_group() as query_tg:
              query_tg.start_soon(query_once)
              query_tg.start_soon(query_once)
            assert results == [5, 5]

          assert period == b"\x34\x12"
          assert io._callbacks[0x701] == []

  async def test_partial_connect_failure_disconnects(self) -> None:
    """A partially allocated adapter is released when connect raises."""
    network = MagicMock()
    network.connect.side_effect = OSError("adapter error")
    io = CANopen("virtual", node_ids=[1])
    with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
      with pytest.raises(OSError, match="adapter error"):
        async with io:
          pass
    network.disconnect.assert_called_once()
    assert not io.connected

  async def test_disconnect_releases_adapter_even_when_in_flight_wait_times_out(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A stuck transfer exceeding the idle wait budget must still call network.disconnect."""
    network = MagicMock()
    io = CANopen("virtual", node_ids=[1])
    original_move_on_after = anyio.move_on_after
    monkeypatch.setattr(
      "pylabrobot.io.canopen.anyio.move_on_after",
      lambda delay: original_move_on_after(0.01 if delay == 5.0 else delay),
    )
    with patch("pylabrobot.io.canopen.canopen.Network", return_value=network):
      async with io:
        io._in_flight = 1
        io._idle = anyio.Event()
    network.disconnect.assert_called_once()
    assert not io.connected

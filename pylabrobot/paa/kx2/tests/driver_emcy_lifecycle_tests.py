"""Lifecycle tests for the KX2 driver's EMCY callback registration / shutdown.

Covers the post-PR-880 fixes for:
- ``stop()`` clearing ``_emcy_callbacks`` (no leak across setup retries).
- ``stop()`` setting ``_loop = None`` before ``network.disconnect()`` so racing
  listener-thread ``_cb``s no-op at their guard.
- ``setup()`` subscribing EMCY before sending the NMT start command.
- ``KX2ArmBackend._on_emcy`` scheduling via the driver's captured loop, not the
  deprecated ``asyncio.get_event_loop()``.
"""
import asyncio
import struct
import unittest
from typing import List, Tuple
from unittest import mock

from pylabrobot.paa.kx2 import driver as driver_mod
from pylabrobot.paa.kx2.arm_backend import KX2ArmBackend
from pylabrobot.paa.kx2.driver import (
  EmcyFrame,
  KX2Driver,
  _EMCY_COB_BASE,
  _IpmEmcyState,
)


def _frame(
  err_code: int, elmo: int = 0, err_reg: int = 0, data1: int = 0, data2: int = 0
) -> bytes:
  return struct.pack("<HBBHH", err_code, err_reg, elmo, data1, data2)


class StopClearsCallbacksTests(unittest.TestCase):
  def test_stop_clears_emcy_callbacks(self):
    drv = KX2Driver()
    drv.add_emcy_callback(lambda *args: None)
    drv.add_emcy_callback(lambda *args: None)
    self.assertEqual(len(drv._emcy_callbacks), 2)

    # Simulate a live driver: stop() short-circuits when _network is None.
    drv._network = mock.MagicMock()
    drv._loop = asyncio.new_event_loop()
    try:
      drv._loop.run_until_complete(drv.stop())
    finally:
      # stop() set _loop = None already; nothing to close here.
      pass
    self.assertEqual(drv._emcy_callbacks, [])

  def test_stop_sets_loop_to_none(self):
    drv = KX2Driver()
    drv._network = mock.MagicMock()
    loop = asyncio.new_event_loop()
    drv._loop = loop
    try:
      loop.run_until_complete(drv.stop())
    finally:
      loop.close()
    self.assertIsNone(drv._loop)

  def test_stop_loop_cleared_before_disconnect(self):
    # If a listener-thread _cb fires during disconnect, _loop must already be
    # None so the cb's `if self._loop is None: return` guard fires.
    drv = KX2Driver()
    network = mock.MagicMock()
    observed = {}

    def _disconnect():
      observed["loop_at_disconnect"] = drv._loop

    network.disconnect.side_effect = _disconnect
    drv._network = network
    loop = asyncio.new_event_loop()
    drv._loop = loop
    try:
      loop.run_until_complete(drv.stop())
    finally:
      loop.close()
    self.assertIsNone(observed["loop_at_disconnect"])


class StaleCallbackAfterStopTests(unittest.TestCase):
  def test_stale_listener_cb_noops_after_stop(self):
    drv = KX2Driver()
    cb = drv._make_emcy_callback(node_id=1)

    # Simulate setup having run, then stop() teardown.
    drv._network = mock.MagicMock()
    loop = asyncio.new_event_loop()
    drv._loop = loop
    drv._ipm_emcy[1] = _IpmEmcyState()
    try:
      loop.run_until_complete(drv.stop())
    finally:
      loop.close()

    # Stale listener-thread callback fires now: must not crash, must not
    # resurrect any state.
    cb(_EMCY_COB_BASE + 1, _frame(0x5441), 0.0)

    self.assertFalse(drv.emcy_move_error_received)
    self.assertEqual(drv.emcy_move_error, "")
    self.assertEqual(drv._ipm_emcy, {})
    self.assertEqual(drv._emcy_callbacks, [])


class SetupOrdersEmcySubscribeBeforeNmtStartTests(unittest.TestCase):
  def test_emcy_subscribe_precedes_nmt_start(self):
    # Build a fake canopen.Network that records subscribe + nmt.send_command
    # in call order, and stub the rest of setup just enough to reach the
    # ordering point we care about. Entries are heterogeneous tuples: some
    # carry a payload (e.g. ("nmt", code)), others are tag-only (("scan",)).
    calls: List[Tuple] = []

    class _FakeNmt:
      def send_command(self_inner, code):
        calls.append(("nmt", code))

    class _FakeScanner:
      def __init__(self_inner):
        self_inner.nodes = [1, 2, 3, 4, 6]

      def search(self_inner):
        calls.append(("scan",))

    class _FakeSdo:
      RESPONSE_TIMEOUT = 0.3

      def download(self_inner, *a, **k):
        pass

      def upload(self_inner, *a, **k):
        return b""

    class _FakeNode:
      def __init__(self_inner):
        self_inner.sdo = _FakeSdo()

    class _FakeNetwork:
      def __init__(self_inner):
        self_inner.nmt = _FakeNmt()
        self_inner.scanner = _FakeScanner()

      def connect(self_inner, **kwargs):
        calls.append(("connect",))

      def disconnect(self_inner):
        calls.append(("disconnect",))

      def subscribe(self_inner, cob_id, cb):
        calls.append(("subscribe", cob_id))

      def add_node(self_inner, nid, od):
        return _FakeNode()

      def send_message(self_inner, *a, **k):
        pass

    drv = KX2Driver()

    # Short-circuit the parts of setup we don't need (PDO mapping, Elmo
    # vendor-object writes, ipm_select_mode). The fake's add_node returns a
    # node whose sdo download/upload are no-ops, so these would technically
    # work, but skipping keeps the test fast and focused on call ordering.
    async def _noop(*a, **k):
      return None

    with mock.patch.object(driver_mod.canopen, "Network", _FakeNetwork), \
         mock.patch.object(driver_mod, "_HAS_CANOPEN", True), \
         mock.patch.object(KX2Driver, "_can_tpdo_unmap", _noop), \
         mock.patch.object(KX2Driver, "_tpdo_map", _noop), \
         mock.patch.object(KX2Driver, "_rpdo_map", _noop), \
         mock.patch.object(KX2Driver, "can_sdo_download_elmo_object", _noop), \
         mock.patch.object(KX2Driver, "ipm_select_mode", _noop), \
         mock.patch("asyncio.sleep", _noop):
      asyncio.new_event_loop().run_until_complete(drv.setup())

    # Find the index of the NMT 0x01 ("Start All Nodes") and the EMCY
    # subscribe calls — every EMCY subscribe must precede the start.
    start_idx = next(
      i for i, c in enumerate(calls) if c == ("nmt", 0x01)
    )
    emcy_subscribe_idxs = [
      i for i, c in enumerate(calls)
      if c[0] == "subscribe" and _EMCY_COB_BASE <= c[1] < _EMCY_COB_BASE + 0x80
    ]
    self.assertEqual(len(emcy_subscribe_idxs), len(drv.node_id_list))
    self.assertTrue(all(i < start_idx for i in emcy_subscribe_idxs))

    # And the subscribed COB-IDs match the expected node list.
    subscribed_nids = sorted(
      calls[i][1] - _EMCY_COB_BASE for i in emcy_subscribe_idxs
    )
    self.assertEqual(subscribed_nids, drv.node_id_list)


class OnEmcyUsesDriverLoopTests(unittest.TestCase):
  def test_on_emcy_schedules_via_driver_loop(self):
    drv = KX2Driver()
    backend = KX2ArmBackend(driver=drv)

    fake_loop = mock.MagicMock()
    fake_loop.create_task = mock.MagicMock()
    drv._loop = fake_loop

    frame = EmcyFrame(0x5441, 0, 0, 0, 0)
    backend._on_emcy(
      node_id=1, frame=frame, description="E-stop button was pressed",
      disable_motors=True,
    )

    self.assertEqual(fake_loop.create_task.call_count, 1)
    # Sanity: the scheduled object is a coroutine. Close it so asyncio
    # doesn't warn about a never-awaited coroutine.
    (coro,), _ = fake_loop.create_task.call_args
    self.assertTrue(asyncio.iscoroutine(coro))
    coro.close()

  def test_on_emcy_no_op_when_disable_motors_false(self):
    drv = KX2Driver()
    backend = KX2ArmBackend(driver=drv)

    fake_loop = mock.MagicMock()
    fake_loop.create_task = mock.MagicMock()
    drv._loop = fake_loop

    backend._on_emcy(
      node_id=1, frame=EmcyFrame(0x8130, 0, 0, 0, 0),
      description="Heartbeat event", disable_motors=False,
    )

    fake_loop.create_task.assert_not_called()


if __name__ == "__main__":
  unittest.main()

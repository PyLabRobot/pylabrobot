"""Unit tests for the KX2 driver's EMCY (CANopen Emergency) handling.

Covers the decoder table (parity with clscanmotor.cs:1070-1267) and the live
``KX2Driver._dispatch_emcy`` path: state mutation, sticky-error fields,
suppress-callback edge case, and user-callback invocation.
"""
import struct
import unittest

from pylabrobot.paa.kx2.driver import (
  EmcyFrame,
  KX2Driver,
  _PvtEmcyState,
  _decode_emcy,
)


def _frame(
  err_code: int, elmo: int = 0, err_reg: int = 0, data1: int = 0, data2: int = 0
) -> bytes:
  return struct.pack("<HBBHH", err_code, err_reg, elmo, data1, data2)


class DecodeEmcyTests(unittest.TestCase):
  def test_pvt_queue_low_ff00(self):
    state = _PvtEmcyState()
    desc, disable, suppress = _decode_emcy(
      EmcyFrame(0xFF00, 0, 0x56, 0x1234, 0x0042), state
    )
    self.assertEqual(desc, "Queue Low")
    self.assertFalse(disable)
    self.assertFalse(suppress)
    self.assertTrue(state.queue_low)
    self.assertEqual(state.queue_low_write_pointer, 0x1234)
    self.assertEqual(state.queue_low_read_pointer, 0x0042)

  def test_pvt_queue_full_ff02(self):
    state = _PvtEmcyState()
    desc, disable, suppress = _decode_emcy(
      EmcyFrame(0xFF02, 0, 0x34, 0x55AA, 0), state
    )
    self.assertEqual(desc, "Queue Full")
    self.assertFalse(disable)
    self.assertFalse(suppress)
    self.assertTrue(state.queue_full)
    self.assertEqual(state.queue_full_failed_write_pointer, 0x55AA)

  def test_estop_disables_motors(self):
    state = _PvtEmcyState()
    desc, disable, suppress = _decode_emcy(EmcyFrame(0x5441, 0, 0, 0, 0), state)
    self.assertEqual(desc, "E-stop button was pressed")
    self.assertTrue(disable)
    self.assertFalse(suppress)

  def test_interpolation_underflow_suppresses_callback(self):
    state = _PvtEmcyState()
    desc, disable, suppress = _decode_emcy(EmcyFrame(0xFF02, 0, 0x8A, 0, 0), state)
    self.assertEqual(desc, "Position Interpolation buffer underflow")
    self.assertFalse(disable)
    self.assertTrue(suppress)
    self.assertTrue(state.queue_low)

  def test_unknown_code_falls_back_to_hex(self):
    state = _PvtEmcyState()
    desc, disable, suppress = _decode_emcy(EmcyFrame(0x1234, 0, 0xAB, 0, 0), state)
    self.assertEqual(desc, "Unknown EMCY 0x1234/0xAB")
    self.assertFalse(disable)
    self.assertFalse(suppress)

  def test_ff02_unknown_elmo_is_ds402_ip_error(self):
    state = _PvtEmcyState()
    desc, _, _ = _decode_emcy(EmcyFrame(0xFF02, 0, 0xCC, 0, 0), state)
    self.assertEqual(desc, "DS402 IP Error 0xCC")

  def test_position_tracking_error_disables_motors(self):
    state = _PvtEmcyState()
    desc, disable, _ = _decode_emcy(EmcyFrame(0x8611, 0, 0, 0, 0), state)
    self.assertEqual(desc, "Position tracking error")
    self.assertTrue(disable)


class DispatchEmcyTests(unittest.TestCase):
  def setUp(self):
    self.driver = KX2Driver()
    # _dispatch_emcy normally runs on the asyncio loop after
    # call_soon_threadsafe; here we drive it synchronously since the method
    # itself is sync and doesn't touch the network.
    self.driver._pvt_emcy[1] = _PvtEmcyState()

  def test_estop_sets_sticky_error_fields(self):
    self.driver._dispatch_emcy(1, _frame(0x5441))
    self.assertTrue(self.driver.emcy_move_error_received)
    self.assertEqual(self.driver.emcy_move_error, "E-stop button was pressed")
    self.assertEqual(self.driver.emcy_move_error_node_id, 1)
    self.assertIsNotNone(self.driver.last_emcy)
    self.assertEqual(self.driver.last_emcy.err_code, 0x5441)

  def test_non_fatal_does_not_set_sticky_error(self):
    self.driver._dispatch_emcy(1, _frame(0xFF00, 0x56, data1=10, data2=5))
    self.assertFalse(self.driver.emcy_move_error_received)
    self.assertEqual(self.driver.emcy_move_error, "")
    self.assertTrue(self.driver._pvt_emcy[1].queue_low)
    self.assertEqual(self.driver._pvt_emcy[1].queue_low_write_pointer, 10)

  def test_callback_invoked(self):
    received = []
    self.driver.add_emcy_callback(
      lambda nid, frame, desc, disable: received.append((nid, desc, disable))
    )
    self.driver._dispatch_emcy(1, _frame(0x5441))
    self.assertEqual(len(received), 1)
    nid, desc, disable = received[0]
    self.assertEqual(nid, 1)
    self.assertEqual(desc, "E-stop button was pressed")
    self.assertTrue(disable)

  def test_callback_suppressed_for_interpolation_underflow(self):
    received = []
    self.driver.add_emcy_callback(lambda *args: received.append(args))
    self.driver._dispatch_emcy(1, _frame(0xFF02, 0x8A))
    self.assertEqual(received, [])
    self.assertTrue(self.driver._pvt_emcy[1].queue_low)

  def test_callback_exception_does_not_break_dispatch(self):
    received = []

    def bad(*_):
      raise RuntimeError("intentional")

    self.driver.add_emcy_callback(bad)
    self.driver.add_emcy_callback(lambda *a: received.append(a))
    self.driver._dispatch_emcy(1, _frame(0x5441))
    self.assertEqual(len(received), 1)

  def test_clear_emcy_state_resets_node(self):
    self.driver._dispatch_emcy(1, _frame(0xFF00, 0x56, data1=10, data2=5))
    self.driver._dispatch_emcy(1, _frame(0x5441))
    self.driver.clear_emcy_state(node_id=1)
    self.assertFalse(self.driver.emcy_move_error_received)
    self.assertEqual(self.driver.emcy_move_error, "")
    self.assertIsNone(self.driver.emcy_move_error_node_id)
    self.assertFalse(self.driver._pvt_emcy[1].queue_low)

  def test_short_frame_logged_not_raised(self):
    # Should warn and return without raising.
    self.driver._dispatch_emcy(1, b"\x00\x00\x00")
    self.assertFalse(self.driver.emcy_move_error_received)


if __name__ == "__main__":
  unittest.main()

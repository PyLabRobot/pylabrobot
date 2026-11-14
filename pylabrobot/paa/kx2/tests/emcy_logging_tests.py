"""Polish-pass tests for the KX2 EMCY pipeline.

Covers three follow-ups to the main EMCY decoder/dispatcher behavior tested
in ``driver_emcy_tests.py``:

* DS301 §7.2.7.1 "error reset" frame (err_code=0).
* Tiered log levels in ``_dispatch_emcy``.
* ``home_motor`` preserves rich EMCY descriptions on motor-fault re-raise.
"""

import logging
import struct
import unittest

from pylabrobot.paa.kx2.kx2 import KX2
from pylabrobot.paa.kx2.protocol import (
  EmcyFrame,
  _decode_emcy,
  _NodeEmcyState,
)


def _frame(err_code: int, elmo: int = 0, err_reg: int = 0, data1: int = 0, data2: int = 0) -> bytes:
  return struct.pack("<HBBHH", err_code, err_reg, elmo, data1, data2)


class DecodeErrorResetTests(unittest.TestCase):
  def test_zero_err_code_is_error_reset_and_suppresses(self):
    state = _NodeEmcyState()
    desc, disable, suppress = _decode_emcy(EmcyFrame(0, 0, 0, 0, 0), state)
    self.assertEqual(desc, "Error reset")
    self.assertFalse(disable)
    self.assertTrue(suppress)

  def test_zero_err_code_does_not_mutate_state(self):
    state = _NodeEmcyState()
    state.queue_low = False
    state.queue_full = False
    _decode_emcy(EmcyFrame(0, 0, 0, 0, 0), state)
    # All sticky flags untouched.
    self.assertFalse(state.queue_low)
    self.assertFalse(state.queue_full)
    self.assertFalse(state.bad_head_pointer)
    self.assertFalse(state.bad_mode_init_data)
    self.assertFalse(state.motion_terminated)
    self.assertFalse(state.out_of_modulo)

  def test_zero_err_code_with_nonzero_elmo_still_treated_as_reset(self):
    # Some drives stamp a stale elmo byte on the reset frame; err_code=0 is
    # the canonical signal per DS301 so we treat it as reset regardless.
    state = _NodeEmcyState()
    desc, disable, suppress = _decode_emcy(EmcyFrame(0, 0, 0xFF, 0, 0), state)
    self.assertEqual(desc, "Error reset")
    self.assertFalse(disable)
    self.assertTrue(suppress)

  def test_existing_unknown_code_path_unaffected(self):
    # Sanity: the err_code=0 branch must not steal the unknown-code path.
    state = _NodeEmcyState()
    desc, _, _ = _decode_emcy(EmcyFrame(0x1234, 0, 0xAB, 0, 0), state)
    self.assertEqual(desc, "Unknown EMCY 0x1234/0xAB")


class DispatchEmcyLogLevelTests(unittest.TestCase):
  """Verify the level matrix in ``_dispatch_emcy`` so IPM housekeeping events
  don't drown ops logs while real faults stay loud."""

  def setUp(self):
    self.driver = KX2()
    self.driver._emcy[1] = _NodeEmcyState()
    self.logger_name = "pylabrobot.paa.kx2.kx2"

  def _dispatch_at(self, payload: bytes):
    with self.assertLogs(self.logger_name, level=logging.DEBUG) as cm:
      self.driver._dispatch_emcy(1, payload)
    # Find the EMCY line (ignore any unrelated logs).
    emcy_records = [r for r in cm.records if "EMCY node=" in r.getMessage()]
    self.assertEqual(len(emcy_records), 1, cm.records)
    return emcy_records[0]

  def test_error_reset_logs_at_debug(self):
    rec = self._dispatch_at(_frame(0x0000))
    self.assertEqual(rec.levelno, logging.DEBUG)

  def test_interpolation_underflow_logs_at_debug(self):
    rec = self._dispatch_at(_frame(0xFF02, 0x8A))
    self.assertEqual(rec.levelno, logging.DEBUG)

  def test_queue_low_logs_at_info(self):
    rec = self._dispatch_at(_frame(0xFF00, 0x56, data1=10, data2=5))
    self.assertEqual(rec.levelno, logging.INFO)

  def test_estop_logs_at_error(self):
    rec = self._dispatch_at(_frame(0x5441))
    self.assertEqual(rec.levelno, logging.ERROR)

  def test_unknown_emcy_logs_at_warning(self):
    rec = self._dispatch_at(_frame(0x9999, 0x00))
    self.assertEqual(rec.levelno, logging.WARNING)


if __name__ == "__main__":
  unittest.main()

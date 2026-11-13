"""Event decoding tests, on payloads recorded from a Mantis v4."""

import unittest

from pylabrobot.formulatrix.mantis.driver.errors import FrameError
from pylabrobot.formulatrix.mantis.driver.events import (
  HOME_DONE_EVENT,
  INPUT_CHANGED_EVENT,
  MOTION_STARTED_EVENT,
  MOTOR_ERROR_EVENT,
  MOVE_DONE_EVENT,
  SEQUENCE_PROGRESS_EVENT,
  SEQUENCE_STOPPED_EVENT,
  decode_home_done,
  decode_input_changed,
  decode_motion_started,
  decode_motor_error,
  decode_move_done,
  decode_sequence_progress,
  decode_sequence_stopped,
  describe_event,
)

# Event payloads a Mantis v4 sent, as hex.
RECORDED_MOTION_STARTED = "0200"
RECORDED_MOVE_DONE = "0200040000000000d0e74ec000000000000000000000000000000000"
# A move-done after a motor error, with status bit 0x0100 set.
RECORDED_MOVE_DONE_AFTER_ERROR = "00000001000000a08ac7574000000000000000000000000000000000"
RECORDED_HOME_DONE = "0200000000001148a2c0000000000078c0bf"
RECORDED_MOTOR_ERROR = "01000800"
RECORDED_SEQUENCE_PROGRESS = "03000200c600"
RECORDED_SEQUENCE_STOPPED = "19000000"
RECORDED_INPUT_CHANGED = "00000100"


class DecodeTests(unittest.TestCase):
  def test_decodes(self):
    cases = [
      (decode_motion_started, RECORDED_MOTION_STARTED, 2),
      (decode_move_done, RECORDED_MOVE_DONE, (2, 4, -61.81103515625, 0.0, 0.0)),
      (decode_move_done, RECORDED_MOVE_DONE_AFTER_ERROR, (0, 0x100, 95.11783599853516, 0.0, 0.0)),
      (decode_home_done, RECORDED_HOME_DONE, (2, -2340.033203125, -0.128662109375)),
      (decode_motor_error, RECORDED_MOTOR_ERROR, (1, 8)),
      (decode_sequence_progress, RECORDED_SEQUENCE_PROGRESS, (3, 2, 198)),
      (decode_sequence_stopped, RECORDED_SEQUENCE_STOPPED, (25, 0)),
      (decode_input_changed, RECORDED_INPUT_CHANGED, (0, True)),
    ]
    for decode, payload, expected in cases:
      with self.subTest(payload=payload):
        self.assertEqual(decode(payload=bytes.fromhex(payload)), expected)

  def test_descriptions(self):
    cases = [
      (MOTION_STARTED_EVENT, RECORDED_MOTION_STARTED, "motor 2 started moving"),
      (MOVE_DONE_EVENT, RECORDED_MOVE_DONE, "motor 2 moved, status 0x0004: "),
      (
        MOVE_DONE_EVENT,
        RECORDED_MOVE_DONE_AFTER_ERROR,
        "motor 0 moved, status 0x0100 (following error while moving)",
      ),
      (HOME_DONE_EVENT, RECORDED_HOME_DONE, "motor 2 homed"),
      (
        MOTOR_ERROR_EVENT,
        RECORDED_MOTOR_ERROR,
        "motor 1 reported error 8: a skip was detected while the motor was idle",
      ),
      (SEQUENCE_PROGRESS_EVENT, RECORDED_SEQUENCE_PROGRESS, "item 3 played; 2 queued, 198 free"),
      (SEQUENCE_STOPPED_EVENT, RECORDED_SEQUENCE_STOPPED, "sequencer stopped at item 25"),
      (INPUT_CHANGED_EVENT, RECORDED_INPUT_CHANGED, "input 0 on"),
      (0x7FFF, "", "event 0x7fff, not decoded: "),
    ]
    for code, payload, expected in cases:
      with self.subTest(code=code, payload=payload):
        self.assertIn(expected, describe_event(code=code, payload=bytes.fromhex(payload)))

  def test_payload_of_the_wrong_size_raises(self):
    with self.assertRaises(FrameError):
      decode_sequence_progress(payload=bytes.fromhex(RECORDED_SEQUENCE_PROGRESS)[:4])
    with self.assertRaises(FrameError):
      describe_event(code=MOTOR_ERROR_EVENT, payload=bytes.fromhex(RECORDED_MOTOR_ERROR)[:2])


if __name__ == "__main__":
  unittest.main()

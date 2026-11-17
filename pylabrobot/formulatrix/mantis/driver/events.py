"""The events a Mantis sends unasked, and what each carries.

An event is a device frame with a code from ``FIRST_EVENT_CODE`` to 0x7FFF; every one seen came
from board 0. Its packet number echoes a recent command and matches nothing. Payloads are
little-endian. A code not listed here, such as 0x7FFF with no payload, is logged by its number.
"""

import struct
from typing import Callable, Dict, Tuple

from .errors import MOTOR_ERROR_CODE_DESCRIPTIONS, MOTOR_STATUS_ERROR_BITS, FrameError

# A motor started moving: the motor.
MOTION_STARTED_EVENT = 0x0200
# A motor's move ended: the motor, its status word (``MOTOR_STATUS_ERROR_BITS``) and three values,
# of which only the first, where it stopped, has been seen other than 0.
MOVE_DONE_EVENT = 0x0201
# A motor's homing ended: the motor and two values.
HOME_DONE_EVENT = 0x0202
# A motor reported an error: the motor and the code (``MOTOR_ERROR_CODE_DESCRIPTIONS``).
MOTOR_ERROR_EVENT = 0x0203
# The sequencer played a queue item: the item, the items still queued and the free slots.
SEQUENCE_PROGRESS_EVENT = 0x0310
# The sequencer stopped: the last item and a second word, 0 after a stop and 2 after an abort that
# left two items queued.
SEQUENCE_STOPPED_EVENT = 0x0311
# A digital input changed: the input and whether it is on.
INPUT_CHANGED_EVENT = 0x0321

# The events a motor error ends: a waiter for one fails with the error at once.
MOTION_EVENTS = (MOTION_STARTED_EVENT, MOVE_DONE_EVENT, HOME_DONE_EVENT, SEQUENCE_PROGRESS_EVENT)

# Each event's payload layout.
EVENT_PAYLOAD_FORMATS: Dict[int, str] = {
  MOTION_STARTED_EVENT: "<H",
  MOVE_DONE_EVENT: "<hHddd",
  HOME_DONE_EVENT: "<hdd",
  MOTOR_ERROR_EVENT: "<HH",
  SEQUENCE_PROGRESS_EVENT: "<hhh",
  SEQUENCE_STOPPED_EVENT: "<hh",
  INPUT_CHANGED_EVENT: "<HH",
}


def _unpack_event(*, code: int, payload: bytes) -> Tuple:
  """Split an event's payload into its fields.

  Raises:
    FrameError: if the payload's size does not match the event's layout.
  """
  layout = EVENT_PAYLOAD_FORMATS[code]
  if len(payload) != struct.calcsize(layout):
    raise FrameError(
      f"event 0x{code:04x} carries {struct.calcsize(layout)} bytes, got {payload.hex()}"
    )
  return struct.unpack(layout, payload)


def decode_motion_started(payload: bytes) -> int:
  """The motor a motion-started event names."""
  (motor,) = _unpack_event(code=MOTION_STARTED_EVENT, payload=payload)
  return int(motor)


def decode_move_done(payload: bytes) -> Tuple[int, int, float, float, float]:
  """A move-done event's motor, status word and three values, in the device's units."""
  motor, status, first, second, third = _unpack_event(code=MOVE_DONE_EVENT, payload=payload)
  return motor, status, first, second, third


def decode_home_done(payload: bytes) -> Tuple[int, float, float]:
  """A home-done event's motor and two values, in the device's units."""
  motor, first, second = _unpack_event(code=HOME_DONE_EVENT, payload=payload)
  return motor, first, second


def decode_motor_error(payload: bytes) -> Tuple[int, int]:
  """A motor-error event's motor and code."""
  motor, code = _unpack_event(code=MOTOR_ERROR_EVENT, payload=payload)
  return motor, code


def decode_sequence_progress(payload: bytes) -> Tuple[int, int, int]:
  """A sequence-progress event's item, the items still queued and the free slots."""
  item, queued, free = _unpack_event(code=SEQUENCE_PROGRESS_EVENT, payload=payload)
  return item, queued, free


def decode_sequence_stopped(payload: bytes) -> Tuple[int, int]:
  """A sequence-stopped event's last item and its second word."""
  item, unprocessed = _unpack_event(code=SEQUENCE_STOPPED_EVENT, payload=payload)
  return item, unprocessed


def decode_input_changed(payload: bytes) -> Tuple[int, bool]:
  """An input-changed event's input and whether it is on."""
  input_number, on = _unpack_event(code=INPUT_CHANGED_EVENT, payload=payload)
  return input_number, bool(on)


def _describe_motion_started(payload: bytes) -> str:
  """A motion-started event, in words."""
  return f"motor {decode_motion_started(payload=payload)} started moving"


def _describe_move_done(payload: bytes) -> str:
  """A move-done event, in words."""
  motor, status, first, second, third = decode_move_done(payload=payload)
  faults = [meaning for bit, meaning in MOTOR_STATUS_ERROR_BITS.items() if status & bit]
  fault_text = f" ({', '.join(faults)})" if faults else ""
  return f"motor {motor} moved, status 0x{status:04x}{fault_text}: {first}, {second}, {third}"


def _describe_home_done(payload: bytes) -> str:
  """A home-done event, in words."""
  motor, first, second = decode_home_done(payload=payload)
  return f"motor {motor} homed: {first}, {second}"


def _describe_motor_error(payload: bytes) -> str:
  """A motor-error event, in words."""
  motor, code = decode_motor_error(payload=payload)
  meaning = MOTOR_ERROR_CODE_DESCRIPTIONS.get(code, "a code not seen before")
  return f"motor {motor} reported error {code}: {meaning}"


def _describe_sequence_progress(payload: bytes) -> str:
  """A sequence-progress event, in words."""
  item, queued, free = decode_sequence_progress(payload=payload)
  return f"item {item} played; {queued} queued, {free} free"


def _describe_sequence_stopped(payload: bytes) -> str:
  """A sequence-stopped event, in words."""
  item, unprocessed = decode_sequence_stopped(payload=payload)
  return f"sequencer stopped at item {item}, second word {unprocessed}"


def _describe_input_changed(payload: bytes) -> str:
  """An input-changed event, in words."""
  input_number, on = decode_input_changed(payload=payload)
  return f"input {input_number} {'on' if on else 'off'}"


# How each event is said in a log message.
_EVENT_DESCRIPTIONS: Dict[int, Callable[..., str]] = {
  MOTION_STARTED_EVENT: _describe_motion_started,
  MOVE_DONE_EVENT: _describe_move_done,
  HOME_DONE_EVENT: _describe_home_done,
  MOTOR_ERROR_EVENT: _describe_motor_error,
  SEQUENCE_PROGRESS_EVENT: _describe_sequence_progress,
  SEQUENCE_STOPPED_EVENT: _describe_sequence_stopped,
  INPUT_CHANGED_EVENT: _describe_input_changed,
}


def describe_event(*, code: int, payload: bytes) -> str:
  """An event in words, for the log; a code not decoded here by its number and payload.

  Raises:
    FrameError: if a known event's payload does not match its layout.
  """
  describe = _EVENT_DESCRIPTIONS.get(code)
  if describe is None:
    return f"event 0x{code:04x}, not decoded: {payload.hex()}"
  return describe(payload=payload)

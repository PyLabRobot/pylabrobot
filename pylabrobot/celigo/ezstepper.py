"""AllMotion EZStepper command formatting for the Celigo motors.

The Celigo stage/Z/filter motors are AllMotion EZStepper/EZServo drivers. Commands are
ASCII strings tunneled through the USB-IO board's ``MOTOR_CMD_QUERY`` opcode (see
:meth:`pylabrobot.celigo.controller.CeligoController.send_motor_query`).

A command string is::

    "/" + <motor designation> + <one or more command tokens> + ["R"] + "\\r"

where each token is a letter code followed by its decimal argument, ``"R"`` (run) is
appended for everything except queries/terminate/reset, and ``"\\r"`` ends the packet.

Responses follow the AllMotion convention: after the ``"/0"`` reply prefix a status
byte encodes ready/busy (bit ``0x20``) and an error code (low nibble), followed by the
data payload.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Sequence, Tuple


class EZCommand(enum.Enum):
  """EZStepper command identifiers (value is unique; ASCII code is in :data:`CODE`)."""

  MOVE_ABSOLUTE = enum.auto()
  MOVE_POSITIVE = enum.auto()
  MOVE_NEGATIVE = enum.auto()
  HOME = enum.auto()
  SET_POSITION = enum.auto()
  SET_POLARITY = enum.auto()
  SET_POSITIVE_DIRECTION = enum.auto()
  SET_VELOCITY = enum.auto()
  SET_ACCELERATION = enum.auto()
  SET_MOVE_CURRENT = enum.auto()
  SET_HOLD_CURRENT = enum.auto()
  SET_CURRENT_PERCENTAGE = enum.auto()
  SET_MODE = enum.auto()
  SET_SPECIAL_MODE = enum.auto()
  SET_ENCODER_RATIO = enum.auto()
  SET_BACKLASH_COMPENSATION = enum.auto()
  SET_RESPONSE_TIME = enum.auto()
  SET_S_CURVE = enum.auto()
  SET_OVERLOAD_TIMEOUT = enum.auto()
  SET_COURSE_CORRECTION = enum.auto()
  SET_FINE_CORRECTION = enum.auto()
  SET_INTEGRATION_PERIOD = enum.auto()
  HALT = enum.auto()
  WAIT = enum.auto()
  TURN_DRIVER_ON_OFF = enum.auto()
  RUN = enum.auto()
  TERMINATE = enum.auto()
  QUERY = enum.auto()
  QUERY_FIRMWARE = enum.auto()
  QUERY_STATUS = enum.auto()


# ASCII letter codes (StringValue attributes in EZStepperMotorCommands).
CODE = {
  EZCommand.MOVE_ABSOLUTE: "A",
  EZCommand.MOVE_POSITIVE: "P",
  EZCommand.MOVE_NEGATIVE: "D",
  EZCommand.HOME: "Z",
  EZCommand.SET_POSITION: "z",
  EZCommand.SET_POLARITY: "f",
  EZCommand.SET_POSITIVE_DIRECTION: "F",
  EZCommand.SET_VELOCITY: "V",
  EZCommand.SET_ACCELERATION: "L",
  EZCommand.SET_MOVE_CURRENT: "m",
  EZCommand.SET_HOLD_CURRENT: "h",
  EZCommand.SET_CURRENT_PERCENTAGE: "l",
  EZCommand.SET_MODE: "n",
  EZCommand.SET_SPECIAL_MODE: "N",
  EZCommand.SET_ENCODER_RATIO: "aE",
  EZCommand.SET_BACKLASH_COMPENSATION: "K",
  EZCommand.SET_RESPONSE_TIME: "aP",
  EZCommand.SET_S_CURVE: "aj",
  EZCommand.SET_OVERLOAD_TIMEOUT: "au",
  EZCommand.SET_COURSE_CORRECTION: "aC",
  EZCommand.SET_FINE_CORRECTION: "ac",
  EZCommand.SET_INTEGRATION_PERIOD: "x",
  EZCommand.HALT: "H",
  EZCommand.WAIT: "M",
  EZCommand.TURN_DRIVER_ON_OFF: "J",
  EZCommand.RUN: "R",
  EZCommand.TERMINATE: "T",
  EZCommand.QUERY: "?",
  EZCommand.QUERY_FIRMWARE: "&",
  EZCommand.QUERY_STATUS: "Q",
}

COMMAND_START = "/"
COMMAND_END = "\r"
RUN = "R"

# Commands that must NOT have "R" (run) appended (GenerateSingleCommand's exclusion set).
_NO_RUN = frozenset(
  {
    EZCommand.TERMINATE,
    EZCommand.QUERY,
    EZCommand.QUERY_STATUS,
    EZCommand.QUERY_FIRMWARE,
  }
)

# Argument validation limits.
MIN_VELOCITY = 1
MAX_VELOCITY = 16_777_216
MAX_ACCELERATION = 65_000
MAX_MOVE = 2**31 - 1


class EZStepperQuery(enum.IntEnum):
  """Argument values for the ``"?"`` query command (``EZStepperQueries``)."""

  CURRENT_COMMANDED_POSITION = 0
  CURRENT_MAX_SPEED = 2
  STATUS_ALL_INPUTS = 4
  CURRENT_VELOCITY_MODE_SPEED = 5
  CURRENT_STEP_SIZE = 6
  CURRENT_O_VALUE = 7
  ENCODER_POSITION = 8
  SECOND_ENCODER = 10


class EZStepperMode(enum.IntFlag):
  """Bit flags for the ``"n"`` SetMode command (``EZStepperModes``)."""

  NONE = 0
  ENABLE_PULSE_JOG = 0x1
  ENABLE_LIMITS = 0x2
  ENABLE_CONTINUOUS_JOG = 0x4
  ENABLE_POSITION_CORRECTION = 0x8
  ENABLE_OVERLOAD_REPORT = 0x10
  ENABLE_STEP_AND_DIRECTION = 0x20
  ENABLE_MOTOR_SLAVE_TO_ENCODER = 0x40
  DISABLE_RESPONSE = 0x100


class EZStepperError(enum.IntEnum):
  """Low-nibble error codes in an EZStepper status byte (``EZStepperErrorCode``)."""

  NO_ERROR = 0
  INIT_ERROR = 1
  BAD_COMMAND = 2
  BAD_OPERAND = 3
  COMMUNICATION_ERROR = 5
  NOT_INITIALIZED = 7
  OVERLOAD_ERROR = 9
  MOVE_NOT_ALLOWED = 11
  COMMAND_OVERFLOW = 15


def motor_designation(axis_index: int) -> str:
  """Address character for a motor (``MotorDesignation``): "1".."9" for 1-9, else chr(48+i)."""
  if 0 < axis_index < 10:
    return str(axis_index)
  return chr(48 + axis_index)


def _token(command: EZCommand, argument: "int | None") -> str:
  code = CODE[command]
  return code if argument is None else f"{code}{argument}"


def _validate(command: EZCommand, argument: "int | None") -> None:
  if command in (EZCommand.MOVE_POSITIVE, EZCommand.MOVE_NEGATIVE):
    if argument is None or argument == 0:
      raise ValueError("Relative move argument cannot be 0 (infinite moves not allowed).")
    if not 0 < argument <= MAX_MOVE:
      raise ValueError(f"Relative move argument out of range: {argument}")
  elif command is EZCommand.MOVE_ABSOLUTE:
    # absolute targets may be negative (e.g. filter wheel "A-1980")
    if argument is None or not -MAX_MOVE <= argument <= MAX_MOVE:
      raise ValueError(f"MOVE_ABSOLUTE argument out of range: {argument}")
  elif command in (EZCommand.HOME, EZCommand.SET_POSITION):
    if argument is None or not 0 <= argument <= MAX_MOVE:
      raise ValueError(f"{command.name} argument out of range: {argument}")
  elif command is EZCommand.SET_VELOCITY:
    if argument is None or not MIN_VELOCITY <= argument <= MAX_VELOCITY:
      raise ValueError(f"Velocity out of range [{MIN_VELOCITY}, {MAX_VELOCITY}]: {argument}")
  elif command is EZCommand.SET_ACCELERATION:
    if argument is None or not 0 < argument <= MAX_ACCELERATION:
      raise ValueError(f"Acceleration out of range (0, {MAX_ACCELERATION}]: {argument}")
  elif command in (EZCommand.SET_POLARITY, EZCommand.SET_POSITIVE_DIRECTION):
    if argument not in (0, 1):
      raise ValueError(f"{command.name} argument must be 0 or 1: {argument}")


def single_command(command: EZCommand, argument: "int | None", axis_index: int) -> str:
  """Build a one-token command string (``GenerateSingleCommand``)."""
  _validate(command, argument)
  parts = [COMMAND_START, motor_designation(axis_index), _token(command, argument)]
  if command not in _NO_RUN:
    parts.append(RUN)
  parts.append(COMMAND_END)
  return "".join(parts)


def multi_command(commands: Sequence[Tuple[EZCommand, "int | None"]], axis_index: int) -> str:
  """Build a multi-token command string (``GenerateMultiCommand``).

  ``Terminate`` and ``Query`` may not be combined with other tokens. ``"R"`` (run) is
  appended unless the only token is ``Terminate``.
  """
  if not commands:
    raise ValueError("At least one command is required.")
  for command, argument in commands:
    if command in (EZCommand.TERMINATE, EZCommand.QUERY) and len(commands) > 1:
      raise ValueError(f"{command.name} cannot be combined with other commands.")
    _validate(command, argument)
  parts = [COMMAND_START, motor_designation(axis_index)]
  parts += [_token(c, a) for c, a in commands]
  if not (len(commands) == 1 and commands[0][0] is EZCommand.TERMINATE):
    parts.append(RUN)
  parts.append(COMMAND_END)
  return "".join(parts)


# -- convenience builders for common motions --------------------------------


def move_absolute(axis_index: int, position: int) -> str:
  return single_command(EZCommand.MOVE_ABSOLUTE, position, axis_index)


def move_relative(axis_index: int, steps: int) -> str:
  if steps >= 0:
    return single_command(EZCommand.MOVE_POSITIVE, steps, axis_index)
  return single_command(EZCommand.MOVE_NEGATIVE, -steps, axis_index)


def home(axis_index: int, argument: int = 0) -> str:
  return single_command(EZCommand.HOME, argument, axis_index)


def set_velocity(axis_index: int, velocity: int) -> str:
  return single_command(EZCommand.SET_VELOCITY, velocity, axis_index)


def query_encoder_position(axis_index: int) -> str:
  return single_command(EZCommand.QUERY, int(EZStepperQuery.ENCODER_POSITION), axis_index)


def query_commanded_position(axis_index: int) -> str:
  return single_command(EZCommand.QUERY, int(EZStepperQuery.CURRENT_COMMANDED_POSITION), axis_index)


def terminate(axis_index: int) -> str:
  return single_command(EZCommand.TERMINATE, None, axis_index)


STX = "\x02"
ETX = "\x03"


def to_oem_packet(command: str) -> bytes:
  """Wrap a ``/<addr><tokens>R\\r`` command in the AllMotion OEM frame.

  The frame is ``STX + <addr> + '1' + <tokens> + ETX`` followed by a one-byte XOR
  checksum over all those bytes (including STX and ETX). ``<addr>`` is the motor
  designation ('1'=X, '2'=Y, '3'=Z/focus, '4'=filter); the literal ``'1'`` after it
  is the device sub-index. (The ``MOTOR_CMD_QUERY_WLEN`` opcode 47 path uses this frame.)
  """
  slash = command.rfind(COMMAND_START)
  if slash < 0:
    raise ValueError(f"Command missing '/': {command!r}")
  rest = command[slash + 1 :]
  end = rest.find(COMMAND_END)
  if end <= 0:
    raise ValueError(f"Command missing terminator: {command!r}")
  addr = rest[0]
  tokens = rest[1:end]
  body = f"{STX}{addr}1{tokens}{ETX}".encode("ascii")
  checksum = 0
  for b in body:
    checksum ^= b
  return body + bytes([checksum])


def from_oem_response(raw: str) -> str:
  """Unwrap an OEM-framed device reply into a plain ``/<content>`` string.

  Extracts the bytes between STX and ETX. The caller can
  then :func:`parse_response` the result. Returns ``raw`` unchanged if it isn't OEM-framed.
  """
  start = raw.rfind(STX)
  if start < 0:
    return raw
  end = raw.find(ETX, start)
  if end < 0:
    return raw
  content = raw[start + 1 : end]
  return f"{COMMAND_START}{content}"


@dataclass
class EZStepperResponse:
  """Parsed EZStepper reply: ready/busy flag, error code, and the data payload."""

  ready: bool
  error: EZStepperError
  data: str
  raw: str

  @property
  def ok(self) -> bool:
    return self.error == EZStepperError.NO_ERROR


# AllMotion status byte: 0x40 base, 0x20 = ready, low nibble = error code.
_STATUS_READY_BIT = 0x20
_STATUS_ERROR_MASK = 0x0F


def parse_response(raw: str) -> EZStepperResponse:
  """Parse an AllMotion reply string into :class:`EZStepperResponse`.

  The reply contains a ``"/0"`` master-address prefix followed by a status byte and the
  data, optionally wrapped in control characters. We locate the status byte right after
  the ``"/0"`` prefix (falling back to the first byte with the 0x40 base set).
  """
  text = raw
  idx = text.find("/0")
  if idx >= 0 and idx + 2 < len(text):
    status_pos: int = idx + 2
  else:
    found = next((i for i, ch in enumerate(text) if ord(ch) & 0x40), None)
    if found is None:
      raise ValueError(f"No EZStepper status byte found in response: {raw!r}")
    status_pos = found
  status = ord(text[status_pos])
  ready = bool(status & _STATUS_READY_BIT)
  error_code = status & _STATUS_ERROR_MASK
  try:
    error = EZStepperError(error_code)
  except ValueError:
    error = EZStepperError.NO_ERROR
  # data is everything after the status byte up to a terminating ETX/CR if present.
  data = text[status_pos + 1 :]
  for term in ("\x03", "\r", "\n"):
    cut = data.find(term)
    if cut >= 0:
      data = data[:cut]
  return EZStepperResponse(ready=ready, error=error, data=data, raw=raw)

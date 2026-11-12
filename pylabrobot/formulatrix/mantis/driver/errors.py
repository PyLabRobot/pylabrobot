"""Errors of the Mantis, one class per kind, and what the device's codes mean.

Catch ``MantisError`` for any of them. Each also subclasses the built-in it corresponds to, so an
``except RuntimeError``, ``ValueError`` or ``TimeoutError`` still matches.
"""

from typing import Dict, Optional


class MantisError(Exception):
  """Any Mantis error."""


class FrameError(MantisError, RuntimeError):
  """A frame from the device is malformed."""


class ChecksumError(FrameError):
  """A frame's check word does not match its bytes."""


class CommandRefusedError(MantisError, RuntimeError):
  """The device answered a command with a status other than 0."""

  def __init__(self, message: str, *, board: int, command: int, status: int):
    """
    Args:
      message: what went wrong, with the device's own message when it sent one.
      board: the board the command went to.
      command: the command number.
      status: the reply's status, signed; negative is a device error.
    """
    super().__init__(message)
    self.board = board
    self.command = command
    self.status = status


class MantisNotConnectedError(MantisError, RuntimeError):
  """A command needs the link, but setup has not run or stop has."""


class MantisConnectionLostError(MantisError, RuntimeError):
  """Reading a link failed; the failure is the cause. Setup runs again to reopen it."""


class MantisTimeoutError(MantisError, TimeoutError):
  """The device did not answer, or did not reach a state, in time."""


class MantisConfigurationError(MantisError, ValueError):
  """A declared configuration disagrees with the device, lacks a value, or names an unknown slot."""


class PlateClampError(MantisError, RuntimeError):
  """The plate clamp read a failure state (``CLAMP_STATE_DESCRIPTIONS``)."""

  def __init__(self, message: str, *, state: int):
    """
    Args:
      message: what went wrong.
      state: the clamp state read, 4, 7 or 8.
    """
    super().__init__(message)
    self.state = state


class MantisMotionError(MantisError, RuntimeError):
  """A motor reported a fault or did not reach a commanded state."""

  def __init__(
    self,
    message: str,
    *,
    motor: Optional[int] = None,
    code: Optional[int] = None,
    status: Optional[int] = None,
  ):
    """
    Args:
      message: what went wrong.
      motor: the motor, when known.
      code: the motor error code (``MOTOR_ERROR_CODE_DESCRIPTIONS``), when the device sent one.
      status: the motor status word, when the fault was read from it (``MOTOR_STATUS_ERROR_BITS``).
    """
    super().__init__(message)
    self.motor = motor
    self.code = code
    self.status = status


class MantisPressureError(MantisError, RuntimeError):
  """A pressure controller did not reach its target, or a sensor raised an alarm."""

  def __init__(
    self,
    message: str,
    *,
    sensor: Optional[int] = None,
    code: Optional[int] = None,
    value: Optional[float] = None,
  ):
    """
    Args:
      message: what went wrong.
      sensor: the sensor, when known.
      code: the alarm code (``SENSOR_ALARM_CODE_DESCRIPTIONS``), for a sensor alarm.
      value: the sensor's value, when the device sent one.
    """
    super().__init__(message)
    self.sensor = sensor
    self.code = code
    self.value = value


class InvalidQueueItemError(MantisError, RuntimeError):
  """A sequencer queue command answered a non-zero error field."""

  def __init__(self, message: str, *, command: int, error: int):
    """
    Args:
      message: what went wrong.
      command: the queue command number.
      error: the error field it answered.
    """
    super().__init__(message)
    self.command = command
    self.error = error


class MantisKinematicsError(MantisError, ValueError):
  """A target position is out of the arm's reach or at a singularity."""


class MantisCoordinateError(MantisError, ValueError):
  """A container cannot be mapped to a device position."""


class MantisPrimeError(MantisError, RuntimeError):
  """A prime ran its full volume, but the liquid sensor never confirmed flow."""


# The motor error codes a motor command answers, or a motor-error event carries.
MOTOR_ERROR_CODE_DESCRIPTIONS: Dict[int, str] = {
  1: "the motor id is not registered",
  2: "below the motor's lower limit",
  3: "above the motor's upper limit",
  4: "the position cannot be reached",
  5: "the velocity is too low or too high",
  6: "the acceleration is too low or too high",
  7: "the motor aborted",
  8: "a skip was detected while the motor was idle",
  9: "a skip was detected while the motor was moving",
  10: "the encoder failed",
  11: "the motor is disabled",
  12: "the motor is homing",
  13: "the motor current is unstable",
}

# The motor status word's fault bits, in the order they are reported when several are set.
MOTOR_STATUS_ERROR_BITS: Dict[int, str] = {
  32: "over current",
  64: "aborted",
  128: "following error while idle",
  256: "following error while moving",
  512: "encoder error",
  1024: "unstable current",
}

# The alarm codes of a sensor-alarm event.
SENSOR_ALARM_CODE_DESCRIPTIONS: Dict[int, str] = {
  0: "below the limit, set",
  1: "below the limit, cleared",
  2: "above the limit, set",
  3: "above the limit, cleared",
}

# The plate clamp's states, as it reads them.
CLAMP_STATE_DESCRIPTIONS: Dict[int, str] = {
  0: "idle",
  1: "stopped",
  2: "clamping",
  3: "clamping done",
  4: "clamping error timeout",
  5: "unclamping",
  6: "unclamping done",
  7: "unclamping error timeout",
  8: "motor fault",
}

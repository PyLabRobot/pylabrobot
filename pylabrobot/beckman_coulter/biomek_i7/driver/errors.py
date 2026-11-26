"""Biomek i7 error types, and the reply bytes that raise them."""

from typing import Dict, List, Mapping, Optional, Tuple

# ACE error codes, the byte after a reply's status. The Master Controller names more, but their
# numbers are not guessed.
ACE_ERROR_NAMES: Dict[int, str] = {
  0x0F: "interlock tripped",
  0x30: "motion device not configured",
  0x31: "motion device not enabled",
  0x32: "motion device not homed",
  0x38: "coordinated motion failed",
  0x3C: "motion error window exceeded",
}

# Device error codes: the drive's own reason, after the ACE error code of a failed move, and in an
# EMCY message. Each one here was seen in a reply or an EMCY message.
DEVICE_ERROR_NAMES: Dict[int, str] = {
  0x0100: "motion error window exceeded",
  0x0407: "interlock tripped",
  0x0416: "interlock active",
  0x0431: "servo home aborted",
  0x0437: "stepper glitch",
}


class FrameError(Exception):
  """Bytes from a board that do not split into whole frames."""


class AceError(Exception):
  """A board refused a command or reported that it failed.

  Attributes:
    code: the ACE error code.
    device_error: the drive's own error code, when the reply carries one.
    request: a short description of the command that failed.
    axis_errors: for a failed Move All, the errors the axes that failed reported.
  """

  def __init__(
    self,
    code: int,
    device_error: Optional[int],
    request: str,
    device: Optional[int] = None,
    index: Optional[int] = None,
  ):
    self.code = code
    self.device_error = device_error
    self.request = request
    # The device and index the reply came from, when known: which axis failed.
    self.device = device
    self.index = index
    self.axis_errors: List["AceError"] = []
    self.reason = ACE_ERROR_NAMES.get(code, f"ACE error 0x{code:02X}")
    name = ACE_ERROR_NAMES.get(code, f"ACE error 0x{code:02X}")
    detail = ""
    if device_error is not None:
      device_name = DEVICE_ERROR_NAMES.get(device_error)
      detail = f" (device error 0x{device_error:X}" + (f": {device_name})" if device_name else ")")
    super().__init__(f"{request}: {name}{detail}")


class InterlockTrippedError(AceError):
  """The interlock tripped (light curtain broken, or tripped by the host), so the board stopped or
  refused the move."""


class NotConfiguredError(AceError):
  """The axis has no motor table loaded: the board was powered up, or the drive lost its
  parameters, since the last Configure."""


class NotEnabledError(AceError):
  """The axis is configured but disabled (Set State ENABLED has not been sent since)."""


class NotHomedError(AceError):
  """The axis has not been homed since the board started."""


class MotionError(AceError):
  """A move did not complete: the axis left its error window, or a coordinated move failed."""


# The ACE error a board answers when the interlock is tripped.
INTERLOCK_TRIPPED_CODE = 0x0F

_ERROR_CLASSES = {
  INTERLOCK_TRIPPED_CODE: InterlockTrippedError,
  0x30: NotConfiguredError,
  0x31: NotEnabledError,
  0x32: NotHomedError,
  0x38: MotionError,
  0x3C: MotionError,
}


def ace_error(
  code: int,
  device_error: Optional[int],
  request: str,
  device: Optional[int] = None,
  index: Optional[int] = None,
) -> AceError:
  """Build the exception for an ACE error code."""
  return _ERROR_CLASSES.get(code, AceError)(code, device_error, request, device, index)


# The errors the Master Controller reports as one axis's motion failing (not enabled, homing failed, operational parameters exceeded, error window exceeded, framing
# error, controller error; only those with a known number here).
_MOTION_FAILED_CODES = {0x31, 0x3C}
COORDINATED_MOTION_FAILED_CODE = 0x38


def english(error: AceError, axis_names: Mapping[Tuple[int, int], str]) -> str:
  """What the Master Controller replies for a command that failed with `error`, as its logs show
  it, with each axis named by `axis_names` ((device, index) ->
  name):

  - not homed: "The instrument needs to be homed.";
  - interlock tripped: "A light curtain violation has occurred.";
  - a coordinated move failed: "The <motor> motion failed: <error>." for each axis that reported;
  - an axis's own motion error: the same, for that axis;
  - anything else: "<command> failed: <error>".
  """

  def motion_failed(e: AceError) -> str:
    name = axis_names.get((e.device, e.index)) if e.device is not None else None  # type: ignore[arg-type]
    return f"The {name or e.request} motion failed: {e.reason}."

  if isinstance(error, NotHomedError):
    return "The instrument needs to be homed."
  if isinstance(error, InterlockTrippedError):
    return "A light curtain violation has occurred."
  if error.code == COORDINATED_MOTION_FAILED_CODE and error.axis_errors:
    return " ".join(motion_failed(e) for e in error.axis_errors)
  if error.code in _MOTION_FAILED_CODES and error.device is not None:
    return motion_failed(error)
  return f"{error.request} failed: {error.reason}."


class TipCheckError(RuntimeError):
  """A probe's tip is not what was expected, or cannot be determined: the Master Controller's
  `VT`."""

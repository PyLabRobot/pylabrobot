"""Turning an error code into something a caller can act on.

An instrument reports failure as a number. :func:`raise_for_status` turns that number into an
exception, so a failure cannot be dropped by not looking at a return value, and the exception's
class says what kind of thing went wrong -- which is what decides the caller's next move. A
:class:`LinkError` is worth retrying, a :class:`MotorError` needs a person, and a
:class:`RejectedError` means the request asks for something this instrument cannot do and will
fail again identically until the request or the instrument's configuration changes.

Zero is success: it is the instrument's own "no instrument error".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.error_handling.error_codes import (
  FAMILY_MESSAGES,
  LINK_FAULTS,
  LINK_NAMES,
  MESSAGES,
  MOTOR_NAMES,
  UNNAMED_MOTOR,
)

NO_ERROR = 0x0000
UNKNOWN_CODE = "No description available."

# The link failures this package detects on the host side, named so that a caller raising one does
# not have to carry a number of its own.
WRITE_FAILED = 0x6045
NOT_ACKNOWLEDGED = 0x6048
REPLY_TIMED_OUT = 0x6053
PORT_WOULD_NOT_OPEN = 0x6058

# What the host finds in the firmware version record itself, rather than in a reply's status.
WRONG_BASECODE_PART_NUMBER = 0x6002
SETTINGS_DATA_TOO_OLD = 0x6003

_LINK_FAULT_RANGE = (0x8100, 0x81FF)
_CODE_MASK = 0xFFFF
_HEX_FROM = 256


class ErrorKind(enum.Enum):
  """What kind of thing went wrong.

  The code space is laid out by subsystem, so the code itself says which one failed. See
  :data:`KIND_RANGES` for the mapping.
  """

  NONE = "no error"
  ABORTED = "the task was stopped"
  LINK = "the message did not get through"
  MOTOR = "a motor or axis fault"
  SENSOR = "a sensor or calibration-data fault"
  FLUIDICS = "a syringe, pump, valve or buffer fault"
  STORAGE = "on-board protocol storage"
  FIRMWARE = "bootcode, processor or power supply"
  REJECTED = "the request was refused as invalid"
  SERVICE_TEST = "a manifold verification result"
  UNKNOWN = "not in the table"


# (first, last, kind), inclusive, in the order :func:`classify` tries them. The 0x60xx block is the
# one that mixes concerns: 0x6040-0x6062 and 0x6065 are the serial link, while the rest of the
# block is request validation -- an out-of-range field and "this needs hardware that is not
# fitted" side by side.
KIND_RANGES: tuple[tuple[int, int, ErrorKind], ...] = (
  (0x0000, 0x0000, ErrorKind.NONE),
  (0x0100, 0x0100, ErrorKind.ABORTED),
  (0x0200, 0x07FF, ErrorKind.MOTOR),
  (0x0900, 0x0900, ErrorKind.SENSOR),
  (0x0A00, 0x0A00, ErrorKind.REJECTED),
  (0x0C00, 0x0CFF, ErrorKind.SENSOR),
  (0x1000, 0x12FF, ErrorKind.FIRMWARE),
  (0x1300, 0x15FF, ErrorKind.FLUIDICS),
  (0x1600, 0x16FF, ErrorKind.STORAGE),
  (0x1700, 0x17FF, ErrorKind.SENSOR),
  (0x2400, 0x2400, ErrorKind.REJECTED),
  (0x4000, 0x40FF, ErrorKind.STORAGE),
  (0x6000, 0x6001, ErrorKind.LINK),
  (0x6002, 0x6003, ErrorKind.FIRMWARE),
  (0x6004, 0x603F, ErrorKind.REJECTED),
  (0x6040, 0x6062, ErrorKind.LINK),
  (0x6065, 0x6065, ErrorKind.LINK),
  (0x6063, 0x610F, ErrorKind.REJECTED),
  (0x6110, 0x615F, ErrorKind.SERVICE_TEST),
  (0x6160, 0x61FF, ErrorKind.REJECTED),
  (0x8100, 0x81FF, ErrorKind.LINK),
  (0xA000, 0xA7FF, ErrorKind.FIRMWARE),
)


def normalize(code: int) -> int:
  """The code as an unsigned 16-bit value.

  Args:
    code: A code, which may have been read as a signed 16-bit integer.

  Returns:
    The same code, unsigned.
  """
  return code & _CODE_MASK if code < 0 else code


def classify(code: int) -> ErrorKind:
  """Which subsystem a code belongs to.

  Args:
    code: The code to classify.

  Returns:
    The kind of failure, or :attr:`ErrorKind.UNKNOWN` for a code in no known range.
  """
  code = normalize(code)
  for first, last, kind in KIND_RANGES:
    if first <= code <= last:
      return kind
  return ErrorKind.UNKNOWN


def error_message(code: int, family: InstrumentFamily = InstrumentFamily.EL406) -> str:
  """The instrument's sentence for a code.

  The family matters because a motion fault's low nibble is a motor number, and the motors differ
  per family.

  Args:
    code: The code to look up.
    family: The family of the instrument that reported it.

  Returns:
    The message, or an empty string for a code with none.
  """
  code = normalize(code)
  first, last = _LINK_FAULT_RANGE
  if first <= code <= last:
    fault = LINK_FAULTS.get(code & 0x0F)
    if fault is None:
      return ""
    link = LINK_NAMES.get(code & 0xF0)
    return f"{link}\r\n{fault}" if link else fault
  override = FAMILY_MESSAGES.get((family, code))
  if override is not None:
    return override
  message = MESSAGES.get(code)
  if message is None:
    return ""
  names = MOTOR_NAMES[family]
  number = code & 0x0F
  motor = names[number] if number < len(names) else UNNAMED_MOTOR
  return message.replace("{motor}", motor)


def describe(
  code: int, family: InstrumentFamily = InstrumentFamily.EL406, context: str = ""
) -> str:
  """A code and its message as one block of text.

  Args:
    code: The code to describe.
    family: The family of the instrument that reported it.
    context: What was being attempted, appended when given.

  Returns:
    The code, its message, and the context. Codes are written in hex above 255 and decimal below,
    since the larger ones are bit fields and only read as hex.
  """
  number = f"{code:X}" if code >= _HEX_FROM else str(code)
  message = error_message(code, family) or UNKNOWN_CODE
  return f"Error code: {number}\r\n{message}" + (f"\r\n{context}" if context else "")


@dataclass(frozen=True)
class ErrorInfo:
  """Everything known about one failure.

  Attributes:
    code: The code reported, unsigned. Zero when this package found the failure itself and has no
      code behind it.
    kind: Which subsystem failed.
    message: The instrument's sentence for the code, or an empty string when it has none.
    operation: What was being attempted, for a caller that supplied it.
  """

  code: int
  kind: ErrorKind
  message: str
  operation: str = ""

  def __str__(self) -> str:
    """The failure as one line.

    Returns:
      What was being attempted, what went wrong, and the kind and code it came from.
    """
    where = f"{self.operation}: " if self.operation else ""
    detail = self.message or UNKNOWN_CODE
    return f"{where}{detail} [{self.kind.value}, code {self.code:#06x}]"


class BiotekError(RuntimeError):
  """A failure the instrument or this package reported.

  Args:
    info: What is known about the failure.
  """

  def __init__(self, info: ErrorInfo):
    super().__init__(str(info))
    self.info = info

  @property
  def code(self) -> int:
    """The code reported, unsigned."""
    return self.info.code

  @property
  def kind(self) -> ErrorKind:
    """Which subsystem failed."""
    return self.info.kind

  @property
  def message(self) -> str:
    """The instrument's sentence for the code."""
    return self.info.message

  @property
  def operation(self) -> str:
    """What was being attempted."""
    return self.info.operation


class LinkError(BiotekError):
  """The message did not reach the instrument, or its reply did not come back intact.

  Whether the instrument acted on the message is unknown, which makes this the one kind where
  repeating the same request is reasonable.
  """


class AbortedError(BiotekError):
  """The task was stopped, either by a request to stop it or from the instrument's keypad."""


class MotorError(BiotekError):
  """A motor did not reach or hold a position. Needs a person, not a retry."""


class SensorError(BiotekError):
  """A sensor did not read, or its calibration data is missing or out of range."""


class FluidicsError(BiotekError):
  """A syringe, pump, valve or buffer fault.

  Includes the conditions an operator can clear, such as waste bottles that must be emptied or an
  absent buffer: they are faults of the fluid path rather than of the instrument.
  """


class StorageError(BiotekError):
  """On-board protocol storage: out of space, locked, not found, or a bad checksum."""


class FirmwareError(BiotekError):
  """Bootcode, processor, power supply, or basecode that is the wrong part or too old."""


class RejectedError(BiotekError):
  """The instrument refused the request as invalid.

  Either a field is out of range or the request needs hardware that is not fitted. This is the one
  kind that is a fault of the request rather than a condition of the instrument, so it will fail
  again identically until the request or the instrument's configuration changes.
  """


class ServiceTestError(BiotekError):
  """A manifold verification test did not pass, or could not be run."""


EXCEPTIONS: dict[ErrorKind, type[BiotekError]] = {
  ErrorKind.ABORTED: AbortedError,
  ErrorKind.LINK: LinkError,
  ErrorKind.MOTOR: MotorError,
  ErrorKind.SENSOR: SensorError,
  ErrorKind.FLUIDICS: FluidicsError,
  ErrorKind.STORAGE: StorageError,
  ErrorKind.FIRMWARE: FirmwareError,
  ErrorKind.REJECTED: RejectedError,
  ErrorKind.SERVICE_TEST: ServiceTestError,
}


def info_for(
  code: int, family: InstrumentFamily = InstrumentFamily.EL406, operation: str = ""
) -> ErrorInfo:
  """Everything known about a code.

  Args:
    code: The code reported.
    family: The family of the instrument that reported it.
    operation: What was being attempted.

  Returns:
    The assembled :class:`ErrorInfo`.
  """
  code = normalize(code)
  return ErrorInfo(code, classify(code), error_message(code, family), operation)


def for_info(info: ErrorInfo) -> BiotekError:
  """The exception that goes with a failure.

  Args:
    info: What is known about the failure.

  Returns:
    An exception of the class matching ``info.kind``, already carrying ``info``. Not raised.
  """
  return EXCEPTIONS.get(info.kind, BiotekError)(info)


def raise_for_status(
  code: int, family: InstrumentFamily = InstrumentFamily.EL406, operation: str = ""
) -> None:
  """Raise unless a code means success.

  Args:
    code: The code reported.
    family: The family of the instrument that reported it.
    operation: What was being attempted, included in the message.

  Raises:
    BiotekError: A subclass matching what failed, unless ``code`` is zero.
  """
  if normalize(code) == NO_ERROR:
    return
  raise for_info(info_for(code, family, operation))


def fail(kind: ErrorKind, message: str, operation: str = "", code: int = NO_ERROR) -> BiotekError:
  """The exception for a failure this package found itself.

  Args:
    kind: Which subsystem failed.
    message: What went wrong.
    operation: What was being attempted.
    code: The code behind it, when there is one. Zero means there is none.

  Returns:
    An exception of the class matching ``kind``. Not raised.
  """
  return for_info(ErrorInfo(code, kind, message, operation))

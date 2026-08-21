"""Gemini protocol-level errors.

Three errors can arise purely from the wire exchange, independent of what a
command was trying to do:

- :class:`GeminiTimeoutError` -- no response arrived within the per-request timeout.
- :class:`NAKError` -- the controller returned a ``*_ERR_RESP`` packet.
- :class:`MultipacketError` -- one packet in a multipacket batch was NAK'd.

A NAK code is also mapped onto
:class:`~pylabrobot.agilent.bravo.errors.BravoError`
(:attr:`~pylabrobot.agilent.bravo.errors.ErrorType.DARWIN_GENERIC`) via
:func:`nak_to_bravo_error`, so callers that only handle the shared error type
still see these failures.
"""

from __future__ import annotations

from typing import Optional

from ...errors import BravoError, ErrorType
from .enums import CommandNAKTypes


class GeminiProtocolError(Exception):
  """Base class for protocol-level errors raised by the Gemini engine."""


class GeminiTimeoutError(GeminiProtocolError):
  """A request did not receive a matching response within its timeout.

  Attributes:
    timeout: The timeout that elapsed, in seconds, if known.
  """

  def __init__(self, message: str, *, timeout: Optional[float] = None):
    """Create a Gemini timeout error.

    Args:
      message: A description of which request timed out.
      timeout: The timeout that elapsed, in seconds.
    """
    super().__init__(message)
    self.timeout = timeout


class NAKError(GeminiProtocolError):
  """The controller returned an error-response packet (``*_ERR_RESP``).

  The ``cmd_val`` of the error packet holds the :class:`~.enums.CommandNAKTypes`
  code.

  Attributes:
    nak_code: The raw NAK code byte.
    nak: The decoded :class:`~.enums.CommandNAKTypes`, or ``None`` if
      ``nak_code`` does not match a known member.
    sub_command: The subcommand that was NAK'd, if known.
    dest_node: The controller-tree node address that NAK'd, if known.
    dest_dev: The device index within that node, if known.
  """

  def __init__(
    self,
    nak_code: int,
    *,
    sub_command: Optional[int] = None,
    dest_node: Optional[int] = None,
    dest_dev: Optional[int] = None,
  ):
    """Create a NAK error.

    Args:
      nak_code: The raw NAK code byte from the error-response packet.
      sub_command: The subcommand that was NAK'd, if known.
      dest_node: The controller-tree node address that NAK'd, if known.
      dest_dev: The device index within that node, if known.
    """
    self.nak_code = nak_code
    try:
      self.nak: Optional[CommandNAKTypes] = CommandNAKTypes(nak_code)
      nak_name = self.nak.name
    except ValueError:
      self.nak = None
      nak_name = f"UNKNOWN_NAK_{nak_code}"
    self.sub_command = sub_command
    self.dest_node = dest_node
    self.dest_dev = dest_dev
    location = ""
    if dest_node is not None:
      location = f" at node {dest_node}"
      if dest_dev:
        location += f".{dest_dev}"
    sub = f" subcmd={sub_command}" if sub_command is not None else ""
    super().__init__(f"Gemini NAK {nak_name}{location}{sub}")


class MultipacketError(GeminiProtocolError):
  """A multipacket batch was rejected: one of its packets was NAK'd.

  Attributes:
    nak_code: The raw NAK code byte.
    nak: The decoded :class:`~.enums.CommandNAKTypes`, or ``None`` if
      ``nak_code`` does not match a known member.
    error_device_addr: Address byte of the device that NAK'd.
    num_exchanges: How many packets in the batch the controller accepted
      before the NAK.
  """

  def __init__(
    self,
    nak_code: int,
    error_device_addr: int,
    num_exchanges: int,
  ):
    """Create a multipacket error.

    Args:
      nak_code: The raw NAK code byte from the multipacket response.
      error_device_addr: Address byte of the device that NAK'd.
      num_exchanges: How many packets in the batch the controller accepted
        before the NAK.
    """
    self.nak_code = nak_code
    try:
      self.nak: Optional[CommandNAKTypes] = CommandNAKTypes(nak_code)
      nak_name = self.nak.name
    except ValueError:
      self.nak = None
      nak_name = f"UNKNOWN_NAK_{nak_code}"
    self.error_device_addr = error_device_addr
    self.num_exchanges = num_exchanges
    super().__init__(
      f"Gemini multipacket NAK {nak_name} at device 0x{error_device_addr:02X} "
      f"(after {num_exchanges} exchanges)"
    )


# --- NAK -> BravoError bridge --------------------------------------------------

_NAK_TO_BRAVO: dict[int, ErrorType] = {
  CommandNAKTypes.INVALID_SUBCMD: ErrorType.COULD_NOT_SEND_COMMAND,
  CommandNAKTypes.INVALID_DEVICE: ErrorType.CONTROLLER_UNIDENTIFIED,
  CommandNAKTypes.OUT_OF_RANGE: ErrorType.INVALID_DEST,
  CommandNAKTypes.READ_ONLY: ErrorType.COULD_NOT_SEND_COMMAND,
  CommandNAKTypes.WRITE_ONLY: ErrorType.COULD_NOT_SEND_COMMAND,
  CommandNAKTypes.INSTR_TBL_FULL: ErrorType.CONTROLLER_QUEUE,
  CommandNAKTypes.PLATE_DETECT_NOT_AVAILABLE: ErrorType.DARWIN_GENERIC,
  CommandNAKTypes.BRAKE_NOT_AVAILABLE: ErrorType.CONTROLLER_BRAKE,
  CommandNAKTypes.FLASH_PROTECTED: ErrorType.DARWIN_GENERIC,
  CommandNAKTypes.UNSUCCESSFUL_OPERATION: ErrorType.DARWIN_GENERIC,
  CommandNAKTypes.MOVE_IN_PROGRESS: ErrorType.MOVE_POSITION,
}


def nak_to_bravo_error(
  nak_code: int,
  *,
  sub_command: Optional[int] = None,
  extra: Optional[str] = None,
) -> BravoError:
  """Translate a NAK code into a :class:`~pylabrobot.agilent.bravo.errors.BravoError`.

  The specific NAK name is preserved as the error's custom text, so a caller
  that only handles the shared error hierarchy still sees which NAK fired.

  Args:
    nak_code: The raw NAK code byte.
    sub_command: The subcommand that was NAK'd, if known.
    extra: Additional context to append to the error text.

  Returns:
    The translated error.
  """
  error_type = _NAK_TO_BRAVO.get(nak_code, ErrorType.DARWIN_GENERIC)
  try:
    name = CommandNAKTypes(nak_code).name
  except ValueError:
    name = f"UNKNOWN_NAK_{nak_code}"
  bits = [f"Gemini NAK {name}"]
  if sub_command is not None:
    bits.append(f"subcmd={sub_command}")
  if extra:
    bits.append(extra)
  return BravoError(error_type, custom_text=" ".join(bits))

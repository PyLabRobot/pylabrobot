"""Gemini protocol error types.

Three protocol-level errors:
    GeminiTimeoutError      — no response within the per-request timeout
    NAKError                — controller returned a ``*_ERR_RESP`` packet
    MultipacketError        — a packet in a multipacket batch was NAK'd

A NAK code is also mapped into the existing :class:`pybravo.protocol.errors.BravoError`
(``ErrorType.DARWIN_GENERIC``) so upper layers that catch ``BravoError`` keep working.
"""

from __future__ import annotations

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import CommandNAKTypes


class GeminiProtocolError(Exception):
  """Base for protocol-level errors raised by the Gemini engine."""


class GeminiTimeoutError(GeminiProtocolError):
  """A request did not receive a matching response in time."""

  def __init__(self, message: str, *, timeout_ms: int | None = None):
    super().__init__(message)
    self.timeout_ms = timeout_ms


class NAKError(GeminiProtocolError):
  """The controller returned an error-response packet (``*_ERR_RESP``).

  The ``cmd_val`` of the error packet holds the :class:`CommandNAKTypes` code.
  """

  def __init__(
    self,
    nak_code: int,
    *,
    sub_command: int | None = None,
    dest_node: int | None = None,
    dest_dev: int | None = None,
  ):
    self.nak_code = nak_code
    try:
      self.nak = CommandNAKTypes(nak_code)
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
  """A multipacket batch was rejected: one of its packets got NAK'd."""

  def __init__(
    self,
    nak_code: int,
    error_device_addr: int,
    num_exchanges: int,
  ):
    self.nak_code = nak_code
    try:
      self.nak = CommandNAKTypes(nak_code)
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


# --- NAK → BravoError bridge --------------------------------------------------

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
  sub_command: int | None = None,
  extra: str | None = None,
) -> BravoError:
  """Translate a NAK code to a :class:`BravoError` for upper-layer surface.

  The underlying :class:`NAKError` detail is preserved as custom text so the
  user still sees which NAK was returned.
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

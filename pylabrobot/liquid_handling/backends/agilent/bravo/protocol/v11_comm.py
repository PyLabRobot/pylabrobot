"""V11DeviceComm protocol layer.

Implements the command framing, retry logic, and error parsing used between
the PC and the Rabbit microcontroller (Agile controller path).

Protocol (length-prefixed binary framing, matching C++ V11DeviceComm DLL):
  - Send: [length (2 bytes LE)] [command_id (1 byte)] [data payload (N bytes)]
      where length = 1 + N  (bytes after the length field)
  - Receive: [length (2 bytes LE)] [error_code (1 byte)] [response data (M bytes)]
      where length = 1 + M
  - Error code 0x00 = success; strip it and return response data
  - Up to MAX_COMMAND_RETRIES retries on communication failure
  - Default timeout: DEFAULT_COMMAND_TIMEOUT_MS (2000ms)
"""

from __future__ import annotations

import logging
import struct

from pylabrobot.liquid_handling.backends.agilent.bravo.logging_config import TRACE
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import (
  CommandID,
  DEFAULT_COMMAND_TIMEOUT_MS,
  MAX_COMMAND_RETRIES,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import (
  BravoError,
  ErrorType,
  RabbitErrorCode,
  rabbit_error_to_bravo_error,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.base import Transport

logger = logging.getLogger(__name__)

# V11 frame header: 2-byte little-endian length prefix
_LENGTH_HEADER_SIZE = 2
_LENGTH_HEADER_FMT = "<H"


class V11DeviceComm:
  """Communication layer wrapping a Transport with V11 command framing and retries.

  All commands are wrapped in a 2-byte LE length-prefixed frame before
  being sent to the Rabbit microcontroller.  Responses are deframed
  using the same length prefix.
  """

  def __init__(self, transport: Transport):
    self._transport = transport

  @property
  def transport(self) -> Transport:
    return self._transport

  def connect(self) -> None:
    self._transport.connect()

  def disconnect(self) -> None:
    self._transport.disconnect()

  @property
  def is_connected(self) -> bool:
    return self._transport.is_connected

  def send_command(
    self,
    command_id: CommandID,
    data: bytes = b"",
    timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS,
  ) -> bytes:
    """Send a command and return the response data (error byte stripped).

    Raises:
        BravoError: On hardware/protocol error.
        ConnectionError: If not connected.
        TimeoutError: If no response within timeout.
    """
    last_error: Exception | None = None

    for attempt in range(1, MAX_COMMAND_RETRIES + 1):
      try:
        return self._send_once(command_id, data, timeout_ms)
      except TimeoutError as exc:
        last_error = exc
        logger.warning(
          "Command 0x%02X attempt %d/%d timed out: %s",
          command_id,
          attempt,
          MAX_COMMAND_RETRIES,
          exc,
        )
      except ConnectionError as exc:
        last_error = exc
        logger.warning(
          "Command 0x%02X attempt %d/%d connection error: %s",
          command_id,
          attempt,
          MAX_COMMAND_RETRIES,
          exc,
        )

    raise BravoError(
      ErrorType.NO_RESPONSE,
      custom_text=f"Command 0x{command_id:02X} failed after {MAX_COMMAND_RETRIES} "
      f"retries: {last_error}",
    )

  def _send_once(
    self,
    command_id: CommandID,
    data: bytes,
    timeout_ms: int,
  ) -> bytes:
    """Single send/receive cycle with V11 length-prefix framing."""
    if not self._transport.is_connected:
      raise ConnectionError("Transport is not connected")

    # --- Build the V11 frame ---
    # Inner payload: [command_id (1 byte)][data (N bytes)]
    inner_payload = struct.pack("<B", int(command_id)) + data
    # Frame: [length (2 bytes LE)][inner_payload]
    frame_length = len(inner_payload)
    frame = struct.pack(_LENGTH_HEADER_FMT, frame_length) + inner_payload

    logger.debug(
      "TX cmd=0x%02X data_len=%d frame_len=%d",
      command_id,
      len(data),
      len(frame),
    )
    if logger.isEnabledFor(TRACE):
      logger.log(TRACE, "TX frame: %s", frame.hex())

    self._transport.send(frame)

    # --- Read the V11 response frame ---
    # First read the 2-byte length prefix
    length_bytes = self._transport.receive_exact(
      _LENGTH_HEADER_SIZE,
      timeout_ms,
    )
    (response_length,) = struct.unpack(_LENGTH_HEADER_FMT, length_bytes)

    if response_length == 0:
      raise BravoError(ErrorType.NO_RESPONSE)

    # Now read exactly response_length bytes of payload
    response_payload = self._transport.receive_exact(
      response_length,
      timeout_ms,
    )

    logger.debug(
      "RX response_length=%d payload_len=%d",
      response_length,
      len(response_payload),
    )
    if logger.isEnabledFor(TRACE):
      logger.log(TRACE, "RX frame: %s", (length_bytes + response_payload).hex())

    if len(response_payload) < 1:
      raise BravoError(ErrorType.NO_RESPONSE)

    error_code = response_payload[0]
    response_data = response_payload[1:]

    if error_code != RabbitErrorCode.NONE:
      raise rabbit_error_to_bravo_error(error_code)

    return response_data

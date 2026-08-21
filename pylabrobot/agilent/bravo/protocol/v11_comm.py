"""V11 command framing, retry, and error handling for the Rabbit microcontroller.

The Rabbit microcontroller on Agile-generation Bravo controllers speaks a
length-prefixed binary protocol over the transport:

- Send: ``[length (2 bytes LE)][command_id (1 byte)][data payload (N bytes)]``,
  where ``length = 1 + N`` (the byte count after the length field itself).
- Receive: ``[length (2 bytes LE)][error_code (1 byte)][response data (M bytes)]``,
  where ``length = 1 + M``.
- Error code 0x00 means success; any other value is a
  :class:`~pylabrobot.agilent.bravo.errors.RabbitErrorCode`, and the response
  data past it is discarded.
- A command is retried up to :data:`~.commands.MAX_COMMAND_RETRIES` times on a
  transport-level failure (timeout or disconnect); a rejected command
  (non-zero error code) is not retried, since resending the same command to
  the same faulted state is not expected to succeed.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

from pylabrobot.io import LOG_LEVEL_IO

from ..errors import BravoError, ErrorType, RabbitErrorCode, rabbit_error_to_bravo_error
from ..transport import Transport
from .commands import DEFAULT_COMMAND_TIMEOUT, MAX_COMMAND_RETRIES, CommandID

logger = logging.getLogger(__name__)

# V11 frame header: 2-byte little-endian length prefix.
_LENGTH_HEADER_SIZE = 2
_LENGTH_HEADER_FMT = "<H"


class V11DeviceComm:
  """Wraps a Transport with V11 length-prefixed command framing and retries.

  Every command is wrapped in a 2-byte little-endian length-prefixed frame
  before being sent to the Rabbit microcontroller; responses are deframed
  using the same length prefix.
  """

  def __init__(self, transport: Transport):
    """Bind this comm layer to a transport.

    Args:
      transport: The byte channel to the Rabbit microcontroller. Its
        connection lifecycle belongs to the caller.
    """
    self._transport = transport

  @property
  def transport(self) -> Transport:
    """The underlying transport, so a caller can reach it directly (e.g. to drain it)."""
    return self._transport

  @property
  def is_connected(self) -> bool:
    """Whether the underlying transport is currently connected."""
    return self._transport.is_connected

  def send_command(
    self,
    command_id: CommandID,
    data: bytes = b"",
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
  ) -> bytes:
    """Send a command and return its response data, with the error byte stripped.

    Args:
      command_id: The command to send.
      data: The command's payload bytes, if any.
      timeout: Maximum time to wait for each attempt's response, in seconds.

    Returns:
      The response payload, with the leading error-code byte removed.

    Raises:
      BravoError: If every attempt fails, or if the controller reports a
        hardware/protocol error.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_COMMAND_RETRIES + 1):
      try:
        return self._send_once(command_id, data, timeout)
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
      custom_text=(
        f"Command 0x{command_id:02X} failed after {MAX_COMMAND_RETRIES} retries: {last_error}"
      ),
    )

  def _send_once(
    self,
    command_id: CommandID,
    data: bytes,
    timeout: float,
  ) -> bytes:
    """Send and receive one V11 frame, without retrying.

    Args:
      command_id: The command to send.
      data: The command's payload bytes, if any.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The response payload, with the leading error-code byte removed.

    Raises:
      ConnectionError: If the transport is not connected.
      TimeoutError: If the response does not arrive within ``timeout``.
      BravoError: If the response is empty, or the controller reports an error.
    """
    if not self._transport.is_connected:
      raise ConnectionError("Transport is not connected")

    # --- Build the V11 frame: [length (2 bytes LE)][command_id][data] ---
    inner_payload = struct.pack("<B", int(command_id)) + data
    frame_length = len(inner_payload)
    frame = struct.pack(_LENGTH_HEADER_FMT, frame_length) + inner_payload

    logger.debug(
      "TX cmd=0x%02X data_len=%d frame_len=%d",
      command_id,
      len(data),
      len(frame),
    )
    if logger.isEnabledFor(LOG_LEVEL_IO):
      logger.log(LOG_LEVEL_IO, "TX frame: %s", frame.hex())

    self._transport.send(frame)

    # --- Read the V11 response frame ---
    length_bytes = self._transport.receive_exact(_LENGTH_HEADER_SIZE, timeout)
    (response_length,) = struct.unpack(_LENGTH_HEADER_FMT, length_bytes)

    if response_length == 0:
      raise BravoError(ErrorType.NO_RESPONSE)

    response_payload = self._transport.receive_exact(response_length, timeout)

    logger.debug(
      "RX response_length=%d payload_len=%d",
      response_length,
      len(response_payload),
    )
    if logger.isEnabledFor(LOG_LEVEL_IO):
      logger.log(LOG_LEVEL_IO, "RX frame: %s", (length_bytes + response_payload).hex())

    if len(response_payload) < 1:
      raise BravoError(ErrorType.NO_RESPONSE)

    error_code = response_payload[0]
    response_data = response_payload[1:]

    if error_code != RabbitErrorCode.NONE:
      raise rabbit_error_to_bravo_error(error_code)

    return response_data

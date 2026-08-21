"""V11 command framing for the Agile 7612 Bravo generation -- swapped frame order.

Standard V11 (:class:`~.v11_comm.V11DeviceComm`) sends
``[length_u16_LE][cmd][data]`` and receives ``[length_u16_LE][error][data]``.
The Agile 7612 generation instead puts the command byte before the length in
both directions: send ``[cmd][length_u16_LE][data]``, receive
``[cmd][length_u16_LE][error][data]``. Sending a standard-order frame to an
Agile 7612 controller, or vice versa, produces a frame the firmware silently
ignores rather than one it rejects with an error.
"""

from __future__ import annotations

import logging
import struct

from pylabrobot.io import LOG_LEVEL_IO

from ..errors import BravoError, ErrorType, RabbitErrorCode, rabbit_error_to_bravo_error
from ..transport import Transport
from .commands import CommandID
from .v11_comm import V11DeviceComm

logger = logging.getLogger(__name__)

_LENGTH_HEADER_FMT = "<H"


class V11Agile7612DeviceComm(V11DeviceComm):
  """V11 comm with the Agile 7612 frame order: ``[cmd][length][data]`` both ways.

  Attributes:
    error_log: Every command error seen, most recent last, for diagnostics.
    command_counts: How many times each command name has been sent.
  """

  def __init__(self, transport: Transport):
    """Bind this comm layer to a transport.

    Args:
      transport: The byte channel to the Agile 7612 controller.
    """
    super().__init__(transport)
    self.error_log: list[dict[str, str]] = []
    self.command_counts: dict[str, int] = {}

  def _send_once(
    self,
    command_id: CommandID,
    data: bytes,
    timeout: float,
  ) -> bytes:
    """Send and receive one Agile-7612-ordered V11 frame, without retrying.

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

    payload_length = len(data)
    frame = struct.pack("<BH", int(command_id), payload_length) + data

    try:
      cmd_name = CommandID(command_id).name
    except ValueError:
      cmd_name = f"0x{command_id:02X}"
    self.command_counts[cmd_name] = self.command_counts.get(cmd_name, 0) + 1

    logger.debug("TX cmd=0x%02X data_len=%d", command_id, len(data))
    if logger.isEnabledFor(LOG_LEVEL_IO):
      logger.log(LOG_LEVEL_IO, "TX frame: %s", frame.hex())

    self._transport.send(frame)

    header_bytes = self._transport.receive_exact(3, timeout)
    resp_cmd = header_bytes[0]
    (response_length,) = struct.unpack(_LENGTH_HEADER_FMT, header_bytes[1:3])

    logger.debug("RX cmd=0x%02X length=%d", resp_cmd, response_length)

    if response_length == 0:
      raise BravoError(ErrorType.NO_RESPONSE)

    response_payload = self._transport.receive_exact(response_length, timeout)

    if len(response_payload) < 1:
      raise BravoError(ErrorType.NO_RESPONSE)

    error_code = response_payload[0]
    response_data = response_payload[1:]

    logger.debug("RX error=0x%02X data_len=%d", error_code, len(response_data))
    if logger.isEnabledFor(LOG_LEVEL_IO):
      logger.log(LOG_LEVEL_IO, "RX frame: %s", (header_bytes + response_payload).hex())

    if error_code != RabbitErrorCode.NONE:
      self.error_log.append(
        {
          "cmd": cmd_name,
          "cmd_hex": f"0x{command_id:02X}",
          "error_code": f"0x{error_code:02X}",
          "data_hex": data.hex()[:40] if data else "",
        }
      )
      raise rabbit_error_to_bravo_error(error_code)

    return response_data

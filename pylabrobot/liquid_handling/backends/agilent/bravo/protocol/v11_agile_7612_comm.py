"""V11 communication layer for Agile 7612 Bravo — swapped frame byte order.

Standard V11: send ``[length_u16_LE][cmd][data]``, recv ``[length_u16_LE][error][data]``
Agile 7612 variant:  send ``[cmd][length_u16_LE][data]``, recv ``[cmd][length_u16_LE][error][data]``
"""

from __future__ import annotations

import logging
import struct

from pylabrobot.liquid_handling.backends.agilent.bravo.logging_config import TRACE
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import CommandID, DEFAULT_COMMAND_TIMEOUT_MS
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType, RabbitErrorCode, rabbit_error_to_bravo_error
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.v11_comm import V11DeviceComm

logger = logging.getLogger(__name__)

_LENGTH_HEADER_FMT = "<H"


class V11Agile7612DeviceComm(V11DeviceComm):
    """V11 comm with Agile 7612 frame order: ``[cmd][length][data]`` in both directions."""

    def __init__(self, transport):
        super().__init__(transport)
        self.error_log: list[dict] = []
        self.command_counts: dict[str, int] = {}

    def _send_once(
        self,
        command_id: CommandID,
        data: bytes,
        timeout_ms: int,
    ) -> bytes:
        if not self._transport.is_connected:
            raise ConnectionError("Transport is not connected")

        payload_length = len(data)
        frame = struct.pack("<BH", int(command_id), payload_length) + data

        from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import CommandID as _CID
        cmd_name = _CID(command_id).name if command_id in _CID.__members__.values() else f"0x{command_id:02X}"
        self.command_counts[cmd_name] = self.command_counts.get(cmd_name, 0) + 1

        logger.debug("TX cmd=0x%02X data_len=%d", command_id, len(data))
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, "TX frame: %s", frame.hex())

        self._transport.send(frame)

        header_bytes = self._transport.receive_exact(3, timeout_ms)
        resp_cmd = header_bytes[0]
        (response_length,) = struct.unpack(_LENGTH_HEADER_FMT, header_bytes[1:3])

        logger.debug("RX cmd=0x%02X length=%d", resp_cmd, response_length)

        if response_length == 0:
            raise BravoError(ErrorType.NO_RESPONSE)

        response_payload = self._transport.receive_exact(response_length, timeout_ms)

        if len(response_payload) < 1:
            raise BravoError(ErrorType.NO_RESPONSE)

        error_code = response_payload[0]
        response_data = response_payload[1:]

        logger.debug("RX error=0x%02X data_len=%d", error_code, len(response_data))
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, "RX frame: %s", (header_bytes + response_payload).hex())

        if error_code != RabbitErrorCode.NONE:
            self.error_log.append({
                "cmd": cmd_name,
                "cmd_hex": f"0x{command_id:02X}",
                "error_code": f"0x{error_code:02X}",
                "data_hex": data.hex()[:40] if data else "",
            })
            raise rabbit_error_to_bravo_error(error_code)

        return response_data

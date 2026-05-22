"""Serial (RS-232) transport for the Bravo Agile controller.

Connection parameters: 115200 baud, 8 data bits, no parity, 1 stop bit,
hardware flow control (RTS/CTS). Alpha+ variants use no flow control.
"""

from __future__ import annotations

import logging
import time

import serial

from pylabrobot.liquid_handling.backends.agilent.bravo.logging_config import TRACE
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.base import Transport

logger = logging.getLogger(__name__)

DEFAULT_BAUD = 115200
DEFAULT_DATA_BITS = serial.EIGHTBITS
DEFAULT_PARITY = serial.PARITY_NONE
DEFAULT_STOP_BITS = serial.STOPBITS_ONE


class SerialTransport(Transport):
    """RS-232 serial connection to the Bravo via the Rabbit microcontroller."""

    def __init__(
        self,
        port: str,
        baud: int = DEFAULT_BAUD,
        hardware_flow_control: bool = True,
    ):
        self._port = port
        self._baud = baud
        self._hw_flow = hardware_flow_control
        self._serial: serial.Serial | None = None

    def connect(self) -> None:
        if self._serial and self._serial.is_open:
            return
        logger.info("Connecting via serial: port=%s baud=%d hw_flow=%s",
                     self._port, self._baud, self._hw_flow)
        self._serial = serial.Serial(
            port=self._port,
            baudrate=self._baud,
            bytesize=DEFAULT_DATA_BITS,
            parity=DEFAULT_PARITY,
            stopbits=DEFAULT_STOP_BITS,
            rtscts=self._hw_flow,
            timeout=2.0,
        )

    def disconnect(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("Serial connection closed: %s", self._port)
        self._serial = None

    def send(self, data: bytes) -> None:
        if not self._serial or not self._serial.is_open:
            raise ConnectionError("Serial transport is not connected")
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, "Serial TX %d bytes: %s", len(data), data.hex())
        self._serial.write(data)
        self._serial.flush()

    def receive(self, timeout_ms: int = 2000) -> bytes:
        if not self._serial or not self._serial.is_open:
            raise ConnectionError("Serial transport is not connected")
        self._serial.timeout = timeout_ms / 1000.0
        data = self._serial.read(4096)
        if not data:
            raise TimeoutError(f"No response within {timeout_ms}ms on {self._port}")
        return bytes(data)

    def receive_exact(self, num_bytes: int, timeout_ms: int = 2000) -> bytes:
        if not self._serial or not self._serial.is_open:
            raise ConnectionError("Serial transport is not connected")
        deadline = time.monotonic() + timeout_ms / 1000.0
        buf = bytearray()
        while len(buf) < num_bytes:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise TimeoutError(
                    f"Timed out reading {num_bytes} bytes on "
                    f"{self._port} (got {len(buf)})"
                )
            self._serial.timeout = remaining_time
            chunk = self._serial.read(num_bytes - len(buf))
            if not chunk:
                raise TimeoutError(
                    f"Timed out reading {num_bytes} bytes on "
                    f"{self._port} (got {len(buf)})"
                )
            buf.extend(chunk)
        result = bytes(buf)
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, "Serial RX %d bytes: %s", len(result), result.hex())
        return result

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

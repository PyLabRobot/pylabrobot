"""TCP/IP transport for the Bravo (both Agile/Rabbit and Darwin controllers)."""

from __future__ import annotations

import logging
import select
import socket
import time

from pylabrobot.liquid_handling.backends.agilent.bravo.logging_config import TRACE
from pylabrobot.liquid_handling.backends.agilent.bravo.transport.base import Transport

logger = logging.getLogger(__name__)

DEFAULT_PORT = 10000  # typical Bravo TCP port


class TCPTransport(Transport):
    """TCP/IP socket connection to the Bravo controller."""

    def __init__(self, address: str, port: int = DEFAULT_PORT, connect_timeout: float = 5.0):
        self._address = address
        self._port = port
        self._connect_timeout = connect_timeout
        self._socket: socket.socket | None = None

    def connect(self) -> None:
        if self._socket:
            return
        logger.info("Connecting via TCP: %s:%d", self._address, self._port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._connect_timeout)
        sock.connect((self._address, self._port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._socket = sock
        logger.info("TCP connection established: %s:%d", self._address, self._port)

    def disconnect(self) -> None:
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()
            logger.info("TCP connection closed: %s:%d", self._address, self._port)
        self._socket = None

    def send(self, data: bytes) -> None:
        if not self._socket:
            raise ConnectionError("TCP transport is not connected")
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, "TCP TX %d bytes: %s", len(data), data.hex())
        self._socket.sendall(data)

    def receive(self, timeout_ms: int = 2000) -> bytes:
        if not self._socket:
            raise ConnectionError("TCP transport is not connected")
        self._socket.settimeout(timeout_ms / 1000.0)
        try:
            data = self._socket.recv(4096)
        except socket.timeout as exc:
            raise TimeoutError(
                f"No response within {timeout_ms}ms from {self._address}:{self._port}"
            ) from exc
        if not data:
            raise ConnectionError("Connection closed by remote host")
        return data

    def receive_exact(self, num_bytes: int, timeout_ms: int = 2000) -> bytes:
        if not self._socket:
            raise ConnectionError("TCP transport is not connected")
        deadline = time.monotonic() + timeout_ms / 1000.0
        buf = bytearray()
        while len(buf) < num_bytes:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise TimeoutError(
                    f"Timed out reading {num_bytes} bytes from "
                    f"{self._address}:{self._port} (got {len(buf)})"
                )
            self._socket.settimeout(remaining_time)
            try:
                chunk = self._socket.recv(num_bytes - len(buf))
            except socket.timeout as exc:
                raise TimeoutError(
                    f"Timed out reading {num_bytes} bytes from "
                    f"{self._address}:{self._port} (got {len(buf)})"
                ) from exc
            if not chunk:
                raise ConnectionError("Connection closed by remote host")
            buf.extend(chunk)
        result = bytes(buf)
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, "TCP RX %d bytes: %s", len(result), result.hex())
        return result

    def drain_pending(self) -> int:
        """Read and discard any bytes already in the receive buffer.

        Uses select() to check for pending data without changing the
        socket's blocking mode (avoids WinError 10035 races).
        Returns the number of bytes drained (0 if buffer was clean).
        """
        if not self._socket:
            return 0
        drained = 0
        while True:
            readable, _, _ = select.select([self._socket], [], [], 0)
            if not readable:
                break
            try:
                chunk = self._socket.recv(4096)
                if not chunk:
                    break
                drained += len(chunk)
            except OSError:
                break
        if drained:
            logger.warning("Drained %d stale bytes from TCP buffer", drained)
        return drained

    @property
    def is_connected(self) -> bool:
        return self._socket is not None

    def reconnect(self) -> None:
        """Disconnect and reconnect (used by Darwin controller on timeout)."""
        self.disconnect()
        self.connect()

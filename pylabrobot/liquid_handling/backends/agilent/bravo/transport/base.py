"""Abstract transport interface for Bravo communication."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Transport(ABC):
    """Base class for Bravo communication transports (serial or TCP/IP)."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the device."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection."""

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Send raw bytes to the device."""

    @abstractmethod
    def receive(self, timeout_ms: int = 2000) -> bytes:
        """Receive response bytes from the device.

        Args:
            timeout_ms: Maximum time to wait for a response.

        Returns:
            The raw response bytes.

        Raises:
            TimeoutError: If no response within the timeout period.
            ConnectionError: If the transport is not connected.
        """

    @abstractmethod
    def receive_exact(self, num_bytes: int, timeout_ms: int = 2000) -> bytes:
        """Receive exactly *num_bytes* from the device.

        Blocks until all bytes are received or the timeout expires.

        Args:
            num_bytes: Exact number of bytes to read.
            timeout_ms: Maximum time to wait.

        Returns:
            Exactly *num_bytes* bytes.

        Raises:
            TimeoutError: If the full byte count is not received within timeout.
            ConnectionError: If the transport is not connected.
        """

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is currently connected."""

    def __enter__(self) -> Transport:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()

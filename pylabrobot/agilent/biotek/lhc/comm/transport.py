"""The byte-level link to an instrument, independent of how the host is cabled to it.

An instrument speaks the same asynchronous serial link whether it is reached through an operating
system serial port or through a USB bridge, so everything above this module is written once.
:class:`Transport` is that shared surface; :mod:`.serial_transport` and :mod:`.ftdi_transport`
supply the two implementations, and :func:`.connection.open_transport` picks between them.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

BAUDRATE = 38400
DATA_BITS = 8
STOP_BITS = 2
PARITY_NONE = "N"

DEFAULT_READ_TIMEOUT = 15.0
DEFAULT_WRITE_TIMEOUT = 5.0

_POLL_INTERVAL = 0.005


class Transport(abc.ABC):
  """A 38400 8N2 link to an instrument, with no flow control.

  Reads are the only subtle part. A single :meth:`read` may return fewer bytes than asked for, so
  callers that need a fixed number of them use :meth:`read_exactly`, which keeps reading until the
  count is met or a deadline passes. A short return from :meth:`read_exactly` is how a caller
  detects an instrument that stopped answering part-way through a reply, so it is not an error
  here.

  Args:
    port: The port string this transport was opened from, kept for logging and error messages.
    timeout: Default read timeout in seconds.
  """

  def __init__(self, port: str, timeout: float = DEFAULT_READ_TIMEOUT) -> None:
    self._port = port
    self._read_timeout = timeout

  @property
  def port(self) -> str:
    """The port string this transport was opened from."""
    return self._port

  @property
  def read_timeout(self) -> float:
    """The default read timeout in seconds."""
    return self._read_timeout

  @read_timeout.setter
  def read_timeout(self, timeout: float) -> None:
    self._read_timeout = timeout
    self._apply_read_timeout(timeout)

  def _apply_read_timeout(self, timeout: float) -> None:
    """Push the read timeout down to the transport, for transports that enforce it themselves.

    Args:
      timeout: Read timeout in seconds.
    """

  @abc.abstractmethod
  async def setup(self) -> None:
    """Open the link and configure it for 38400 8N2 with no flow control."""

  @abc.abstractmethod
  async def stop(self) -> None:
    """Close the link. Calling this on a closed link does nothing."""

  @abc.abstractmethod
  async def write(self, data: bytes) -> None:
    """Write every byte of ``data``.

    Args:
      data: The bytes to write.

    Raises:
      RuntimeError: If the transport accepted fewer bytes than it was given.
    """

  @abc.abstractmethod
  async def read(self, num_bytes: int = 1) -> bytes:
    """Read up to ``num_bytes`` bytes. May return fewer, including none at all.

    Args:
      num_bytes: The most bytes to return.

    Returns:
      What had arrived, which may be shorter than requested.
    """

  @abc.abstractmethod
  async def purge(self) -> None:
    """Discard whatever is buffered in either direction."""

  async def read_exactly(self, num_bytes: int, timeout: float | None = None) -> bytes:
    """Read ``num_bytes`` bytes, or fewer once ``timeout`` has passed.

    Args:
      num_bytes: How many bytes to read.
      timeout: Read timeout in seconds. Defaults to :attr:`read_timeout`.

    Returns:
      Exactly ``num_bytes`` bytes, or fewer if the deadline passed first.
    """
    timeout = self._read_timeout if timeout is None else timeout
    self._apply_read_timeout(timeout)
    deadline = time.monotonic() + timeout
    data = bytearray()
    while len(data) < num_bytes:
      chunk = await self.read(num_bytes - len(data))
      if chunk:
        data += chunk
        continue
      if time.monotonic() >= deadline:
        logger.debug("[%s] read %d of %d bytes before timing out", self._port, len(data), num_bytes)
        break
      await asyncio.sleep(_POLL_INTERVAL)
    return bytes(data)

"""Hamilton's text protocol over TCP: plain ASCII lines, each ending in CRLF."""

import asyncio
from typing import Optional

from pylabrobot.io.io import IOBase
from pylabrobot.io.socket import Socket

DEFAULT_PORT = 24652
TERMINATOR = b"\r\n"

# A link with nothing to say is normal: the reply router reads it continuously, whether or not a
# command is waiting. The socket's own read timeout is therefore long, so that an idle link does
# not log a timeout every period.
IDLE_READ_TIMEOUT = 3600


class HamiltonTextTCP(IOBase):
  """A TCP connection to a Hamilton machine that speaks the text protocol.

  A command is sent as one line and a reply, or a message the machine sends unasked, comes back as
  one line. There is no handshake and no keepalive. The connection must be opened with `setup`
  before it is read or written.

  `read` and `write` deal in whole lines without their terminator, so a reply split across several
  TCP segments is returned once complete, and several replies in one segment are returned one by
  one.
  """

  def __init__(
    self,
    host: str,
    port: int = DEFAULT_PORT,
    write_timeout: float = 30,
    read_timeout: float = IDLE_READ_TIMEOUT,
  ):
    """
    Args:
      host: The address of the machine, as an IP address or a name the system can resolve.
      port: The TCP port the machine listens on.
      write_timeout: Seconds to wait for a line to be handed to the network.
      read_timeout: Seconds `read` waits for a line, unless it is given another time.
    """
    self._host = host
    self._port = port
    self._write_timeout = write_timeout
    self._read_timeout = read_timeout
    self._socket = Socket(
      human_readable_device_name="Hamilton text TCP",
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

  async def setup(self):
    """Open the connection."""
    await self._socket.setup()

  async def stop(self):
    """Close the connection."""
    await self._socket.stop()

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Send one line.

    Args:
      data: The line, without its terminator.
      timeout: Seconds to wait for the line to be handed to the network. If None, the write
        timeout the connection was made with is used.

    Raises:
      TimeoutError: If the line was not handed to the network in time.
    """
    await self._socket.write(data + TERMINATOR, timeout=timeout)

  async def read(self, timeout: Optional[float] = None) -> bytes:
    """Read one line, waiting until it is complete.

    Args:
      timeout: Seconds to wait for a line. If None, the read timeout the connection was made with
        is used.

    Returns:
      The line, without its terminator.

    Raises:
      TimeoutError: If no complete line arrived in time.
      ConnectionError: If the machine closed the connection.
    """
    try:
      line = await self._socket.readuntil(TERMINATOR, timeout=timeout)
    except asyncio.IncompleteReadError as e:
      raise ConnectionError(f"{self._host}:{self._port} closed the connection") from e
    return line[: -len(TERMINATOR)]

  def serialize(self) -> dict:
    """The arguments this connection was made with."""
    return {
      **super().serialize(),
      "type": "HamiltonTextTCP",
      "host": self._host,
      "port": self._port,
      "write_timeout": self._write_timeout,
      "read_timeout": self._read_timeout,
    }

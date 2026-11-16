"""Hamilton's text protocol over TCP: plain ASCII lines, each ending in CRLF."""

from typing import Any, Callable, Dict, Optional

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.io import ReconnectableConnectionIO
from pylabrobot.io.socket import Socket

DEFAULT_PORT = 24652
TERMINATOR = b"\r\n"


class HamiltonTextTCP(ReconnectableConnectionIO):
  """A TCP connection to a Hamilton machine that speaks the text protocol.

  A command is sent as one line and a reply, or a message the machine sends unasked, comes back as
  one line. There is no handshake and no keepalive. Enter the connection's lifespan with
  ``async with`` before reading or writing.

  ``read`` and ``write`` deal in whole lines without their terminator, so a reply split across
  several TCP segments is returned once complete, and several replies in one segment are returned
  one by one.
  """

  def __init__(
    self,
    host: str,
    port: int = DEFAULT_PORT,
    write_timeout: float = 30,
  ) -> None:
    """
    Args:
      host: The address of the machine, as an IP address or a name the system can resolve.
      port: The TCP port the machine listens on.
      write_timeout: Seconds to wait for a line to be handed to the network.
    """
    super().__init__()
    self._host = host
    self._port = port
    self._write_timeout = write_timeout
    self._socket = Socket(
      human_readable_device_name="Hamilton text TCP",
      host=host,
      port=port,
      write_timeout=write_timeout,
    )

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open the TCP connection for this lifespan."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self._socket.lifespan())

  async def reconnect(self) -> None:
    """Close the current TCP connection and establish a replacement."""
    if not self.is_active:
      raise RuntimeError(
        "HamiltonTextTCP requires an active lifespan; have you entered its lifespan?"
      )
    await self._socket.reconnect()

  async def write(
    self,
    data: bytes,
    timeout: Optional[float] = None,
    on_dispatch: Optional[Callable[[], None]] = None,
  ) -> None:
    """Send one line.

    Args:
      data: The line, without its terminator.
      timeout: Seconds to wait for the line to be handed to the network. If None, the write
        timeout the connection was made with is used.
      on_dispatch: Optional notification immediately before the stream write is attempted.

    Raises:
      RuntimeError: If the connection's lifespan is not active.
      TimeoutError: If the line was not handed to the network in time.
    """
    if not self.is_active:
      raise RuntimeError(
        "HamiltonTextTCP requires an active lifespan; have you entered its lifespan?"
      )
    await self._socket._write_with_dispatch(
      data + TERMINATOR, timeout=timeout, on_dispatch=on_dispatch
    )

  async def read(self) -> bytes:
    """Read one line, waiting until it is complete.

    Returns:
      The line, without its terminator.

    Raises:
      RuntimeError: If the connection's lifespan is not active.
      ConnectionError: If the machine closed the connection.
    """
    if not self.is_active:
      raise RuntimeError(
        "HamiltonTextTCP requires an active lifespan; have you entered its lifespan?"
      )
    try:
      line = await self._socket.readuntil(TERMINATOR)
    except (
      EOFError,
      anyio.EndOfStream,
      anyio.BrokenResourceError,
      anyio.ClosedResourceError,
    ) as e:
      raise ConnectionError(f"{self._host}:{self._port} closed the connection") from e
    return line[: -len(TERMINATOR)]

  async def read_reply(self) -> bytes:
    """Read one framed text reply for ReplyRouter."""
    return await self.read()

  def serialize(self) -> Dict[str, Any]:
    """The arguments this connection was made with."""
    return {
      **super().serialize(),
      "type": "HamiltonTextTCP",
      "host": self._host,
      "port": self._port,
      "write_timeout": self._write_timeout,
    }

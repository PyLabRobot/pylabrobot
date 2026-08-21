"""TCP transport for Bravo instruments, over PyLabRobot's socket I/O."""

from pylabrobot.io.socket import Socket

from ._bridge import _RECEIVE_BUFFER_SIZE, AsyncTransportBase


class SocketTransport(AsyncTransportBase):
  """A synchronous byte channel over :class:`pylabrobot.io.socket.Socket`.

  Because ``Socket.read_exact`` applies its timeout per chunk rather than
  cumulatively, the outer, future-level bound each call is given (see
  ``AsyncTransportBase._run_bounded``) is the real cumulative ceiling on that
  call: a trickling
  response can delay individual chunks indefinitely, but
  ``receive_exact(n, timeout=2.0)`` can itself never take longer than 3.0 seconds
  in total, no matter how many chunks the response arrives in.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    host: str,
    port: int,
    timeout: float = 2.0,
  ):
    """Create a socket transport for the given host and port.

    Args:
      human_readable_device_name: Name used in PyLabRobot's I/O logs.
      host: The instrument's IP address or hostname.
      port: The instrument's TCP port.
      timeout: Default time to wait for a send to complete, in seconds.
    """
    super().__init__(transport_name="socket", endpoint=f"{host}:{port}")
    self._io = Socket(
      human_readable_device_name=human_readable_device_name,
      host=host,
      port=port,
    )
    self._timeout = timeout

  async def _open_io(self) -> None:
    """Connect the socket."""
    await self._io.setup()

  async def _close_io(self) -> None:
    """Disconnect the socket."""
    await self._io.stop()

  def send(self, data: bytes) -> None:
    """Send raw bytes to the device. See :meth:`Transport.send`."""
    self._run_bounded(self._io.write(data, timeout=self._timeout), self._timeout)

  def receive(self, timeout: float = 2.0) -> bytes:
    """Receive whatever bytes a single underlying read yields, up to 4096 bytes.

    See :meth:`Transport.receive`. The 4096-byte cap only bounds an unusually
    large response; it is not the hazard to plan around. A single read can, and
    routinely does, return far fewer bytes than a complete frame regardless of
    that frame's size, because it returns whatever fragment TCP has delivered so
    far -- a 20-byte frame split across two TCP segments can come back as 8 bytes
    on one call. This method never guarantees a complete frame; reassembling one
    from possibly-partial reads is the caller's responsibility.
    """
    return self._run_receive(
      self._io.read(num_bytes=_RECEIVE_BUFFER_SIZE, timeout=timeout), timeout
    )

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    """Receive exactly ``num_bytes``. See :meth:`Transport.receive_exact`."""
    return self._run_bounded(self._io.read_exact(num_bytes, timeout=timeout), timeout)

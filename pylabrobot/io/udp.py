"""Connected UDP transport preserving datagram boundaries and IO captures."""

import asyncio
import logging
import socket
from typing import Optional

from pylabrobot.io.capture import capturer, get_capture_or_validation_active
from pylabrobot.io.io import IOBase
from pylabrobot.io.socket import SocketCommand
from pylabrobot.io.validation_utils import LOG_LEVEL_IO

logger = logging.getLogger(__name__)


class UDP(IOBase):
  """Send and receive whole datagrams from one peer, without automatic retries.

  A connected UDP socket filters incoming packets by peer address and port. A
  successful setup does not establish that the peer is present or listening.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    host: str,
    port: int,
    read_timeout: float = 30,
    write_timeout: float = 30,
    source_ip: Optional[str] = None,
  ) -> None:
    """Configure a disconnected UDP socket with an optional local IPv4 address."""
    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a UDP object while capture or validation is active")
    self.human_readable_device_name = human_readable_device_name
    self._host = host
    self._port = port
    self._read_timeout = read_timeout
    self._write_timeout = write_timeout
    self._source_ip = source_ip
    self._socket: Optional[socket.socket] = None
    self._read_lock = asyncio.Lock()
    self._write_lock = asyncio.Lock()
    self._unique_id = f"{host}:{port}"

  async def setup(self) -> None:
    """Open a nonblocking IPv4 socket and select its peer."""
    if self._socket is not None:
      return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
      if self._source_ip is not None:
        sock.bind((self._source_ip, 0))
      await asyncio.get_running_loop().sock_connect(sock, (self._host, self._port))
    except BaseException:
      sock.close()
      raise
    self._socket = sock

  async def stop(self) -> None:
    """Close the socket; no datagram is sent to the peer."""
    if self._socket is not None:
      self._socket.close()
      self._socket = None

  def serialize(self) -> dict:
    """Describe the configured transport."""
    return {
      "human_readable_device_name": self.human_readable_device_name,
      "host": self._host,
      "port": self._port,
      "read_timeout": self._read_timeout,
      "write_timeout": self._write_timeout,
      "source_ip": self._source_ip,
      "type": "UDP",
    }

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Send one nonempty IPv4 datagram and record it, without retransmission."""
    if not 1 <= len(data) <= 65507:
      raise ValueError("UDP data must contain 1–65507 bytes")
    async with self._write_lock:
      if self._socket is None:
        raise RuntimeError("UDP socket is closed; call setup() first")
      await asyncio.wait_for(
        asyncio.get_running_loop().sock_sendall(self._socket, data),
        self._write_timeout if timeout is None else timeout,
      )
      logger.log(LOG_LEVEL_IO, "[%s:%d UDP] write %s", self._host, self._port, data)
      capturer.record(SocketCommand(self._unique_id, "write", data.hex(), module="udp"))

  async def read(self, timeout: Optional[float] = None) -> bytes:
    """Receive exactly one datagram, including empty datagrams, without truncation."""
    async with self._read_lock:
      if self._socket is None:
        raise RuntimeError("UDP socket is closed; call setup() first")
      data = await asyncio.wait_for(
        asyncio.get_running_loop().sock_recv(self._socket, 65535),
        self._read_timeout if timeout is None else timeout,
      )
      logger.log(LOG_LEVEL_IO, "[%s:%d UDP] read %s", self._host, self._port, data)
      capturer.record(SocketCommand(self._unique_id, "read", data.hex(), module="udp"))
      return data

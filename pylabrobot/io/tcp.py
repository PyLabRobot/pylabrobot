import asyncio
import datetime
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from pylabrobot.io.capture import capturer, get_capture_or_validation_active, Command
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO

if TYPE_CHECKING:
  from pylabrobot.io.capture import CaptureReader


logger = logging.getLogger(__name__)


@dataclass
class TCPCommand(Command):
  data: str
  timestamp: str

  def __init__(self, device_id: str, action: str, data: str, timestamp: str, module: str = "tcp"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data
    self.timestamp = timestamp


class TCP(IOBase):
  """Minimal IO for reading/writing to a TCP device."""

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: int = 30,
    write_timeout: int = 30,
    buffer_size: int = 1024,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
  ):
    """Initialize an io.TCP object.

    Args:
      host: The hostname or IP address of the TCP server.
      port: The port number of the TCP server.
      read_timeout: The timeout for reading from the server in seconds.
      write_timeout: The timeout for writing to the server in seconds.
      buffer_size: The buffer size for reading data from the socket.
      auto_reconnect: If True, automatically reconnect on connection failure.
      max_reconnect_attempts: Maximum number of reconnection attempts.
    """

    super().__init__()

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new TCP object while capture or validation is active")

    self._host = host
    self._port = port
    self.read_timeout = read_timeout
    self.write_timeout = write_timeout
    self.buffer_size = buffer_size
    self.auto_reconnect = auto_reconnect
    self.max_reconnect_attempts = max_reconnect_attempts

    self.socket: Optional[socket.socket] = None
    self._executor: Optional[ThreadPoolExecutor] = None

    # Connection state tracking
    self._connection_state = "disconnected"
    self._last_error = None
    self._reconnect_attempts = 0


    # unique id in the logs
    self._unique_id = f"[{self._host}:{self._port}]"

  async def _ensure_connected(self):
    """Ensure connection is healthy before operations."""
    if self._connection_state != "connected":
      if self.auto_reconnect:
        logger.info(f"{self._unique_id} Connection not established, attempting to reconnect...")
        await self._reconnect()
      else:
        raise ConnectionError(f"{self._unique_id} Connection not established and auto-reconnect disabled")

  async def _reconnect(self):
    """Attempt to reconnect with exponential backoff."""
    if not self.auto_reconnect:
      raise ConnectionError(f"{self._unique_id} Auto-reconnect disabled")

    for attempt in range(self.max_reconnect_attempts):
      try:
        logger.info(f"{self._unique_id} Reconnection attempt {attempt + 1}/{self.max_reconnect_attempts}")

        # Clean up existing connection
        if self.socket is not None:
          try:
            self.socket.close()
          except:
            pass
          self.socket = None

        # Wait before reconnecting (exponential backoff)
        if attempt > 0:
          wait_time = 1.0 * (2 ** (attempt - 1))  # 1s, 2s, 4s, etc.
          await asyncio.sleep(wait_time)

        # Attempt to reconnect
        await self.setup()
        self._reconnect_attempts = 0
        logger.info(f"{self._unique_id} Reconnection successful")
        return

      except Exception as e:
        self._last_error = e
        logger.warning(f"{self._unique_id} Reconnection attempt {attempt + 1} failed: {e}")

    # All reconnection attempts failed
    self._connection_state = "disconnected"
    raise ConnectionError(f"{self._unique_id} Failed to reconnect after {self.max_reconnect_attempts} attempts")

  async def write(self, data: bytes, timeout: Optional[float] = None):
    """Write data to the TCP server.

    Args:
      data: The data to write.
      timeout: The timeout for writing to the server in seconds. If `None`, use the default timeout
        (specified by the `write_timeout` attribute).
    """

    await self._ensure_connected()

    if timeout is None:
      timeout = self.write_timeout

    # write data to socket
    loop = asyncio.get_running_loop()
    sock = self.socket
    if self._executor is None or sock is None:
      raise RuntimeError("Call setup() first.")

    def write_with_timeout():
      # Set socket timeout for write operation
      sock.settimeout(timeout)
      try:
        sock.sendall(data)
      finally:
        # Reset socket to blocking mode
        sock.settimeout(None)

    try:
      await loop.run_in_executor(self._executor, write_with_timeout)
      self._connection_state = "connected"
      logger.log(LOG_LEVEL_IO, "%s write: %s", self._unique_id, data)

      # Capture raw traffic for debugging
      if get_capture_or_validation_active():
        capturer.record(TCPCommand(
          device_id=self._unique_id,
          action="write",
          data=data.decode("unicode_escape"),
          timestamp=datetime.datetime.now().isoformat()
        ))
    except (ConnectionError, socket.error) as e:
      self._connection_state = "disconnected"
      self._last_error = e
      raise

  async def read(self, num_bytes: int = None, timeout: Optional[int] = None) -> bytes:
    """Read data from the TCP server.

    Args:
      num_bytes: Maximum number of bytes to read. If None, use buffer_size.
      timeout: The timeout for reading from the server in seconds. If `None`, use the default
        timeout (specified by the `read_timeout` attribute).
    """

    await self._ensure_connected()

    if timeout is None:
      timeout = self.read_timeout

    if num_bytes is None:
      num_bytes = self.buffer_size

    def read_or_timeout():
      # Set socket timeout
      self.socket.settimeout(timeout)

      try:
        # Read data from socket
        data = self.socket.recv(num_bytes)
        if not data:
          raise ConnectionError("Connection closed by server")

        logger.log(LOG_LEVEL_IO, "%s read: %s", self._unique_id, data)

        # Capture raw traffic for debugging
        if get_capture_or_validation_active():
          capturer.record(TCPCommand(
            device_id=self._unique_id,
            action="read",
            data=data.decode("unicode_escape"),
            timestamp=datetime.datetime.now().isoformat()
          ))

        return data

      except socket.timeout:
        raise TimeoutError("Timeout while reading.")
      finally:
        # Reset socket to blocking mode
        self.socket.settimeout(None)

    loop = asyncio.get_running_loop()
    if self._executor is None or self.socket is None:
      raise RuntimeError("Call setup() first.")

    try:
      data = await loop.run_in_executor(self._executor, read_or_timeout)
      self._connection_state = "connected"
      return data
    except (ConnectionError, socket.error) as e:
      self._connection_state = "disconnected"
      self._last_error = e
      raise

  async def read_until(self, terminator: bytes, timeout: Optional[int] = None) -> bytes:
    """Read until terminator is found.

    Args:
      terminator: The byte sequence to read until (e.g., b'\r\n', b'\n').
      timeout: The timeout for reading from the server in seconds. If `None`, use the default
        timeout (specified by the `read_timeout` attribute).

    Returns:
      The data read up to and including the terminator.
    """
    if timeout is None:
      timeout = self.read_timeout

    start_time = time.time()
    message = b""

    while True:
      # Calculate remaining timeout
      elapsed = time.time() - start_time
      remaining_timeout = max(0, timeout - elapsed)

      if remaining_timeout <= 0:
        raise TimeoutError("Timeout while reading until terminator")

      chunk = await self.read(1, int(remaining_timeout))  # Read byte by byte
      if not chunk:
        raise ConnectionError("Connection closed")
      message += chunk
      if message.endswith(terminator):
        break
    return message

  async def read_exact(self, num_bytes: int, timeout: Optional[int] = None) -> bytes:
    """Read exactly num_bytes.

    Args:
      num_bytes: The exact number of bytes to read.
      timeout: The timeout for reading from the server in seconds. If `None`, use the default
        timeout (specified by the `read_timeout` attribute).

    Returns:
      Exactly num_bytes of data.

    Raises:
      ConnectionError: If the connection is closed before num_bytes are read.
    """
    data = b""
    while len(data) < num_bytes:
      chunk = await self.read(num_bytes - len(data), timeout)
      if not chunk:
        raise ConnectionError("Connection closed")
      data += chunk
    return data

  async def read_line(self, timeout: Optional[int] = None) -> str:
    """Read until newline (convenience method for text protocols).

    Args:
      timeout: The timeout for reading from the server in seconds. If `None`, use the default
        timeout (specified by the `read_timeout` attribute).

    Returns:
      The line read, with trailing newline characters removed.
    """
    data = await self.read_until(b'\n', timeout)
    return data.decode('utf-8', errors='replace').rstrip('\r\n')

  async def setup(self):
    """Initialize the TCP connection to the server."""

    if self.socket is not None:
      # previous setup did not properly finish,
      # or we are re-initializing the connection.
      logger.warning("TCP socket already connected. Closing previous connection.")
      await self.stop()

    logger.info("Connecting to TCP server %s:%d...", self._host, self._port)

    # Create socket and connect
    loop = asyncio.get_running_loop()
    if self._executor is None:
      self._executor = ThreadPoolExecutor(max_workers=1)

    try:
      self.socket = await loop.run_in_executor(
        self._executor,
        lambda: socket.create_connection((self._host, self._port), timeout=self.read_timeout)
      )
      self._connection_state = "connected"
      self._last_error = None
      logger.info("Connected to TCP server %s:%d", self._host, self._port)


    except Exception as e:
      self._connection_state = "disconnected"
      self._last_error = e
      raise

  async def stop(self):
    """Close the TCP connection to the server."""

    if self.socket is None:
      raise ValueError("TCP socket was not connected.")

    logging.warning("Closing connection to TCP server.")

    # Close socket immediately
    if self.socket is not None:
      try:
        self.socket.close()
      except Exception as e:
        logger.warning("Error closing socket: %s", e)
      self.socket = None

    self._connection_state = "disconnected"

    # Shutdown executor without waiting
    if self._executor is not None:
      self._executor.shutdown(wait=False)  # Don't wait for pending tasks
      self._executor = None

  @property
  def connection_state(self) -> str:
    """Get the current connection state."""
    return self._connection_state

  @property
  def is_connected(self) -> bool:
    """Check if the connection is currently established."""
    return self._connection_state == "connected"

  @property
  def last_error(self) -> Optional[Exception]:
    """Get the last connection error."""
    return self._last_error


  def serialize(self) -> dict:
    """Serialize the backend to a dictionary."""

    return {
      **super().serialize(),
      "host": self._host,
      "port": self._port,
      "read_timeout": self.read_timeout,
      "write_timeout": self.write_timeout,
      "buffer_size": self.buffer_size,
      "auto_reconnect": self.auto_reconnect,
      "max_reconnect_attempts": self.max_reconnect_attempts,
    }


class TCPValidator(TCP):
  def __init__(
    self,
    cr: "CaptureReader",
    host: str,
    port: int,
    read_timeout: int = 30,
    write_timeout: int = 30,
    buffer_size: int = 1024,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
  ):
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      buffer_size=buffer_size,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
    )
    self.cr = cr

  async def setup(self):
    """Validation mode - no real connection needed."""
    pass

  async def write(self, data: bytes, timeout: Optional[float] = None):
    """Validate write command against captured data."""
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == self._unique_id
      and next_command.action == "write"
    ):
      raise ValidationError("Next command is not TCP write")
    if next_command.data != data.decode("unicode_escape"):
      from pylabrobot.io.validation_utils import align_sequences
      align_sequences(expected=next_command.data, actual=data.decode("unicode_escape"))
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(self, timeout: Optional[int] = None) -> bytes:
    """Validate read command and return captured data."""
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
    ):
      raise ValidationError("Next command is not TCP read")
    return next_command.data.encode()

  async def read_until(self, terminator: bytes, timeout: Optional[int] = None) -> bytes:
    """Validate read_until command and return captured data."""
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
    ):
      raise ValidationError("Next command is not TCP read")
    return next_command.data.encode()

  async def read_exact(self, num_bytes: int, timeout: Optional[int] = None) -> bytes:
    """Validate read_exact command and return captured data."""
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
    ):
      raise ValidationError("Next command is not TCP read")
    return next_command.data.encode()

  async def read_line(self, timeout: Optional[int] = None) -> str:
    """Validate read_line command and return captured data."""
    next_command = TCPCommand(**self.cr.next_command())
    if not (
      next_command.module == "tcp"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
    ):
      raise ValidationError("Next command is not TCP read")
    return next_command.data

  async def stop(self):
    """Validation mode - no real connection to close."""
    pass

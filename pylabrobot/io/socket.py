import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO

if TYPE_CHECKING:
  from pylabrobot.io.capture import CaptureReader


logger = logging.getLogger(__name__)


@dataclass
class SocketCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "socket"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data


class Socket(IOBase):
  """IO for reading/writing to a TCP socket."""

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """Initialize an io.Socket object.

    Args:
      host: The hostname or IP address to connect to.
      port: The port number to connect to.
      read_timeout: The timeout for reading from the socket in seconds.
      write_timeout: The timeout for writing to the socket in seconds.
    """

    super().__init__()

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new Socket object while capture or validation is active")

    self.host = host
    self.port = port
    self.read_timeout = read_timeout
    self.write_timeout = write_timeout

    self._reader: Optional[asyncio.StreamReader] = None
    self._writer: Optional[asyncio.StreamWriter] = None

    # unique id in the logs
    self._unique_id = f"[{self.host}:{self.port}]"

  async def write(self, data: str, timeout: Optional[int] = None):
    if self._writer is None:
      raise ConnectionError("Socket not connected. Call setup() first.")

    if timeout is None:
      timeout = self.write_timeout

    try:
      self._writer.write(data.encode("ascii"))
      await asyncio.wait_for(self._writer.drain(), timeout=timeout)

      logger.log(LOG_LEVEL_IO, "%s write: %s", self._unique_id, data.strip())
      capturer.record(SocketCommand(device_id=self._unique_id, action="write", data=data))
    except asyncio.TimeoutError as exc:
      raise TimeoutError(f"Timeout while writing to socket after {timeout} seconds") from exc

  async def read(self, timeout: Optional[int] = None, read_once=True) -> str:
    """Read data from the socket.
    Args:
      timeout: The timeout for reading from the socket in seconds. If None, uses the default
        read_timeout set during initialization.
      read_once: If True, reads until the first complete message is received. If False, continues
        reading until the connection is closed or a timeout occurs.
    """

    if self._reader is None:
      raise ConnectionError("Socket not connected. Call setup() first.")

    if timeout is None:
      timeout = self.read_timeout

    try:
      chunks = []
      while True:
        try:
          data = await asyncio.wait_for(self._reader.read(1024), timeout=timeout)
          if not data:
            # Connection closed
            break
          chunks.append(data)
          if read_once:
            break
        except asyncio.TimeoutError as exc:
          if chunks:
            # We have some data, return it
            break
          raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc

      if len(chunks) == 0:
        raise ConnectionError("Socket connection closed")

      response = b"".join(chunks).decode("ascii")
      logger.log(LOG_LEVEL_IO, "%s read: %s", self._unique_id, response.strip())
      capturer.record(SocketCommand(device_id=self._unique_id, action="read", data=response))
      return response

    except UnicodeDecodeError as e:
      raise ValueError(f"Failed to decode socket response as ASCII: {e}") from e

  async def setup(self):
    """Initialize the socket connection."""

    if self._writer is not None:
      # previous setup did not properly finish,
      # or we are re-initializing the connection.
      logger.warning("Socket already connected. Closing previous connection.")
      await self.stop()

    logger.info("Connecting to socket %s:%s...", self.host, self.port)

    try:
      self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
      logger.info("Connected to socket %s:%s", self.host, self.port)
    except Exception as e:
      raise ConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}") from e

  async def stop(self):
    """Close the socket connection."""

    if self._writer is None:
      logger.debug("Socket already disconnected.")
      return

    logger.info("Closing connection to socket %s:%s", self.host, self.port)

    try:
      self._writer.close()
      await self._writer.wait_closed()
    except OSError as e:
      logger.warning("Error while closing socket connection: %s", e)
    finally:
      self._reader = None
      self._writer = None

  def serialize(self) -> dict:
    """Serialize the socket to a dictionary."""

    return {
      **super().serialize(),
      "host": self.host,
      "port": self.port,
      "read_timeout": self.read_timeout,
      "write_timeout": self.write_timeout,
    }


class SocketValidator(Socket):
  """Socket validator for testing/validation purposes."""

  def __init__(
    self,
    cr: "CaptureReader",
    host: str,
    port: int,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )
    self.cr = cr

  async def setup(self):
    """Mock setup for validation."""
    return

  async def write(self, *args, **kwargs):
    """Validate write command against captured data."""
    if not args:
      raise ValueError("No data provided to write")

    data = args[0]
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "socket"
      and next_command.device_id == self._unique_id
      and next_command.action == "write"
    ):
      raise ValidationError(
        f"Expected socket write command to {self._unique_id}, "
        f"got {next_command.module} {next_command.action} to {next_command.device_id}"
      )
    if not next_command.data == data:
      raise ValidationError(
        f"Socket write data mismatch. Expected:\n{next_command.data}\nGot:\n{data}"
      )

  async def read(self, *args, **kwargs) -> str:
    """Return captured read data for validation."""
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "socket"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
    ):
      raise ValidationError(
        f"Expected socket read command from {self._unique_id}, "
        f"got {next_command.module} {next_command.action} from {next_command.device_id}"
      )
    return next_command.data

  async def stop(self):
    """Mock stop for validation."""
    return

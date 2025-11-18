import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

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
    read_timeout: float = 30,
    write_timeout: float = 30,
  ):
    self._host = host
    self._port = port
    self._reader: Optional[asyncio.StreamReader] = None
    self._writer: Optional[asyncio.StreamWriter] = None
    self._read_timeout = read_timeout
    self._write_timeout = write_timeout
    self._unique_id = f"{self._host}:{self._port}"
    self._read_lock = asyncio.Lock()
    self._write_lock = asyncio.Lock()

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new TCP object while capture or validation is active")

  async def setup(self):
    await self._connect()

  async def _connect(self):
    self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

  async def stop(self):
    await self._disconnect()

  async def _disconnect(self):
    async with self._read_lock, self._write_lock:
      self._reader = None
      if self._writer is None:
        return

      logger.info("Closing connection to socket %s:%s", self._host, self._port)

      try:
        self._writer.close()
        await self._writer.wait_closed()
      except OSError as e:
        logger.warning("Error while closing socket connection: %s", e)
      finally:
        self._writer = None

  async def reconnect(self):
    await self._disconnect()
    await self._connect()

  def serialize(self):
    return {
      "host": self._host,
      "port": self._port,
      "type": "Socket",
      "read_timeout": self._read_timeout,
      "write_timeout": self._write_timeout,
    }

  @classmethod
  def deserialize(cls, data: dict) -> "Socket":
    kwargs = {}
    if "read_timeout" in data:
      kwargs["read_timeout"] = data["read_timeout"]
    if "write_timeout" in data:
      kwargs["write_timeout"] = data["write_timeout"]
    return cls(
      host=data["host"],
      port=data["port"],
      **kwargs,
    )

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Wrapper around StreamWriter.write with reconnects, lock, io logging.
    Does not retry on timeouts.
    """
    assert self._writer is not None, "forgot to call setup?"

    async with self._write_lock:
      self._writer.write(data)
      logger.log(LOG_LEVEL_IO, "[%s:%d] write %s", self._host, self._port, data)
      capturer.record(
        SocketCommand(
          device_id=f"{self._host}:{self._port}",
          action="write",
          data=data.hex(),
        )
      )
      try:
        await asyncio.wait_for(self._writer.drain(), timeout=timeout or self._write_timeout)
        return
      except (ConnectionResetError, OSError) as e:
        logger.error("write error: %r", e)
        raise

  async def read(self, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
    """Wrapper around StreamReader.read with lock and io logging."""
    assert self._reader is not None, "forgot to call setup?"
    async with self._read_lock:
      data = await asyncio.wait_for(self._reader.read(num_bytes), timeout=timeout or self._read_timeout)
      logger.log(LOG_LEVEL_IO, "[%s:%d] read %s", self._host, self._port, data.hex())
      capturer.record(
        SocketCommand(
          device_id=f"{self._host}:{self._port}",
          action="read",
          data=data.hex(),
        )
      )
      return data

  async def readline(self, timeout: Optional[float] = None) -> bytes:
    """Wrapper around StreamReader.readline with lock and io logging."""
    assert self._reader is not None, "forgot to call setup?"
    async with self._read_lock:
      data = await asyncio.wait_for(self._reader.readline(), timeout=timeout or self._read_timeout)
      logger.log(LOG_LEVEL_IO, "[%s:%d] read %s", self._host, self._port, data.hex())
      capturer.record(
        SocketCommand(
          device_id=f"{self._host}:{self._port}",
          action="readline",
          data=data.hex(),
        )
      )
      return data

  async def readuntil(self, separator: bytes = b"\n", timeout: Optional[float] = None) -> bytes:
    """Wrapper around StreamReader.readuntil with lock and io logging.
    Do not retry on timeouts."""
    assert self._reader is not None, "forgot to call setup?"
    async with self._read_lock:
      data = await asyncio.wait_for(self._reader.readuntil(separator), timeout=timeout or self._read_timeout)
      logger.log(LOG_LEVEL_IO, "[%s:%d] read %s", self._host, self._port, data.hex())
      capturer.record(
        SocketCommand(
          device_id=f"{self._host}:{self._port}",
          action="readuntil:" + separator.hex(),
          data=data.hex(),
        )
      )
      return data

  async def read_until_eof(self, chunk_size: int = 1024, timeout: Optional[float] = None) -> bytes:
    """Read until EOF is reached.
    Do not retry on timeouts.
    """
    buf = bytearray()

    async with self._read_lock:
      while True:
        async def _read_coro() -> bytes:
          assert self._reader is not None, "forgot to call setup?"
          return await self._reader.read(chunk_size)

        chunk = await asyncio.wait_for(_read_coro(), timeout=timeout or self._read_timeout)
        if len(chunk) == 0:
          break

        logger.debug("read_until_eof: got %d bytes", len(chunk))
        buf.extend(chunk)

    line = bytes(buf)
    logger.log(LOG_LEVEL_IO, "[%s:%d] read_until_eof %s", self._host, self._port, line.hex())
    capturer.record(
      SocketCommand(
        device_id=f"{self._host}:{self._port}",
        action="read_until_eof",
        data=line.hex(),
      )
    )
    return line


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

  async def write(self, data: bytes, *args, **kwargs):
    """Validate write command against captured data."""
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
    if not bytes.fromhex(next_command.data) == data:
      raise ValidationError(
        f"Socket write data mismatch. Expected:\n{next_command.data}\nGot:\n{data}"
      )

  async def read(self, *args, **kwargs) -> bytes:
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
    return bytes.fromhex(next_command.data)

  async def readline(self, *args, **kwargs) -> bytes:
    """Return captured readline data for validation."""
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "socket"
      and next_command.device_id == self._unique_id
      and next_command.action == "readline"
    ):
      raise ValidationError(
        f"Expected socket readline command from {self._unique_id}, "
        f"got {next_command.module} {next_command.action} from {next_command.device_id}"
      )
    return bytes.fromhex(next_command.data)

  async def readuntil(self, separator: bytes, *args, **kwargs) -> bytes:
    """Return captured readuntil data for validation."""
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "socket"
      and next_command.device_id == self._unique_id
      and next_command.action == "readuntil:" + separator.hex()
    ):
      expected_sep: Optional[bytes]
      if "readuntil:" in next_command.action:
        expected_sep = bytes.fromhex(next_command.action.split("readuntil:")[1])
      else:
        expected_sep = None
      raise ValidationError(
        f"Expected socket readuntil command from {self._unique_id}, "
        f"got {next_command.module} {next_command.action} from {next_command.device_id}"
        f" (expected separator {expected_sep}, got {separator})"
      )
    return bytes.fromhex(next_command.data)

  async def read_until_eof(self, *args, **kwargs) -> bytes:
    """Return captured read_until_eof data for validation."""
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "socket"
      and next_command.device_id == self._unique_id
      and next_command.action == "read_until_eof"
    ):
      raise ValidationError(
        f"Expected socket read_until_eof command from {self._unique_id}, "
        f"got {next_command.module} {next_command.action} from {next_command.device_id}"
      )
    return bytes.fromhex(next_command.data)

  async def stop(self):
    """Mock stop for validation."""
    return

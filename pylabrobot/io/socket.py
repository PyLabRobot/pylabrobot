import logging
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import anyio
import anyio.streams.buffered
import anyio.streams.tls

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

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
    human_readable_device_name: str,
    host: str,
    port: int,
    read_timeout: float = 30,
    write_timeout: float = 30,
    ssl_context: Optional[ssl.SSLContext] = None,
    server_hostname: Optional[str] = None,
  ):
    self._human_readable_device_name = human_readable_device_name
    self._host = host
    self._port = port
    self._stream: Optional[anyio.streams.buffered.BufferedByteStream] = None
    self._read_timeout = read_timeout
    self._write_timeout = write_timeout
    self._ssl_context = ssl_context
    self._server_hostname = server_hostname
    self._unique_id = f"{self._host}:{self._port}"
    self._read_lock = anyio.Lock()
    self._write_lock = anyio.Lock()
    self._ssl = ssl

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new Socket object while capture or validation is active")

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    await super()._enter_lifespan(stack)
    await self._connect()
    stack.push_async_callback(self._disconnect)

  async def _connect(self):
    raw_stream = await anyio.connect_tcp(self._host, self._port)
    stream: Any
    if self._ssl_context:
      stream = await anyio.streams.tls.TLSStream.wrap(
        raw_stream,
        ssl_context=self._ssl_context,
        server_hostname=self._server_hostname,
      )  # type: ignore[call-arg]
    else:
      stream = raw_stream
    self._stream = anyio.streams.buffered.BufferedByteStream(stream)

  async def _disconnect(self):
    async with self._read_lock, self._write_lock:
      if self._stream is None:
        return

      logger.info("Closing connection to socket %s:%s", self._host, self._port)

      try:
        await self._stream.aclose()
      except OSError as e:
        logger.warning("Error while closing socket connection: %s", e)
      finally:
        self._stream = None

  async def reconnect(self, *, wait_time: float = 0):
    await self._disconnect()
    if wait_time > 0:
      await anyio.sleep(wait_time)
    await self._connect()

  def serialize(self):
    return {
      "human_readable_device_name": self._human_readable_device_name,
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
      human_readable_device_name=data["human_readable_device_name"],
      host=data["host"],
      port=data["port"],
      **kwargs,
    )

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Wrapper around anyio.abc.ByteStream.send with lock and io logging.
    Does not retry on timeouts.
    """
    if self._stream is None:
      raise RuntimeError(
        f"Socket for '{self._human_readable_device_name}' not set up; call setup() first"
      )
    timeout = self._write_timeout if timeout is None else timeout
    async with self._write_lock:
      logger.log(LOG_LEVEL_IO, "[%s:%d] write %s", self._host, self._port, data)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="write",
          data=data.hex(),
        )
      )
      try:
        with anyio.fail_after(timeout):
          await self._stream.send(data)
      except TimeoutError as exc:
        logger.error("write timeout: %r", exc)
        raise TimeoutError(f"Timeout while writing to socket after {timeout} seconds") from exc
      except (ConnectionResetError, OSError) as e:
        logger.error("write error: %r", e)
        raise

  async def read(self, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
    """Wrapper around anyio.abc.ByteStream.receive with lock and io logging.

    Args:
      num_bytes: The maximum number of bytes to read from the socket.
        If fewer bytes are available, the method may return less than `num_bytes`.
        If the end of the stream is reached before `num_bytes` bytes are read, only the available bytes are returned.
      timeout: Maximum time to wait for data before raising a timeout.

    Returns:
      The data read from the socket, which may be fewer than `num_bytes` bytes.
    """
    if self._stream is None:
      raise RuntimeError(
        f"Socket for '{self._human_readable_device_name}' not set up; call setup() first"
      )
    timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      try:
        with anyio.fail_after(timeout):
          data = await self._stream.receive(num_bytes)
      except TimeoutError as exc:
        logger.error("read timeout: %r", exc)
        raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
      except anyio.EndOfStream:
        data = b""

      logger.log(LOG_LEVEL_IO, "[%s:%d] read %s", self._host, self._port, data)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="read",
          data=data.hex(),
        )
      )
      return data

  async def readline(self, timeout: Optional[float] = None) -> bytes:
    """Wrapper around reading from stream until newline with lock and io logging."""
    if self._stream is None:
      raise RuntimeError(
        f"Socket for '{self._human_readable_device_name}' not set up; call setup() first"
      )
    timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      try:
        with anyio.fail_after(timeout):
          data = await self._stream.receive_until(b"\n", max_bytes=65536)
          result = data + b"\n"
      except TimeoutError as exc:
        logger.error("readline timeout: %r", exc)
        raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
      except anyio.IncompleteRead:
        logger.warning("readline: connection closed before newline found, returning partial data")
        result = await self._stream.receive(len(self._stream.buffer))
      except anyio.streams.buffered.DelimiterNotFound as exc:
        logger.error("readline error: delimiter not found")
        raise RuntimeError("Newline not found within max_bytes") from exc

      logger.log(LOG_LEVEL_IO, "[%s:%d] readline %s", self._host, self._port, result)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="readline",
          data=result.hex(),
        )
      )
      return result

  async def readuntil(self, separator: bytes = b"\n", timeout: Optional[float] = None) -> bytes:
    """Wrapper around reading from stream until separator with lock and io logging.
    Do not retry on timeouts."""
    if self._stream is None:
      raise RuntimeError(
        f"Socket for '{self._human_readable_device_name}' not set up; call setup() first"
      )
    timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      try:
        with anyio.fail_after(timeout):
          data = await self._stream.receive_until(separator, max_bytes=65536)
          result = data + separator
      except TimeoutError as exc:
        logger.error("readuntil timeout: %r", exc)
        raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
      except anyio.IncompleteRead:
        logger.warning(
          "readuntil: connection closed before separator found, returning partial data"
        )
        result = await self._stream.receive(len(self._stream.buffer))
      except anyio.streams.buffered.DelimiterNotFound as exc:
        logger.error("readuntil error: delimiter not found")
        raise RuntimeError("Separator not found within max_bytes") from exc

      logger.log(LOG_LEVEL_IO, "[%s:%d] readuntil %s", self._host, self._port, result)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="readuntil:" + separator.hex(),
          data=result.hex(),
        )
      )
      return result

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """Read exactly num_bytes, blocking until all bytes are received.

    Args:
      num_bytes: The exact number of bytes to read.
      timeout: Maximum time to wait for data before raising a timeout.
        Note: The timeout is applied per-chunk read operation, not cumulatively
        for the entire read. For small reads (typical use case), this is acceptable.
        For large reads, consider that the total time may exceed the timeout value.

    Returns:
      Exactly num_bytes of data.

    Raises:
      ConnectionError: If the connection is closed before num_bytes are read.
      TimeoutError: If timeout is reached before num_bytes are read.
    """
    if self._stream is None:
      raise RuntimeError(
        f"Socket for '{self._human_readable_device_name}' not set up; call setup() first"
      )
    timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      try:
        with anyio.fail_after(timeout):
          data = await self._stream.receive_exactly(num_bytes)
      except TimeoutError as exc:
        logger.error("read_exact timeout: %r", exc)
        raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
      except anyio.IncompleteRead as exc:
        logger.error("read_exact error: %r", exc)
        raise ConnectionError("Connection closed before num_bytes were read") from exc

      logger.log(LOG_LEVEL_IO, "[%s:%d] read_exact %s", self._host, self._port, data.hex())
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="read_exact",
          data=data.hex(),
        )
      )
      return data

  async def read_until_eof(self, chunk_size: int = 1024, timeout: Optional[float] = None) -> bytes:
    """Read until EOF is reached.
    Do not retry on timeouts.
    """
    buf = bytearray()
    timeout = self._read_timeout if timeout is None else timeout

    async with self._read_lock:
      while True:
        if self._stream is None:
          raise RuntimeError(
            f"Socket for '{self._human_readable_device_name}' not set up; call setup() first"
          )
        try:
          with anyio.fail_after(timeout):
            chunk = await self._stream.receive(chunk_size)
        except TimeoutError as exc:
          # if some previous read attempts already return some data, we should consider this a success
          if len(buf) > 0:
            break
          logger.error("read_until_eof timeout: %r", exc)
          raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
        except anyio.EndOfStream:
          break

        logger.debug("read_until_eof: got %d bytes", len(chunk))
        buf.extend(chunk)

      result = bytes(buf)
      logger.log(LOG_LEVEL_IO, "[%s:%d] read_until_eof %s", self._host, self._port, result)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="read_until_eof",
          data=result.hex(),
        )
      )
      return result


class SocketValidator(Socket):
  """Socket validator for testing/validation purposes."""

  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    host: str,
    port: int,
    read_timeout: float = 30,
    write_timeout: float = 30,
  ):
    super().__init__(
      human_readable_device_name=human_readable_device_name,
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )
    self.cr = cr

  async def setup(self):
    """Mock setup for validation."""

  async def stop(self):
    """Mock stop for validation."""
    return

  async def _connect(self):
    """Mock connect for validation."""

  async def _disconnect(self):
    """Mock disconnect for validation."""

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
    expected = bytes.fromhex(next_command.data)
    if not expected == data:
      align_sequences(expected=expected.decode("latin-1"), actual=data.decode("latin-1"))
      raise ValidationError("Data mismatch: difference was written to stdout.")

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

  async def readuntil(self, separator: bytes = b"\n", *args, **kwargs) -> bytes:
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
        f" (expected separator {expected_sep!r}, got {separator!r})"
      )
    return bytes.fromhex(next_command.data)

  async def read_exact(self, *args, **kwargs) -> bytes:
    """Return captured read_exact data for validation."""
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "socket"
      and next_command.device_id == self._unique_id
      and next_command.action == "read_exact"
    ):
      raise ValidationError(
        f"Expected socket read_exact command from {self._unique_id}, "
        f"got {next_command.module} {next_command.action} from {next_command.device_id}"
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

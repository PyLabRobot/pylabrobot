import asyncio
import logging
import ssl
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union

import anyio
from anyio.abc import ByteStream
from anyio.streams.tls import TLSStream

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase, ReconnectableConnectionIO
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

if TYPE_CHECKING:
  from pylabrobot.io.capture import CaptureReader


logger = logging.getLogger(__name__)


class _UnsetTimeout:
  """Distinguish an omitted compatibility timeout from an explicit value."""


_UNSET_TIMEOUT = _UnsetTimeout()


@dataclass
class SocketCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str, module: str = "socket"):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data


class _SocketBase(IOBase):
  """Shared TCP framing and stream ownership for socket transport contracts."""

  def __init__(
    self,
    human_readable_device_name: str,
    host: str,
    port: int,
    read_timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    write_timeout: float = 30,
    ssl_context: Optional[ssl.SSLContext] = None,
    server_hostname: Optional[str] = None,
    source_ip: Optional[str] = None,
  ):
    super().__init__()
    self.human_readable_device_name = human_readable_device_name
    self._host = host
    self._port = port
    self._stream: Optional[ByteStream] = None
    if isinstance(read_timeout, _UnsetTimeout):
      self._read_timeout: Optional[float] = None
    else:
      warnings.warn(
        "Socket.read_timeout is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      self._read_timeout = read_timeout
    self._write_timeout = write_timeout
    self._ssl_context = ssl_context
    self._server_hostname = server_hostname
    self._source_ip = source_ip
    self._unique_id = f"{self._host}:{self._port}"
    self._read_lock = anyio.Lock()
    self._write_lock = anyio.Lock()
    self._connection_lock = anyio.Lock()
    self._recv_buffer = bytearray()
    self._connection_lost = False

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new Socket object while capture or validation is active")

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Connect and register cancellation-shielded stream cleanup."""
    await super()._enter_lifespan(stack)
    async with self._connection_lock:
      await self._connect()
    stack.push_shielded_async_callback(self._disconnect)

  def _mark_connection_lost(self) -> None:
    """Invalidate this connection after an observed hard disconnect."""
    self._connection_lost = True

  def _require_connection_usable(self) -> None:
    """Reject I/O after an observed hard disconnect until explicit reconnect."""
    if self._connection_lost:
      raise ConnectionError("Socket connection lost; reconnect or enter a new lifespan")

  async def _connect(self) -> None:
    """Connect TCP, optionally enter TLS, then publish the ready stream."""
    raw_stream = await anyio.connect_tcp(
      remote_host=self._host,
      remote_port=self._port,
      local_host=self._source_ip,
    )
    stream: ByteStream = raw_stream
    try:
      if self._ssl_context is not None:
        stream = await TLSStream.wrap(
          raw_stream,
          ssl_context=self._ssl_context,
          hostname=self._server_hostname,
          standard_compatible=True,
        )
    except BaseException:
      with anyio.CancelScope(shield=True):
        await raw_stream.aclose()
      raise

    self._recv_buffer.clear()
    self._stream = stream
    self._connection_lost = False

  async def _disconnect(self) -> None:
    """Wait for active operations, then close and unpublish the stream."""
    async with self._connection_lock:
      await self._disconnect_current()

  async def _disconnect_current(self) -> None:
    """Close the published stream while the caller owns the connection lock."""
    async with self._read_lock, self._write_lock:
      stream = self._stream
      if stream is None:
        return

      logger.info("Closing connection to socket %s:%s", self._host, self._port)

      try:
        await stream.aclose()
      except OSError as e:
        logger.warning("Error while closing socket connection: %s", e)
      finally:
        if self._stream is stream:
          self._stream = None

  def serialize(self):
    return {
      "human_readable_device_name": self.human_readable_device_name,
      "host": self._host,
      "port": self._port,
      "type": "Socket",
      "read_timeout": self._read_timeout,
      "write_timeout": self._write_timeout,
      "source_ip": self._source_ip,
    }

  async def _receive(self, max_bytes: int = 65536) -> bool:
    """Append one receive to the shared buffer before another cancellation point."""
    self._require_connection_usable()
    stream = self._stream
    if stream is None:
      raise RuntimeError(
        f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
      )
    try:
      chunk = await stream.receive(max_bytes)
    except anyio.EndOfStream:
      self._mark_connection_lost()
      return False
    except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
      self._mark_connection_lost()
      raise
    self._recv_buffer.extend(chunk)
    return True

  def _take_buffer(self, num_bytes: int) -> bytes:
    """Remove and return bytes after a public operation has completed."""
    data = bytes(self._recv_buffer[:num_bytes])
    del self._recv_buffer[:num_bytes]
    return data

  async def _read_to_separator(self, separator: bytes, incomplete_is_error: bool) -> bytes:
    """Fill the shared buffer through a separator and consume the completed frame."""
    while True:
      separator_index = self._recv_buffer.find(separator)
      if separator_index >= 0:
        return self._take_buffer(separator_index + len(separator))
      if not await self._receive():
        partial = self._take_buffer(len(self._recv_buffer))
        if incomplete_is_error:
          raise asyncio.IncompleteReadError(partial=partial, expected=None)
        return partial

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Wrapper around a byte stream send with lock and IO logging.
    Does not retry on timeouts.
    """
    timeout = self._write_timeout if timeout is None else timeout
    async with self._write_lock:
      self._require_connection_usable()
      stream = self._stream
      if stream is None:
        raise RuntimeError(
          f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
        )
      await anyio.lowlevel.checkpoint()
      logger.log(LOG_LEVEL_IO, "[%s:%d] write %s", self._host, self._port, data)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="write",
          data=data.hex(),
        )
      )
      try:
        with anyio.CancelScope(shield=True):
          with anyio.fail_after(timeout):
            await stream.send(data)
        return
      except TimeoutError as exc:
        self._mark_connection_lost()
        logger.error("write timeout: %r", exc)
        raise TimeoutError(f"Timeout while writing to socket after {timeout} seconds") from exc
      except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError) as e:
        self._mark_connection_lost()
        logger.error("write error: %r", e)
        raise

  async def read(
    self, num_bytes: int = 128, timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT
  ) -> bytes:
    """Read up to a requested number of bytes with lock and IO logging.

    Args:
      num_bytes: The maximum number of bytes to read from the socket.
        If fewer bytes are available, the method may return less than `num_bytes`.
        EOF raises ``anyio.EndOfStream`` when no buffered data remains.
      timeout: Deprecated maximum wait for data. Omit for no public deadline.

    Returns:
      The data read from the socket, which may be fewer than `num_bytes` bytes.
    """
    if num_bytes <= 0:
      raise ValueError("num_bytes must be positive")
    if isinstance(timeout, _UnsetTimeout):
      timeout = self._read_timeout
    else:
      warnings.warn(
        "Socket.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      if timeout is None:
        timeout = self._read_timeout
    async with self._read_lock:
      self._require_connection_usable()
      if self._stream is None:
        raise RuntimeError(
          f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
        )
      try:
        with anyio.fail_after(timeout):
          if not self._recv_buffer and not await self._receive(num_bytes):
            raise anyio.EndOfStream
          data = self._take_buffer(min(num_bytes, len(self._recv_buffer)))
      except TimeoutError as exc:
        logger.error("read timeout: %r", exc)
        raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
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
    """Read through the next newline, or return remaining bytes at EOF."""
    timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      self._require_connection_usable()
      if self._stream is None:
        raise RuntimeError(
          f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
        )
      try:
        with anyio.fail_after(timeout):
          data = await self._read_to_separator(b"\n", incomplete_is_error=False)
      except TimeoutError as exc:
        logger.error("readline timeout: %r", exc)
        raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
      logger.log(LOG_LEVEL_IO, "[%s:%d] readline %s", self._host, self._port, data)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="readline",
          data=data.hex(),
        )
      )
      return data

  async def readuntil(self, separator: bytes = b"\n", timeout: Optional[float] = None) -> bytes:
    """Read through a separator with lock and IO logging.
    Do not retry on timeouts."""
    if not separator:
      raise ValueError("Separator should be at least one-byte string")
    timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      self._require_connection_usable()
      if self._stream is None:
        raise RuntimeError(
          f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
        )
      try:
        with anyio.fail_after(timeout):
          data = await self._read_to_separator(separator, incomplete_is_error=True)
      except TimeoutError as exc:
        logger.error("readuntil timeout: %r", exc)
        raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
      logger.log(LOG_LEVEL_IO, "[%s:%d] readuntil %s", self._host, self._port, data)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="readuntil:" + separator.hex(),
          data=data.hex(),
        )
      )
      return data

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
    timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      self._require_connection_usable()
      if self._stream is None:
        raise RuntimeError(
          f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
        )
      if num_bytes <= 0:
        result = b""
      else:
        while len(self._recv_buffer) < num_bytes:
          try:
            with anyio.fail_after(timeout):
              received = await self._receive(num_bytes - len(self._recv_buffer))
          except TimeoutError as exc:
            logger.error("read_exact timeout: %r", exc)
            raise TimeoutError(
              f"Timeout while reading from socket after {timeout} seconds"
            ) from exc
          if not received:
            raise ConnectionError("Connection closed before num_bytes are read")

        result = self._take_buffer(num_bytes)
      logger.log(LOG_LEVEL_IO, "[%s:%d] read_exact %s", self._host, self._port, result.hex())
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="read_exact",
          data=result.hex(),
        )
      )
      return result

  async def read_until_eof(self, chunk_size: int = 1024, timeout: Optional[float] = None) -> bytes:
    """Read until EOF is reached.
    Do not retry on timeouts.
    """
    timeout = self._read_timeout if timeout is None else timeout

    async with self._read_lock:
      self._require_connection_usable()
      if self._stream is None:
        raise RuntimeError(
          f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
        )
      while True:
        try:
          with anyio.fail_after(timeout):
            received = await self._receive(chunk_size)
        except TimeoutError as exc:
          # if some previous read attempts already return some data, we should consider this a success
          if self._recv_buffer:
            break
          logger.error("read_until_eof timeout: %r", exc)
          raise TimeoutError(f"Timeout while reading from socket after {timeout} seconds") from exc
        if not received:
          break

        logger.debug("read_until_eof: buffer has %d bytes", len(self._recv_buffer))

      line = self._take_buffer(len(self._recv_buffer))
      logger.log(LOG_LEVEL_IO, "[%s:%d] read_until_eof %s", self._host, self._port, line)
      capturer.record(
        SocketCommand(
          device_id=self._unique_id,
          action="read_until_eof",
          data=line.hex(),
        )
      )
      return line


class Socket(_SocketBase, ReconnectableConnectionIO):
  """TCP transport with a stable stream between explicit reconnect calls."""

  async def reconnect(self) -> None:
    """Close the current stream before connecting and publishing its replacement."""
    async with self._connection_lock:
      if not self.is_active:
        raise RuntimeError(
          f"Socket for '{self.human_readable_device_name}' not set up; call setup() first"
        )
      self._connection_lost = True
      await self._disconnect_current()
      await self._connect()


class SocketValidator(Socket):
  """Socket validator for testing/validation purposes."""

  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    host: str,
    port: int,
    read_timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT,
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

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Enter validation without opening a network connection."""
    self._connection_lost = False

  async def _connect(self) -> None:
    """Skip network connection during validation."""

  async def _disconnect(self) -> None:
    """Skip network cleanup during validation."""

  async def reconnect(self) -> None:
    """Skip network reconnection during validation."""
    if not self.is_active:
      raise RuntimeError("Socket reconnect requires an active lifespan")
    self._connection_lost = False

  async def write(self, data: bytes, *args, **kwargs):
    """Validate write command against captured data."""
    self._require_connection_usable()
    await anyio.lowlevel.checkpoint()
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

  async def read(
    self, num_bytes: int = 128, timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT
  ) -> bytes:
    """Return captured read data for validation."""
    if num_bytes <= 0:
      raise ValueError("num_bytes must be positive")
    if not isinstance(timeout, _UnsetTimeout):
      warnings.warn(
        "Socket.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
    self._require_connection_usable()
    await anyio.lowlevel.checkpoint()
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
    data = bytes.fromhex(next_command.data)
    if not data:
      self._mark_connection_lost()
      raise anyio.EndOfStream
    if len(data) > num_bytes:
      raise ValidationError(f"Captured socket read exceeds requested size {num_bytes}")
    return data

  async def readline(self, *args, **kwargs) -> bytes:
    """Return captured readline data for validation."""
    self._require_connection_usable()
    await anyio.lowlevel.checkpoint()
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
    self._require_connection_usable()
    await anyio.lowlevel.checkpoint()
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
    self._require_connection_usable()
    await anyio.lowlevel.checkpoint()
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
    self._require_connection_usable()
    await anyio.lowlevel.checkpoint()
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
